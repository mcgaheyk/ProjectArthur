"""Spike logger for PETLIBRO: record what the Granary (PLAF103) and a fountain (PLWF105/106)
report through PETLIBRO's cloud, every few minutes, for a few days. Strictly read only.

Its purpose is discovery: confirm the real response shapes (docs/reference/petlibro.md was
read from a third-party integration, never checked live) and see how the food-low flag and the
water level behave over time. The exit `fields` lines are the inventory it exists to produce.

The pure parts (redaction, scrubbing, flattening, labels, key values, timezone detection,
options) are at the top and are unit-tested. The HTTP client and the run loop are at the
bottom; importing this module never touches the network.

Run it with:  uv run --env-file .env python scripts/spike_petlibro.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from arthur.spikelog import (
    _REDACT_KEY,
    _REDACT_VALUE,
    EXIT_AUTH,
    EXIT_MISSING_ENV,
    EXIT_OK,
    REDACTED,
    Backoff,
    DailyJsonl,
    _sleep_or_stop,
    _Stopped,
    _until_stop,
    diff_records,
    format_duration,
    scrub,
    to_jsonable,
    utc_iso_ms,
)

__all__ = ["EXIT_AUTH", "EXIT_MISSING_ENV", "EXIT_OK", "main"]

# --------------------------------------------------------------------------------------------
# Constants (docs/reference/petlibro.md)
# --------------------------------------------------------------------------------------------

BASE_URL = "https://api.us.petlibro.com"
APP_ID = 1
APP_SN = "c35772530d1041699c87fe62348507a8"  # a constant of the app, not a secret of ours
APP_VERSION = "1.3.45"
REQUEST_TIMEOUT = 30.0
REQUEST_GAP = 1.0  # seconds between calls, to space requests out a little
MAX_PASSWORD_LENGTH = 18

DEFAULT_POLL_SECONDS = 300
MIN_POLL_SECONDS = 60
RELIST_SECONDS = 60 * 60
SLOW_SECONDS = 6 * 60 * 60
WORK_RECORD_DAYS = 7
# An endpoint that answers a device with a non-zero code this many times in a row is not
# called for that device again in this run (it probably does not apply to that product).
DROP_AFTER_API_ERRORS = 3
# The core state call is never dropped: a code that only lasts while a device is offline must
# not blind the rest of the run.
NEVER_DROPPED: frozenset[str] = frozenset({"realInfo"})

CODE_OK = 0
CODE_NOT_YET_LOGIN = 1009
CODE_MEMBER_NOT_EXIST = 1101
CODE_ILLEGAL_PASSWORD = 1102

LOGIN_PATH = "/member/auth/login"
LIST_PATH = "/device/device/list"
BASE_INFO_PATH = "/device/device/baseInfo"
REAL_INFO_PATH = "/device/device/realInfo"
DATA_REAL_INFO_PATH = "/data/data/realInfo"
GRAIN_STATUS_PATH = "/device/data/grainStatus"
DRINK_DATA_PATH = "/data/deviceDrinkWater/todayDrinkData"
WORK_RECORD_PATH = "/device/workRecord/list"

# The hard allowlist. Nothing that feeds, changes a setting or logs out is on it.
READ_PATHS: frozenset[str] = frozenset(
    {
        LIST_PATH,
        BASE_INFO_PATH,
        REAL_INFO_PATH,
        DATA_REAL_INFO_PATH,
        GRAIN_STATUS_PATH,
        DRINK_DATA_PATH,
        WORK_RECORD_PATH,
    }
)
ALLOWED_PATHS: frozenset[str] = READ_PATHS | {LOGIN_PATH}

# Short endpoint names used in the log (`raw`, `changed`, `fields`).
SHORT_NAMES: dict[str, str] = {
    LIST_PATH: "deviceList",
    BASE_INFO_PATH: "baseInfo",
    REAL_INFO_PATH: "realInfo",
    DATA_REAL_INFO_PATH: "dataRealInfo",
    GRAIN_STATUS_PATH: "grainStatus",
    DRINK_DATA_PATH: "drinkData",
    WORK_RECORD_PATH: "workRecord",
}

# Values shown on the console and in each state line's `key`, looked up in this endpoint order.
KEY_FIELDS: tuple[str, ...] = (
    "surplusGrain",
    "grainOutletState",
    "runningState",
    "batteryState",
    "weightPercent",
    "weight",
    "weightState",
    "waterBoxStatus",
    "todayFeedingTimes",
    "todayFeedingQuantity",
    "todayTotalMl",
    "todayTotalTimes",
)
KEY_ENDPOINT_ORDER: tuple[str, ...] = ("realInfo", "dataRealInfo", "grainStatus", "drinkData")

# --------------------------------------------------------------------------------------------
# Redaction and scrubbing
# --------------------------------------------------------------------------------------------

KEEP_KEYS: frozenset[str] = frozenset(
    {"productName", "productIdentifier", "jobName", "templateName"}
)
_SN_KEY = re.compile(r"(Sn|sn|SN)$")
_EXTRA_KEY = re.compile(
    r"(auth|secret|rfid|avatar|url|nick|address|longitude|latitude)", re.IGNORECASE
)


def is_secret_key(key: str) -> bool:
    """True for keys whose values must never be logged (serials, MACs, names, ids...)."""
    if key in KEEP_KEYS:
        return False
    return bool(_REDACT_KEY.search(key) or _SN_KEY.search(key) or _EXTRA_KEY.search(key))


def redact_petlibro(data: Any) -> Any:
    """Redact identifying values by key (PETLIBRO rules) and emails/IPs/MACs by value."""
    if isinstance(data, Mapping):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if is_secret_key(str(key)) and value not in (None, ""):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = redact_petlibro(value)
        return out
    if isinstance(data, (list, tuple)):
        return [redact_petlibro(v) for v in data]
    if isinstance(data, str) and _REDACT_VALUE.search(data):
        return REDACTED
    return to_jsonable(data)


MIN_SECRET_LENGTH = 4


class Scrubber:
    """Replaces known secret strings (email, password, its hash, the token, serials, MACs,
    device names) wherever they appear, case-insensitively, plus the generic token patterns of
    `spikelog.scrub`. Applied to everything written or printed."""

    def __init__(self, secrets: Iterable[Any] = ()) -> None:
        self._secrets: set[str] = set()
        self._pattern: re.Pattern[str] | None = None
        self.add(*secrets)

    def add(self, *values: Any) -> None:
        changed = False
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            text = str(value).strip()
            variants = {text}
            if re.fullmatch(r"[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}", text):
                variants.add(re.sub(r"[:-]", "", text))  # a MAC without separators
            for v in variants:
                if len(v) >= MIN_SECRET_LENGTH and v not in self._secrets:
                    self._secrets.add(v)
                    changed = True
        if changed:
            ordered = sorted(self._secrets, key=len, reverse=True)
            self._pattern = re.compile("|".join(re.escape(s) for s in ordered), re.IGNORECASE)

    def text(self, value: str) -> str:
        if self._pattern is not None:
            value = self._pattern.sub(REDACTED, value)
        return scrub(value)

    def obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {self.text(str(k)): self.obj(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.obj(v) for v in value]
        return value


# --------------------------------------------------------------------------------------------
# Flattening, field inventory, key values
# --------------------------------------------------------------------------------------------


def flatten(data: Any, prefix: str) -> dict[str, Any]:
    """{dotted.path: leaf} with list items as `name[i]`. Empty dicts and lists are leaves."""
    out: dict[str, Any] = {}
    if isinstance(data, Mapping):
        if not data:
            out[prefix] = {}
        for key, value in data.items():
            out.update(flatten(value, f"{prefix}.{key}"))
    elif isinstance(data, (list, tuple)):
        if not data:
            out[prefix] = []
        for i, value in enumerate(data):
            out.update(flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = data
    return out


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


_INDEX = re.compile(r"\[\d+\]")


def add_field_types(inventory: dict[str, dict[str, set[str]]], short: str, data: Any) -> None:
    """Record every dotted field under `short` with the JSON type seen (list indices as [])."""
    fields = inventory.setdefault(short, {})
    for name, value in flatten(data, short).items():
        name = _INDEX.sub("[]", name)[len(short) :].removeprefix(".") or "(data)"
        fields.setdefault(name, set()).add(json_type(value))


def inventory_json(inventory: Mapping[str, Mapping[str, set[str]]]) -> dict[str, Any]:
    return {
        short: {name: sorted(types) for name, types in sorted(fields.items())}
        for short, fields in sorted(inventory.items())
    }


def extract_key(payloads: Mapping[str, Any]) -> dict[str, Any]:
    """The key values present in this poll's payloads (first endpoint in order wins)."""
    key: dict[str, Any] = {}
    for name in KEY_FIELDS:
        for short in KEY_ENDPOINT_ORDER:
            data = payloads.get(short)
            if isinstance(data, Mapping) and name in data:
                key[name] = to_jsonable(data[name])
                break
    return key


def flat_record(payloads: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per endpoint, the flattened payload keyed like `realInfo.weightPercent`."""
    return {short: flatten(data, short) for short, data in payloads.items()}


# --------------------------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------------------------


def endpoints_for(product: str) -> tuple[str, ...]:
    """Paths polled every cycle for a device with this productIdentifier."""
    paths = [REAL_INFO_PATH, DATA_REAL_INFO_PATH]
    if product.startswith("PLAF"):
        paths.append(GRAIN_STATUS_PATH)
    elif product.startswith("PLWF"):
        paths.append(DRINK_DATA_PATH)
    return tuple(paths)


def slow_endpoints_for(product: str) -> tuple[str, ...]:
    """Paths fetched at start and every SLOW_SECONDS."""
    if product.startswith("PLAF"):
        return (BASE_INFO_PATH, WORK_RECORD_PATH)
    return (BASE_INFO_PATH,)


def assign_labels(
    devices: Iterable[Mapping[str, Any]], existing: Mapping[str, str] | None = None
) -> dict[str, str]:
    """{deviceSn: "<productIdentifier>#<n>"}, n counted per product in list order. Devices
    already labelled keep their label; new ones take the next free number."""
    labels = dict(existing or {})
    for device in devices:
        sn = device.get("deviceSn")
        if not sn or str(sn) in labels:
            continue
        product = str(device.get("productIdentifier") or "UNKNOWN")
        taken = {lbl for lbl in labels.values() if lbl.startswith(product + "#")}
        n = 1
        while f"{product}#{n}" in taken:
            n += 1
        labels[str(sn)] = f"{product}#{n}"
    return labels


def device_body(sn: str) -> dict[str, Any]:
    return {"id": sn, "deviceSn": sn}


def work_record_body(sn: str, now: datetime, days: int = WORK_RECORD_DAYS) -> dict[str, Any]:
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    return {
        "deviceSn": sn,
        "startTime": start_ms,
        "endTime": end_ms,
        "size": 25,
        "type": ["GRAIN_OUTPUT_SUCCESS"],
    }


# --------------------------------------------------------------------------------------------
# Login pieces
# --------------------------------------------------------------------------------------------


def md5_hex(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()  # the API sends an MD5 of the password


def login_body(email: str, password_md5: str, timezone: str) -> dict[str, Any]:
    return {
        "appId": APP_ID,
        "appSn": APP_SN,
        "country": "US",
        "email": email,
        "password": password_md5,
        "phoneBrand": "",
        "phoneSystemVersion": "",
        "timezone": timezone,
        "thirdId": None,
        "type": None,
    }


def request_headers(timezone: str, token: str | None = None) -> dict[str, str]:
    headers = {
        "source": "ANDROID",
        "language": "EN",
        "timezone": timezone,
        "version": APP_VERSION,
    }
    if token:
        headers["token"] = token
    return headers


def auth_hint(code: int | None, msg: str | None) -> str:
    if code == CODE_ILLEGAL_PASSWORD:
        text = (
            "PETLIBRO rejected the password (code 1102). Check PETLIBRO_PASSWORD in .env. "
            "PETLIBRO also gives this code when the password is longer than 18 characters; "
            "if it is, change it to a shorter one in the phone app."
        )
    elif code == CODE_MEMBER_NOT_EXIST:
        text = (
            "PETLIBRO says this account does not exist on its app API (code 1101). Accounts "
            "made on the website, PETLIBRO Lite accounts and non-US accounts do not work here. "
            "Create the second account in the PETLIBRO phone app."
        )
    else:
        text = f"PETLIBRO rejected the login (code {code}: {msg or 'no message'})."
    return text + "\nNot retrying."


# --------------------------------------------------------------------------------------------
# Timezone, environment, options
# --------------------------------------------------------------------------------------------


class TimezoneError(Exception):
    """No usable IANA timezone could be found."""


def _valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def resolve_timezone(
    environ: Mapping[str, str], localtime: Path = Path("/etc/localtime")
) -> str:
    """IANA zone from PETLIBRO_TIMEZONE, then TZ (when it looks like Area/City), then the
    /etc/localtime symlink target. Raises TimezoneError rather than guessing."""
    explicit = (environ.get("PETLIBRO_TIMEZONE") or "").strip()
    if explicit:
        if _valid_zone(explicit):
            return explicit
        raise TimezoneError(f"PETLIBRO_TIMEZONE={explicit!r} is not a known IANA timezone.")
    tz = (environ.get("TZ") or "").strip().lstrip(":")
    if "/" in tz and not tz.startswith("/") and _valid_zone(tz):
        return tz
    try:
        target = os.readlink(localtime)
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        name = target.split("zoneinfo/", 1)[1]
        if _valid_zone(name):
            return name
    raise TimezoneError("Could not detect the local timezone.")


TIMEZONE_MESSAGE = """\
{reason}

Set PETLIBRO_TIMEZONE in .env to your IANA timezone, for example:
  PETLIBRO_TIMEZONE=America/Chicago
"""

MISSING_ENV_MESSAGE = """\
PETLIBRO credentials are not set ({missing}).

Use a SECOND PETLIBRO account: logging in here logs that account out of the phone app.
  1. In the PETLIBRO phone app, create a new account (not on the website; password of
     18 characters or fewer).
  2. From your own account, share the Granary (and later the fountain) with it, and accept
     the share while signed in as the new account.
  3. Add PETLIBRO_EMAIL and PETLIBRO_PASSWORD to .env with the new account's login (the
     lines to copy are in .env.example; .env is gitignored).
  4. Run:  uv run --env-file .env python scripts/spike_petlibro.py
"""

PHONE_LOGOUT_NOTICE = (
    "Note: logging in here logs this PETLIBRO account out of the phone app. "
    "It should be a second account with the devices shared to it."
)


def read_credentials(environ: Mapping[str, str]) -> tuple[str, str] | list[str]:
    """Return (email, password), or the list of missing variable names."""
    names = ("PETLIBRO_EMAIL", "PETLIBRO_PASSWORD")
    missing = [n for n in names if not (environ.get(n) or "").strip()]
    if missing:
        return missing
    return environ["PETLIBRO_EMAIL"].strip(), environ["PETLIBRO_PASSWORD"]


def clamp_poll_seconds(value: float) -> tuple[float, bool]:
    """Enforce the minimum poll interval. Returns (seconds, was_clamped)."""
    if value < MIN_POLL_SECONDS:
        return float(MIN_POLL_SECONDS), True
    return float(value), False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spike_petlibro.py",
        description=(
            "Log what your PETLIBRO Granary and fountain report through PETLIBRO's cloud, "
            "read only (JSON lines in the log directory; a console line when a key value "
            "changes)."
        ),
        epilog=(
            "example:\n"
            "  uv run --env-file .env python scripts/spike_petlibro.py\n"
            "  uv run --env-file .env python scripts/spike_petlibro.py --duration 3600\n\n"
            "Needs PETLIBRO_EMAIL and PETLIBRO_PASSWORD in the environment (copy .env.example "
            "to .env),\nfor a second PETLIBRO account with the devices shared to it. "
            "PETLIBRO_TIMEZONE is optional.\nExit codes: 0 ok, 2 settings missing, "
            "3 login rejected."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help="stop after this many seconds (default: run until Ctrl-C)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        metavar="SECONDS",
        help=f"poll interval, minimum {MIN_POLL_SECONDS} (default {DEFAULT_POLL_SECONDS})",
    )
    parser.add_argument(
        "--log-dir", type=Path, default=Path("logs"), help="where to write (default: logs)"
    )
    return parser


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class PetlibroError(Exception):
    def __init__(self, message: str, code: int | None = None, msg: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.msg = msg


class ForbiddenPath(PetlibroError):
    """A path not on the read-only allowlist. Raised before any request is made."""


class PetlibroAuthFailed(PetlibroError):
    """Login rejected (non-zero code on login). Never retried."""


class NotLoggedIn(PetlibroError):
    """Code 1009 on a data call: the token is no longer valid."""


class SessionReplaced(PetlibroError):
    """1009 again right after a fresh login: another login (a phone?) took the session."""


class ApiError(PetlibroError):
    """Any other non-zero code."""


class HttpError(PetlibroError):
    """Non-200 HTTP status, or a timeout."""


class BadResponse(PetlibroError):
    """Invalid JSON or an unexpected envelope."""


SESSION_REPLACED_MESSAGE = (
    "the session was replaced: PETLIBRO rejected the token again right after a fresh login, "
    "probably because the same account is logged in on a phone. Use a second account. "
    "Not logging in again until the next poll."
)


# --------------------------------------------------------------------------------------------
# The HTTP client (read only)
# --------------------------------------------------------------------------------------------


class PetlibroClient:
    """Read-only PETLIBRO client. Every path is checked against ALLOWED_PATHS first.

    1009 on a data call triggers one fresh login and one retry, at most once per poll cycle
    (`begin_cycle`). A second 1009 in the same cycle raises SessionReplaced instead of logging
    in again, so two logins on one account can never fight in a loop.
    """

    def __init__(
        self,
        email: str,
        password: str,
        timezone: str,
        *,
        base_url: str = BASE_URL,
        scrubber: Scrubber | None = None,
        timeout: float = REQUEST_TIMEOUT,
        on_relogin: Callable[[], None] | None = None,
    ) -> None:
        self.email = email
        self._password_md5 = md5_hex(password)
        self.timezone = timezone
        self.base_url = base_url.rstrip("/")
        self.scrubber = scrubber or Scrubber()
        self.scrubber.add(email, password, self._password_md5)
        self.timeout = timeout
        self.on_relogin = on_relogin
        self.token: str | None = None
        self.logins = 0
        self._relogin = "available"  # per poll cycle: available, used or failed
        self._session: Any = None

    def begin_cycle(self) -> None:
        self._relogin = "available"

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> Any:
        if self._session is None:
            import aiohttp

            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    async def _post(self, path: str, body: Mapping[str, Any]) -> Any:
        if path not in ALLOWED_PATHS:
            raise ForbiddenPath(f"refusing to call {path!r}: not on the read-only allowlist")
        is_login = path == LOGIN_PATH
        headers = request_headers(self.timezone, None if is_login else self.token)
        session = self._get_session()
        try:
            async with session.post(self.base_url + path, json=dict(body), headers=headers) as r:
                status = r.status
                text = await r.text()
        except TimeoutError:
            raise HttpError(f"no response within {self.timeout:.0f} s") from None
        if status != 200:
            raise HttpError(f"HTTP {status}")
        try:
            envelope = json.loads(text)
        except ValueError:
            raise BadResponse("response is not valid JSON") from None
        if not isinstance(envelope, Mapping) or not isinstance(envelope.get("code"), int):
            raise BadResponse("response has no integer 'code'")
        code = envelope["code"]
        msg = envelope.get("msg")
        msg = None if msg is None else str(msg)
        if code == CODE_OK:
            return envelope.get("data")
        if is_login:
            raise PetlibroAuthFailed(f"login rejected (code {code}: {msg})", code=code, msg=msg)
        if code == CODE_NOT_YET_LOGIN:
            raise NotLoggedIn(f"not logged in (code {code}: {msg})", code=code, msg=msg)
        raise ApiError(f"code {code}: {msg}", code=code, msg=msg)

    async def login(self) -> None:
        self.logins += 1
        body = login_body(self.email, self._password_md5, self.timezone)
        data = await self._post(LOGIN_PATH, body)
        token = data.get("token") if isinstance(data, Mapping) else None
        if not token or not isinstance(token, str):
            raise BadResponse("login response has no token")
        self.scrubber.add(token)
        self.token = token

    async def call(self, path: str, body: Mapping[str, Any]) -> Any:
        if path not in READ_PATHS:
            raise ForbiddenPath(f"refusing to call {path!r}: not on the read-only allowlist")
        if self.token is None:
            await self.login()
        try:
            return await self._post(path, body)
        except NotLoggedIn:
            if self._relogin == "used":
                raise SessionReplaced(SESSION_REPLACED_MESSAGE) from None
            if self._relogin == "failed":
                raise  # the re-login this cycle failed (network); an error for this call
        self._relogin = "failed"
        await self.login()
        self._relogin = "used"
        if self.on_relogin is not None:
            self.on_relogin()
        try:
            return await self._post(path, body)
        except NotLoggedIn:
            raise SessionReplaced(SESSION_REPLACED_MESSAGE) from None


# --------------------------------------------------------------------------------------------
# The spike
# --------------------------------------------------------------------------------------------


@dataclass
class DeviceState:
    sn: str
    label: str
    product: str
    product_name: str | None = None
    listed_online: Any = None
    polls: int = 0
    errors: int = 0
    last_flat: dict[str, dict[str, Any]] | None = None
    key: dict[str, Any] = field(default_factory=dict)
    key_changes: dict[str, int] = field(default_factory=dict)
    weight_min: float | None = None
    weight_max: float | None = None
    inventory: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    next_slow: float = 0.0
    api_errors: dict[str, int] = field(default_factory=dict)  # consecutive, per endpoint
    dropped: dict[str, int | None] = field(default_factory=dict)  # endpoint -> last code


class PetlibroSpike:
    """Polls through a PetlibroClient and turns responses into log and console lines."""

    def __init__(
        self,
        client: PetlibroClient,
        log_dir: Path,
        timezone: str,
        *,
        out: TextIO | None = None,
        err: TextIO | None = None,
        request_gap: float = REQUEST_GAP,
        relist_seconds: float = RELIST_SECONDS,
        slow_seconds: float = SLOW_SECONDS,
        wall: Callable[[], datetime] | None = None,
        mono: Callable[[], float] | None = None,
    ) -> None:
        self.client = client
        self.scrubber = client.scrubber
        self.log = DailyJsonl(log_dir, "petlibro")
        self.tz = ZoneInfo(timezone)
        self._out = out
        self._err = err
        self.request_gap = request_gap
        self.relist_seconds = relist_seconds
        self.slow_seconds = slow_seconds
        self._wall = wall or (lambda: datetime.now(UTC))
        self._mono = mono or time.monotonic
        self._t0 = self._mono()
        self.devices: dict[str, DeviceState] = {}
        self.labels: dict[str, str] = {}
        self._next_relist: float | None = None
        self._listed_once = False
        self._said_empty = False
        self._pending_errors: list[dict[str, Any]] = []
        self.cycle_errors = 0
        client.on_relogin = self._on_relogin

    # -- output ----------------------------------------------------------------------------

    def elapsed(self) -> float:
        return self._mono() - self._t0

    def say(self, text: str, *, error: bool = False) -> None:
        stream = (self._err or sys.stderr) if error else (self._out or sys.stdout)
        print(self.scrubber.text(text), file=stream, flush=True)

    def write(self, line: Mapping[str, Any]) -> None:
        self.log.write(self.scrubber.obj(to_jsonable(line)))

    def _base(self, kind: str) -> dict[str, Any]:
        return {"ts": utc_iso_ms(self._wall()), "t": round(self.elapsed(), 3), "kind": kind}

    def log_info(self, event: str, **extra: Any) -> None:
        self.write({**self._base("info"), "event": event, **extra})

    def _on_relogin(self) -> None:
        self.log_info("relogin")
        self.say(f"{self._stamp()}  logged in again (the token had expired)")

    def error(
        self,
        where: str,
        exc: BaseException | str,
        device: str | None = None,
        *,
        backoff: bool = True,
    ) -> None:
        """Queue an error line; flush_errors writes it with the delay before the next poll.
        Only errors with `backoff` (transport trouble) slow the polling down."""
        message = str(exc) if isinstance(exc, BaseException) else exc
        error_type = type(exc).__name__ if isinstance(exc, BaseException) else "Error"
        line = {**self._base("error"), "where": where}
        if device is not None:
            line["device"] = device
        line.update({"error": error_type, "message": self.scrubber.text(message)})
        self._pending_errors.append(line)
        if backoff:
            self.cycle_errors += 1

    def flush_errors(self, retry_in: float | None) -> None:
        pending, self._pending_errors = self._pending_errors, []
        for line in pending:
            line["retry_in"] = retry_in
            with contextlib.suppress(OSError):
                self.write(line)
            where = line["where"] + (f" {line['device']}" if "device" in line else "")
            retry = f"; next try in {format_duration(retry_in)}" if retry_in else ""
            self.say(f"error ({where}): {line['error']}: {line['message']}{retry}", error=True)

    def _stamp(self) -> str:
        return self._wall().astimezone(self.tz).strftime("%H:%M:%S")

    # -- devices ---------------------------------------------------------------------------

    async def _gap(self) -> None:
        if self.request_gap > 0:
            await asyncio.sleep(self.request_gap)

    async def relist(self) -> None:
        data = await self.client.call(LIST_PATH, {})
        if not isinstance(data, list):
            raise BadResponse("device list is not a list")
        listed = [d for d in data if isinstance(d, Mapping) and d.get("deviceSn")]
        for d in listed:
            # Not the device name: it is often the product name, and the `name` key is
            # redacted anyway.
            self.scrubber.add(d.get("deviceSn"), d.get("mac"))
        self.labels = assign_labels(listed, self.labels)
        before = set(self.devices)
        current: dict[str, DeviceState] = {}
        for d in listed:
            sn = str(d["deviceSn"])
            state = self.devices.get(sn) or DeviceState(
                sn=sn, label=self.labels[sn], product=str(d.get("productIdentifier") or "")
            )
            state.product_name = d.get("productName")
            state.listed_online = d.get("online")
            add_field_types(state.inventory, "deviceList", d)
            current[sn] = state
        self.devices = current
        self._next_relist = self.elapsed() + self.relist_seconds
        summary = [
            {
                "device": s.label,
                "productName": d.get("productName"),
                "productIdentifier": d.get("productIdentifier"),
                "softwareVersion": d.get("softwareVersion"),
                "hardwareVersion": d.get("hardwareVersion"),
                "online": d.get("online"),
                "deviceShareState": d.get("deviceShareState"),
            }
            for d in listed
            for s in [current[str(d["deviceSn"])]]
        ]
        if not self._listed_once:
            self._listed_once = True
            self.log_info("connected", devices=len(current), list=summary)
        elif set(current) != before:
            self.log_info("devices_changed", devices=len(current), list=summary)
        if not current:
            if not self._said_empty:
                self._said_empty = True
                self.say(
                    "No devices on this PETLIBRO account yet. Probably they are not shared to "
                    "it, or the share has not been accepted in the app while signed in as "
                    "this account. Checking again every poll."
                )
                self.log_info("no_devices")
        else:
            if self._said_empty or set(current) != before:
                labels = ", ".join(
                    f"{s.label} ({s.product_name})" for s in current.values()
                )
                self.say(f"{self._stamp()}  devices: {labels}")
            self._said_empty = False

    async def _fetch(self, state: DeviceState, path: str) -> tuple[bool, Any]:
        body = (
            work_record_body(state.sn, self._wall())
            if path == WORK_RECORD_PATH
            else device_body(state.sn)
        )
        short = SHORT_NAMES[path]
        try:
            data = await self.client.call(path, body)
        except (SessionReplaced, PetlibroAuthFailed, ForbiddenPath):
            raise
        except ApiError as exc:
            # The server answered properly, just not with data: no backoff for this.
            state.errors += 1
            self.error(short, exc, device=state.label, backoff=False)
            self._count_api_error(state, short, exc)
            return False, None
        except Exception as exc:  # noqa: BLE001 - one call's failure must not stop the rest
            state.errors += 1
            self.error(short, exc, device=state.label)
            return False, None
        finally:
            await self._gap()
        state.api_errors[short] = 0
        return True, data

    def _count_api_error(self, state: DeviceState, short: str, exc: ApiError) -> None:
        count = state.api_errors.get(short, 0) + 1
        state.api_errors[short] = count
        if short in NEVER_DROPPED or count < DROP_AFTER_API_ERRORS:
            return
        state.dropped[short] = exc.code
        msg = self.scrubber.text(exc.msg) if exc.msg is not None else None
        self.log_info(
            "endpoint_dropped", device=state.label, endpoint=short, code=exc.code, msg=msg
        )
        self.say(
            f"{self._stamp()}  {state.label}  {short} answered code {exc.code} "
            f"{count} times in a row; not calling it again for this device"
        )

    def _active(self, state: DeviceState, paths: Iterable[str]) -> list[str]:
        return [p for p in paths if SHORT_NAMES[p] not in state.dropped]

    async def poll_device(self, state: DeviceState) -> None:
        payloads: dict[str, Any] = {}
        failed: list[str] = []
        for path in self._active(state, endpoints_for(state.product)):
            ok, data = await self._fetch(state, path)
            if ok:
                payloads[SHORT_NAMES[path]] = data
            else:
                failed.append(SHORT_NAMES[path])
        if self.elapsed() >= state.next_slow:
            slow_ok = True
            for path in self._active(state, slow_endpoints_for(state.product)):
                ok, data = await self._fetch(state, path)
                if not ok:
                    slow_ok = False
                    continue
                short = SHORT_NAMES[path]
                add_field_types(state.inventory, short, data)
                self.write(
                    {
                        **self._base("slow"),
                        "device": state.label,
                        "product": state.product,
                        "endpoint": short,
                        "data": redact_petlibro(data),
                    }
                )
            if slow_ok:
                state.next_slow = self.elapsed() + self.slow_seconds
        state.polls += 1
        if payloads:
            self.observe(state, payloads, failed)

    def observe(self, state: DeviceState, payloads: Mapping[str, Any], failed: list[str]) -> None:
        """Write the state line for one device's poll and print key-value changes."""
        for short, data in payloads.items():
            add_field_types(state.inventory, short, data)
        redacted = {short: redact_petlibro(data) for short, data in payloads.items()}
        flat = flat_record(redacted)
        first = state.last_flat is None
        changed: dict[str, list] = {}
        if not first:
            for short, fields in flat.items():
                changed.update(diff_records(state.last_flat.get(short, {}), fields))
            state.last_flat.update(flat)
        else:
            state.last_flat = dict(flat)
        real = redacted.get("realInfo")
        online = real.get("online") if isinstance(real, Mapping) and "online" in real else None
        if online is None:
            online = state.listed_online
        key = {"online": online} if online is not None else {}
        key.update(extract_key(redacted))
        line: dict[str, Any] = {
            **self._base("state"),
            "device": state.label,
            "product": state.product,
            "online": online,
            "key": key,
            "changed": changed,
        }
        if failed:
            line["failed"] = failed
        if first or changed:
            line["raw"] = redacted
        self.write(line)
        self._track_key(state, key, first)

    def _track_key(self, state: DeviceState, key: Mapping[str, Any], first: bool) -> None:
        weight = key.get("weightPercent")
        if isinstance(weight, (int, float)) and not isinstance(weight, bool):
            state.weight_min = weight if state.weight_min is None else min(state.weight_min, weight)
            state.weight_max = weight if state.weight_max is None else max(state.weight_max, weight)
        stamp = self._stamp()
        if first:
            values = "  ".join(f"{k} {_show(v)}" for k, v in key.items()) or "(no key values)"
            self.say(f"{stamp}  {state.label}  {state.product_name or state.product}: {values}")
        else:
            for name, value in key.items():
                if name in state.key and state.key[name] != value:
                    state.key_changes[name] = state.key_changes.get(name, 0) + 1
                    self.say(f"{stamp}  {state.label}  {name} {_show(state.key[name])} → "
                             f"{_show(value)}")
        state.key.update(key)

    async def poll_cycle(self) -> int:
        """One poll of every device. Returns how many errors call for backoff (transport
        trouble or a failed device list); non-zero API codes are logged but not counted."""
        self.client.begin_cycle()
        self.cycle_errors = 0
        if (
            not self.devices
            or self._next_relist is None
            or self.elapsed() >= self._next_relist
        ):
            try:
                await self.relist()
            except (SessionReplaced, PetlibroAuthFailed, ForbiddenPath):
                raise
            except Exception as exc:  # noqa: BLE001 - keep the old list and carry on
                self.error("deviceList", exc)  # always backs off, ApiError included
            finally:
                await self._gap()
        for state in list(self.devices.values()):
            await self.poll_device(state)
        return self.cycle_errors

    # -- exit ------------------------------------------------------------------------------

    def write_fields(self) -> None:
        for state in self.devices.values():
            self.write(
                {
                    **self._base("fields"),
                    "device": state.label,
                    "product": state.product,
                    "fields": inventory_json(state.inventory),
                    "dropped": dict(state.dropped),
                }
            )

    def summary_text(self) -> str:
        lines = ["", "Summary", "-------", f"Run length: {format_duration(self.elapsed())}"]
        if not self.devices:
            lines.append("No devices seen.")
        for s in self.devices.values():
            lines.append(f"{s.label} ({s.product_name or s.product}): {s.polls} polls, "
                         f"{s.errors} errors")
            now = "  ".join(f"{k} {_show(v)}" for k, v in s.key.items()) or "-"
            lines.append(f"  now: {now}")
            changes = "  ".join(f"{k} {n}" for k, n in s.key_changes.items()) or "none"
            lines.append(f"  changes: {changes}")
            if s.dropped:
                dropped = "  ".join(f"{k} (code {c})" for k, c in s.dropped.items())
                lines.append(f"  dropped: {dropped}")
            if s.weight_min is not None:
                lines.append(f"  weightPercent min {_show(s.weight_min)} "
                             f"max {_show(s.weight_max)}")
        return "\n".join(lines)

    def close(self) -> None:
        self.log.close()


def _show(value: Any) -> str:
    return "?" if value is None else str(value)


# --------------------------------------------------------------------------------------------
# The run loop
# --------------------------------------------------------------------------------------------


async def run(
    opts: argparse.Namespace,
    email: str,
    password: str,
    timezone: str,
    *,
    base_url: str = BASE_URL,
    out: TextIO | None = None,
    err: TextIO | None = None,
    request_gap: float = REQUEST_GAP,
    relist_seconds: float = RELIST_SECONDS,
    slow_seconds: float = SLOW_SECONDS,
) -> int:
    scrubber = Scrubber()
    client = PetlibroClient(email, password, timezone, base_url=base_url, scrubber=scrubber)
    spike = PetlibroSpike(
        client,
        opts.log_dir,
        timezone,
        out=out,
        err=err,
        request_gap=request_gap,
        relist_seconds=relist_seconds,
        slow_seconds=slow_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
            installed.append(sig)
    if opts.duration:
        loop.call_later(opts.duration, stop.set)

    poll_seconds = float(opts.poll_seconds)
    backoff = Backoff(start=poll_seconds, maximum=max(poll_seconds, float(RELIST_SECONDS)))
    exit_code = EXIT_OK
    spike.say(f"Logging to {spike.log.path}  (Ctrl-C to stop)")
    spike.say(PHONE_LOGOUT_NOTICE)
    spike.log_info("start", poll_seconds=poll_seconds, timezone=timezone)
    try:
        # Log in once. Network trouble is retried with backoff; a rejected login is not.
        while not stop.is_set():
            try:
                await _until_stop(stop, client.login())
                backoff.reset()
                break
            except _Stopped:
                break
            except PetlibroAuthFailed as exc:
                spike.error("login", exc)
                spike.flush_errors(None)
                spike.say("\n" + auth_hint(exc.code, exc.msg), error=True)
                exit_code = EXIT_AUTH
                return exit_code
            except Exception as exc:  # noqa: BLE001
                spike.error("login", exc)
                delay = backoff.next()
                spike.flush_errors(delay)
                if await _sleep_or_stop(stop, delay):
                    break

        while not stop.is_set():
            try:
                errors = await _until_stop(stop, spike.poll_cycle())
            except _Stopped:
                spike.flush_errors(None)
                break
            except PetlibroAuthFailed as exc:
                spike.error("login", exc)
                spike.flush_errors(None)
                spike.say("\n" + auth_hint(exc.code, exc.msg), error=True)
                exit_code = EXIT_AUTH
                return exit_code
            except SessionReplaced as exc:
                spike.error("session", exc)
                errors = 1
            delay = backoff.next() if errors else poll_seconds
            if not errors:
                backoff.reset()
            spike.flush_errors(delay)
            if await _sleep_or_stop(stop, delay):
                break
    finally:
        spike.flush_errors(None)
        with contextlib.suppress(Exception):
            spike.log_info("exit")
            spike.write_fields()
        spike.say(spike.summary_text())
        spike.close()
        with contextlib.suppress(Exception):
            await client.close()
        for sig in installed:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(sig)
    return exit_code


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    opts = build_parser().parse_args(argv)
    opts.poll_seconds, clamped = clamp_poll_seconds(opts.poll_seconds)
    if clamped:
        print(f"--poll-seconds raised to the minimum of {MIN_POLL_SECONDS}.", file=sys.stderr)
    env = os.environ if environ is None else environ
    creds = read_credentials(env)
    if isinstance(creds, list):
        print(MISSING_ENV_MESSAGE.format(missing=", ".join(creds)), file=sys.stderr)
        return EXIT_MISSING_ENV
    email, password = creds
    try:
        timezone = resolve_timezone(env)
    except TimezoneError as exc:
        print(TIMEZONE_MESSAGE.format(reason=exc), file=sys.stderr)
        return EXIT_MISSING_ENV
    if len(password) > MAX_PASSWORD_LENGTH:
        print(
            f"warning: PETLIBRO_PASSWORD is longer than {MAX_PASSWORD_LENGTH} characters; "
            "PETLIBRO is known to reject those (code 1102). Trying anyway.",
            file=sys.stderr,
        )
    try:
        return asyncio.run(run(opts, email, password, timezone))
    except KeyboardInterrupt:
        return EXIT_OK
