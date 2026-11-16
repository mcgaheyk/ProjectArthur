"""Spike logger: record every state change of a real Litter-Robot 4 with precise timestamps.

The pure parts (record building, diffing, rate limiting, redaction, summaries, file writing)
are at the top and are unit-tested with fake robots. The network part (`run`) is at the
bottom and imports pylitterbot lazily, so importing this module never touches the network.

Run it with:  uv run --env-file .env python scripts/spike_log.py
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

# --------------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------------

EXIT_OK = 0
EXIT_MISSING_ENV = 2
EXIT_AUTH = 3

DEFAULT_POLL_SECONDS = 300
MIN_POLL_SECONDS = 120
HEARTBEAT_SECONDS = 15 * 60
ACTIVITY_SECONDS = 6 * 60 * 60
NOISY_MIN_INTERVAL = 5.0
BACKOFF_START = 30.0
BACKOFF_MAX = 600.0
TOKEN_FILE = Path(".whisker-token.json")

# Public LitterRobot4 properties recorded on every update (verified in the installed
# pylitterbot 2025.8.0, robot/litterrobot4.py and robot/litterrobot.py). Identifying
# properties (name, serial, id) and the side-effecting `litter_level_calculated` are left out.
TRACKED_PROPERTIES: tuple[str, ...] = (
    "status",
    "status_code",
    "status_text",
    "is_online",
    "is_on",
    "is_sleeping",
    "sleep_mode_enabled",
    "waste_drawer_level",
    "is_waste_drawer_full",
    "is_drawer_full_indicator_triggered",
    "litter_level",
    "litter_level_state",
    "pet_weight",
    "clean_cycle_wait_time_minutes",
    "is_laser_dirty",
    "power_type",
    "last_seen",
    "cycle_count",
    "cycle_capacity",
    "cycles_after_drawer_full",
    "globe_motor_fault_status",
    "hopper_status",
    "is_hopper_removed",
)

# Raw keys from robot.to_dict(). Every one is in LITTER_ROBOT_4_MODEL (robot/models.py).
TRACKED_RAW_KEYS: tuple[str, ...] = (
    "robotStatus",
    "robotCycleState",
    "robotCycleStatus",
    "displayCode",
    "catDetect",
    "isCatDetectPending",
    "weightSensor",
    "pinchStatus",
    "isDFIFull",
    "isDFIPartialFull",
    "DFILevelPercent",
    "litterLevel",
    "litterLevelPercentage",
    "sleepStatus",
    "unitPowerStatus",
    "isBonnetRemoved",
    "wifiRssi",
    "globeMotorFaultStatus",
)

# Fields that change without the robot doing anything interesting. When only these change,
# write at most one line every NOISY_MIN_INTERVAL seconds (the latest value is kept and
# flushed later, never dropped).
NOISY_FIELDS: frozenset[str] = frozenset({"wifiRssi", "weightSensor", "last_seen", "litterLevel"})

VISIT_SEQUENCE: tuple[str, ...] = ("CAT_DETECTED", "CAT_SENSOR_TIMING", "CLEAN_CYCLE", "READY")

REDACTED = "**REDACTED**"
_REDACT_KEY = re.compile(
    r"(^id$|Id$|ID$|_id$|serial|name|email|token|password|session|^ip|ipaddress|"
    r"mac(address)?$|^mac|ssid|account|^user|owner|phone)",
    re.IGNORECASE,
)
_REDACT_VALUE = re.compile(
    r"([^@\s]+@[^@\s]+\.[A-Za-z]{2,}"  # email
    r"|\b\d{1,3}(\.\d{1,3}){3}\b"  # IPv4
    r"|\b[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}\b)"  # MAC
)

# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def to_jsonable(value: Any) -> Any:
    """Turn property values into plain JSON values (enums by name, datetimes as ISO)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    return str(value)


def utc_iso_ms(dt: datetime) -> str:
    """UTC ISO timestamp with milliseconds and a Z suffix."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


_SCRUB_PATTERNS = (
    (re.compile(r"(header|payload)=[^&\s'\"]+"), r"\1=" + REDACTED),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer " + REDACTED),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"), REDACTED),
)


def scrub(text: str) -> str:
    """Remove tokens from text that may be logged (the WebSocket URL carries the bearer
    token base64-encoded in its `header=` query parameter)."""
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class ScrubFilter(logging.Filter):
    """Logging filter that scrubs tokens out of library log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not break logging
            message = str(record.msg)
        record.msg = scrub(message)
        record.args = None
        return True


def redact(data: Any) -> Any:
    """Redact identifying values by key pattern (and emails/IPs/MACs by value); keep keys."""
    if isinstance(data, Mapping):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if _REDACT_KEY.search(str(key)) and value not in (None, ""):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = redact(value)
        return out
    if isinstance(data, (list, tuple)):
        return [redact(v) for v in data]
    if isinstance(data, str) and _REDACT_VALUE.search(data):
        return REDACTED
    return to_jsonable(data)


# --------------------------------------------------------------------------------------------
# Records and diffing
# --------------------------------------------------------------------------------------------


def build_record(robot: Any) -> dict[str, Any]:
    """Flat record of the tracked public properties plus selected raw fields."""
    record: dict[str, Any] = {}
    for name in TRACKED_PROPERTIES:
        try:
            record[name] = to_jsonable(getattr(robot, name))
        except Exception as exc:  # noqa: BLE001 - one broken property must not lose the record
            record[name] = f"<error: {type(exc).__name__}>"
    try:
        raw = robot.to_dict() or {}
    except Exception:  # noqa: BLE001
        raw = {}
    for key in TRACKED_RAW_KEYS:
        record[key] = to_jsonable(raw.get(key))
    return record


def diff_records(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, list]:
    """{field: [old, new]} for every field that differs."""
    old = old or {}
    keys = list(dict.fromkeys([*old.keys(), *new.keys()]))
    return {k: [old.get(k), new.get(k)] for k in keys if old.get(k) != new.get(k)}


def is_noisy_only(changed: Mapping[str, Any]) -> bool:
    return bool(changed) and all(k in NOISY_FIELDS for k in changed)


class Recorder:
    """Decides which observations become log lines.

    Compares against the last *written* record, so a line's `changed` is always relative to
    the previous line. Noisy-only changes are rate-limited; a suppressed one is held as
    pending and written by `flush_pending` once the interval has passed.
    """

    def __init__(
        self,
        wall: Callable[[], datetime] | None = None,
        mono: Callable[[], float] | None = None,
        noisy_interval: float = NOISY_MIN_INTERVAL,
    ) -> None:
        self._wall = wall or (lambda: datetime.now(UTC))
        self._mono = mono or time.monotonic
        self._t0 = self._mono()
        self.noisy_interval = noisy_interval
        self.last_written: dict[str, Any] | None = None
        self._last_write_mono: float | None = None
        self._pending: tuple[dict[str, Any], str, Mapping | None] | None = None

    def elapsed(self) -> float:
        return self._mono() - self._t0

    def _line(
        self, record: dict[str, Any], source: str, changed: dict, raw: Mapping | None
    ) -> dict[str, Any]:
        line: dict[str, Any] = {
            "ts": utc_iso_ms(self._wall()),
            "t": round(self.elapsed(), 3),
            "kind": "state",
            "source": source,
            "status": record.get("status"),
            "status_code": record.get("status_code"),
            "changed": changed,
            "record": record,
        }
        status_changed = "status" in changed or source == "start"
        if status_changed and raw is not None:
            line["raw"] = redact(raw)
        return line

    def _commit(self, record: dict[str, Any]) -> None:
        self.last_written = dict(record)
        self._last_write_mono = self._mono()
        self._pending = None

    def observe(
        self, record: dict[str, Any], source: str, raw: Mapping | None = None
    ) -> dict[str, Any] | None:
        """Return the line to write for this observation, or None."""
        if self.last_written is None:
            line = self._line(record, source, {}, raw)
            self._commit(record)
            return line
        changed = diff_records(self.last_written, record)
        if source == "heartbeat":
            line = self._line(record, source, changed, raw)
            self._commit(record)
            return line
        if not changed:
            self._pending = None
            return None
        if is_noisy_only(changed):
            since = self._mono() - (self._last_write_mono or 0.0)
            if since < self.noisy_interval:
                self._pending = (dict(record), source, raw)
                return None
        line = self._line(record, source, changed, raw)
        self._commit(record)
        return line

    def flush_pending(self) -> dict[str, Any] | None:
        """Write a held noisy-only change once the rate-limit interval has passed."""
        if self._pending is None or self.last_written is None:
            return None
        if self._mono() - (self._last_write_mono or 0.0) < self.noisy_interval:
            return None
        record, source, raw = self._pending
        changed = diff_records(self.last_written, record)
        self._pending = None
        if not changed:
            return None
        line = self._line(record, source, changed, raw)
        self._commit(record)
        return line


# --------------------------------------------------------------------------------------------
# Status timeline and summary
# --------------------------------------------------------------------------------------------


@dataclass
class StatusTracker:
    """Status periods, for the console line and the exit summary."""

    periods: list[tuple[str, float, datetime]] = field(default_factory=list)  # status, t, wall

    @property
    def current(self) -> str | None:
        return self.periods[-1][0] if self.periods else None

    def update(self, status: str, t: float, wall: datetime) -> float | None | bool:
        """Record a status. Returns False if unchanged, else the previous status's duration
        (None if there was no previous status)."""
        if self.periods and self.periods[-1][0] == status:
            return False
        previous = self.periods[-1][1] if self.periods else None
        self.periods.append((status, t, wall))
        return None if previous is None else t - previous

    @property
    def transitions(self) -> int:
        return max(0, len(self.periods) - 1)

    def time_in_status(self, t_end: float) -> dict[str, float]:
        totals: dict[str, float] = {}
        for i, (status, start, _) in enumerate(self.periods):
            end = self.periods[i + 1][1] if i + 1 < len(self.periods) else t_end
            totals[status] = totals.get(status, 0.0) + max(0.0, end - start)
        return totals

    def visit_sequences(self) -> list[dict[str, Any]]:
        """Each complete CAT_DETECTED → CAT_SENSOR_TIMING → CLEAN_CYCLE → READY run, with the
        duration of the three phases before READY."""
        found = []
        n = len(VISIT_SEQUENCE)
        for i in range(len(self.periods) - n + 1):
            window = self.periods[i : i + n]
            if tuple(p[0] for p in window) != VISIT_SEQUENCE:
                continue
            phases = {window[j][0]: round(window[j + 1][1] - window[j][1], 3) for j in range(n - 1)}
            found.append(
                {
                    "start": utc_iso_ms(window[0][2]),
                    "phases": phases,
                    "total": round(window[-1][1] - window[0][1], 3),
                }
            )
        return found

    def summary_text(self, t_end: float) -> str:
        lines = ["", "Summary", "-------"]
        totals = self.time_in_status(t_end)
        if not totals:
            lines.append("No status observed.")
            return "\n".join(lines)
        lines.append(f"Run length: {format_duration(t_end)}   transitions: {self.transitions}")
        lines.append("Time in each status:")
        for status, seconds in sorted(totals.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {status:<24} {format_duration(seconds)}")
        sequences = self.visit_sequences()
        lines.append(f"Complete visits ({' → '.join(VISIT_SEQUENCE)}): {len(sequences)}")
        for seq in sequences:
            phases = "  ".join(f"{k} {format_duration(v)}" for k, v in seq["phases"].items())
            lines.append(f"  {seq['start']}  {phases}  (total {format_duration(seq['total'])})")
        return "\n".join(lines)


def console_line(
    wall: datetime, old: str | None, new: str, source: str, lasted: float | None, drawer: Any
) -> str:
    local = wall.astimezone()
    stamp = local.strftime("%H:%M:%S.") + f"{local.microsecond // 1000:03d}"
    drawer_text = f"{drawer:.0f}%" if isinstance(drawer, (int, float)) else "?"
    previous = f"{old} lasted {format_duration(lasted)}" if old else "first status"
    return f"{stamp}  {old or '-'} → {new}  [{source}]  {previous}  drawer {drawer_text}"


# --------------------------------------------------------------------------------------------
# Files, environment, token cache, options
# --------------------------------------------------------------------------------------------


class DailyJsonl:
    """Append JSON lines to <dir>/<prefix>-YYYYMMDD.jsonl (local date), flushed per line."""

    def __init__(self, log_dir: Path, prefix: str, today: Callable[[], date] | None = None) -> None:
        self.log_dir = Path(log_dir)
        self.prefix = prefix
        self._today = today or date.today
        self._date: date | None = None
        self._fh: TextIO | None = None

    @property
    def path(self) -> Path:
        return self.log_dir / f"{self.prefix}-{self._today():%Y%m%d}.jsonl"

    def write(self, obj: Mapping[str, Any]) -> None:
        today = self._today()
        if self._fh is None or today != self._date:
            self.close()
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")  # noqa: SIM115
            self._date = today
        self._fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


MISSING_ENV_MESSAGE = """\
Whisker credentials are not set ({missing}).

  1. Copy .env.example to .env
  2. Fill in WHISKER_USERNAME and WHISKER_PASSWORD in .env (it is gitignored)
  3. Run:  uv run --env-file .env python scripts/spike_log.py
"""


def read_credentials(environ: Mapping[str, str]) -> tuple[str, str] | list[str]:
    """Return (username, password), or the list of missing variable names."""
    names = ("WHISKER_USERNAME", "WHISKER_PASSWORD")
    missing = [n for n in names if not (environ.get(n) or "").strip()]
    if missing:
        return missing
    return environ["WHISKER_USERNAME"].strip(), environ["WHISKER_PASSWORD"]


def load_token(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("access_token") and data.get("id_token"):
        return data
    return None


def save_token(path: Path, token: Mapping | None) -> None:
    """Write the token dict with mode 600 (atomic replace). Never logs its contents."""
    if not token:
        return
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(dict(token), fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def delete_token(path: Path) -> None:
    with contextlib.suppress(OSError):
        Path(path).unlink()


def clamp_poll_seconds(value: float) -> tuple[float, bool]:
    """Enforce the minimum poll interval. Returns (seconds, was_clamped)."""
    if value < MIN_POLL_SECONDS:
        return float(MIN_POLL_SECONDS), True
    return float(value), False


class Backoff:
    def __init__(self, start: float = BACKOFF_START, maximum: float = BACKOFF_MAX) -> None:
        self.start = start
        self.maximum = maximum
        self._next = start

    def next(self) -> float:
        delay = self._next
        self._next = min(self._next * 2, self.maximum)
        return delay

    def reset(self) -> None:
        self._next = self.start


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spike_log.py",
        description=(
            "Log every state change of your Litter-Robot 4 with precise timestamps "
            "(JSON lines in the log directory; one console line per status change)."
        ),
        epilog=(
            "example:\n"
            "  uv run --env-file .env python scripts/spike_log.py\n"
            "  uv run --env-file .env python scripts/spike_log.py --duration 3600\n\n"
            "Needs WHISKER_USERNAME and WHISKER_PASSWORD in the environment (copy "
            ".env.example to .env).\nExit codes: 0 ok, 2 credentials missing, "
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
        help=f"refresh() interval, minimum {MIN_POLL_SECONDS} (default {DEFAULT_POLL_SECONDS})",
    )
    parser.add_argument(
        "--log-dir", type=Path, default=Path("logs"), help="where to write (default: logs)"
    )
    return parser


# --------------------------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------------------------

_AUTH_ERROR_CODES = {
    "NotAuthorizedException",
    "UserNotFoundException",
    "UserNotConfirmedException",
    "PasswordResetRequiredException",
}


# pylitterbot's login error, and pycognito's (`WarrantException` covers forced password
# change, MFA challenges and token verification), which pylitterbot does not wrap.
_AUTH_EXCEPTION_NAMES = {"LitterRobotLoginException", "WarrantException"}


def _chain(exc: BaseException | None) -> Iterable[BaseException]:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def is_auth_error(exc: BaseException) -> bool:
    """True for a rejected login/token: pylitterbot's LitterRobotLoginException, pycognito's
    WarrantException family, a Cognito ClientError with an auth code, or an HTTP 401 —
    anywhere in the exception chain. Auth errors are never retried in a loop."""
    for e in _chain(exc):
        names = {cls.__name__ for cls in type(e).__mro__}
        if names & _AUTH_EXCEPTION_NAMES:
            return True
        response = getattr(e, "response", None)
        if isinstance(response, Mapping):
            code = (response.get("Error") or {}).get("Code")
            if code in _AUTH_ERROR_CODES:
                return True
        if getattr(e, "status", None) == 401:
            return True
    return False


class AuthFailed(Exception):
    """Login rejected; do not retry."""


class NoRobotFound(Exception):
    """Connected, but no Litter-Robot 4 on the account (or the fetch was swallowed)."""


# --------------------------------------------------------------------------------------------
# The live logger
# --------------------------------------------------------------------------------------------


class SpikeLogger:
    """Holds the recorder, files and timeline; turns robot updates into lines."""

    def __init__(
        self,
        log_dir: Path,
        out: TextIO | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self.recorder = recorder or Recorder()
        self.states = DailyJsonl(log_dir, "spike")
        self.activity = DailyJsonl(log_dir, "spike-activity")
        self.tracker = StatusTracker()
        self.out = out or sys.stdout
        self.robot: Any = None
        self._polling = False
        self._poll_task: asyncio.Task | None = None
        self._seen_activity: set[tuple[str, str]] = set()
        self._activity_since: datetime | None = None

    # -- writing ---------------------------------------------------------------------------

    def _emit(self, line: dict[str, Any] | None) -> None:
        if line is None:
            return
        self.states.write(line)
        status = line.get("status")
        if not isinstance(status, str):
            return
        wall = datetime.fromisoformat(line["ts"])
        old = self.tracker.current
        result = self.tracker.update(status, line["t"], wall)
        if result is False:
            return
        drawer = (line.get("record") or {}).get("waste_drawer_level")
        print(
            console_line(wall, old, status, line["source"], result, drawer),
            file=self.out,
            flush=True,
        )

    def log_error(
        self, where: str, exc: BaseException | str, retry_in: float | None = None
    ) -> None:
        message = scrub(str(exc)) if isinstance(exc, BaseException) else scrub(exc)
        error_type = type(exc).__name__ if isinstance(exc, BaseException) else "Error"
        line = {
            "ts": utc_iso_ms(datetime.now(UTC)),
            "t": round(self.recorder.elapsed(), 3),
            "kind": "error",
            "where": where,
            "error": error_type,
            "message": message,
            "retry_in": retry_in,
        }
        try:
            self.states.write(line)
        except OSError:
            pass
        retry = f"; retrying in {format_duration(retry_in)}" if retry_in else ""
        print(f"error ({where}): {error_type}: {message}{retry}", file=sys.stderr, flush=True)

    def log_info(self, event: str, **extra: Any) -> None:
        self.states.write(
            {
                "ts": utc_iso_ms(datetime.now(UTC)),
                "t": round(self.recorder.elapsed(), 3),
                "kind": "info",
                "event": event,
                **extra,
            }
        )

    # -- observing -------------------------------------------------------------------------

    def observe(self, source: str) -> None:
        if self.robot is None:
            return
        record = build_record(self.robot)
        try:
            raw = self.robot.to_dict()
        except Exception:  # noqa: BLE001
            raw = None
        self._emit(self.recorder.observe(record, source, raw))

    def current_source(self) -> str:
        """`poll` only for updates emitted inside our own refresh() call. The flag alone could
        mislabel a push that lands while refresh() is awaiting, so the task must match too."""
        if self._polling and asyncio.current_task() is self._poll_task:
            return "poll"
        return "ws"

    def on_update(self) -> None:
        """pylitterbot update callback: synchronous, no args, its exceptions are swallowed by
        the library, so never raise and log our own errors."""
        try:
            self.observe(self.current_source())
        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(Exception):
                self.log_error("update-callback", exc)

    def flush(self) -> None:
        try:
            self._emit(self.recorder.flush_pending())
        except Exception as exc:  # noqa: BLE001
            self.log_error("flush", exc)

    async def poll(self) -> None:
        self._polling = True
        self._poll_task = asyncio.current_task()
        try:
            await self.robot.refresh()
        finally:
            self._polling = False
            self._poll_task = None
        self.observe("poll")

    # -- activity history --------------------------------------------------------------------

    def record_activity(self, activities: Iterable[Any], pets: Iterable[Any] = ()) -> int:
        """Append history entries not seen before. Returns how many were new."""
        new = 0
        fetched_at = utc_iso_ms(datetime.now(UTC))
        for act in sorted(activities, key=lambda a: str(a.timestamp)):
            action = act.action
            text = getattr(action, "text", None) or str(action)
            code = action.value if isinstance(action, Enum) else None
            ts = to_jsonable(act.timestamp)
            key = (str(ts), text)
            if key in self._seen_activity:
                continue
            self._seen_activity.add(key)
            entry = {"fetched": fetched_at, "kind": "activity", "timestamp": ts, "action": text}
            if code is not None:
                entry["code"] = code
            self.activity.write(entry)
            new += 1
            if isinstance(act.timestamp, datetime) and (
                self._activity_since is None or act.timestamp > self._activity_since
            ):
                self._activity_since = act.timestamp
        for index, pet in enumerate(pets or (), start=1):
            for m in getattr(pet, "weight_history", None) or ():
                ts = to_jsonable(m.timestamp)
                key = (str(ts), f"pet{index}-weight")
                if key in self._seen_activity:
                    continue
                self._seen_activity.add(key)
                self.activity.write(
                    {
                        "fetched": fetched_at,
                        "kind": "pet_weight",
                        "pet": f"pet{index}",
                        "timestamp": ts,
                        "weight_lbs": m.weight,
                    }
                )
                new += 1
        return new

    async def fetch_activity(self, pets: Iterable[Any], refresh_pets: bool) -> int:
        activities = await self.robot.get_activity_history(limit=100, start=self._activity_since)
        pet_list = list(pets or ())
        if refresh_pets:
            for pet in pet_list:
                try:
                    await pet.fetch_weight_history()
                except Exception as exc:  # noqa: BLE001 - pet weights are a nice-to-have
                    self.log_error("pet-weight-history", exc)
        return self.record_activity(activities, pet_list)

    def close(self) -> None:
        self.states.close()
        self.activity.close()


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """Sleep, waking early if stop is set. Returns True if stopping."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.0, seconds))
    except TimeoutError:
        return False
    return True


class _Stopped(Exception):
    """Stop was requested while waiting."""


async def _until_stop(stop: asyncio.Event, coro: Any) -> Any:
    """Await coro, but cancel it and raise _Stopped if stop is set first (Ctrl-C mid-login)."""
    task = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
    if task.done():
        return task.result()
    task.cancel()
    with contextlib.suppress(BaseException):
        await task
    raise _Stopped


async def connect_account(username: str, password: str, token_path: Path) -> tuple[Any, Any]:
    """Log in (cached token first, then password) and return (account, robot).

    Raises AuthFailed when the password login is rejected, NoRobotFound when the account has
    no LR4 (or the fetch failed silently), and network errors as they come.
    """
    from pylitterbot import Account, LitterRobot4

    def on_token(token: dict | None) -> None:
        try:
            save_token(token_path, token)
        except OSError as exc:
            print(f"warning: could not save token cache: {type(exc).__name__}", file=sys.stderr)

    async def attempt(token: dict | None) -> tuple[Any, Any]:
        account = Account(token=token, token_update_callback=on_token, robot_types=[LitterRobot4])
        try:
            await account.connect(
                username=username,
                password=password,
                load_robots=True,
                subscribe_for_updates=True,
                load_pets=True,
            )
            robots = account.get_robots(LitterRobot4)
            if not robots:
                raise NoRobotFound("No Litter-Robot 4 found on this account (or the fetch failed).")
            return account, robots[0]
        except BaseException:
            with contextlib.suppress(Exception):
                await account.disconnect()
            raise

    cached = load_token(token_path)
    if cached:
        try:
            return await attempt(cached)
        except Exception as exc:
            # A corrupt cache fails in Account() itself: it decodes the id token with PyJWT.
            bad_jwt = any(type(e).__module__.startswith("jwt") for e in _chain(exc))
            if not (is_auth_error(exc) or bad_jwt):
                raise
            # Cached token rejected: fall back to one password login. (A 401 on the robot
            # fetch is swallowed by load_robots, but load_pets runs next and raises it, so a
            # bad token surfaces here rather than as NoRobotFound.)
            delete_token(token_path)
    try:
        return await attempt(None)
    except NoRobotFound:
        raise
    except Exception as exc:
        if is_auth_error(exc):
            raise AuthFailed(scrub(str(exc))) from None
        raise


async def run(
    opts: argparse.Namespace,
    username: str,
    password: str,
    out: TextIO | None = None,
    token_path: Path = TOKEN_FILE,
) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(ScrubFilter())

    spike = SpikeLogger(opts.log_dir, out=out)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    if opts.duration:
        loop.call_later(opts.duration, stop.set)

    connect_backoff = Backoff()
    exit_code = EXIT_OK
    print(f"Logging to {spike.states.path}  (Ctrl-C to stop)", file=spike.out, flush=True)
    try:
        while not stop.is_set():
            try:
                account, robot = await _until_stop(
                    stop, connect_account(username, password, token_path)
                )
            except _Stopped:
                break
            except AuthFailed:
                spike.log_error("login", "login rejected")
                print(
                    "\nLogin rejected by Whisker. Check WHISKER_USERNAME and WHISKER_PASSWORD in "
                    ".env.\nNot retrying, to avoid locking the account.",
                    file=sys.stderr,
                )
                return EXIT_AUTH
            except Exception as exc:  # noqa: BLE001 - network/API: retry with backoff
                delay = connect_backoff.next()
                spike.log_error("connect", exc, retry_in=delay)
                if await _sleep_or_stop(stop, delay):
                    break
                continue
            connect_backoff.reset()
            try:
                needs_password = await monitor(spike, account, robot, opts, stop)
            finally:
                spike.robot = None
                with contextlib.suppress(Exception):
                    await account.disconnect()
            if needs_password:
                delete_token(token_path)
    finally:
        spike.log_info("exit")
        print(spike.tracker.summary_text(spike.recorder.elapsed()), file=spike.out, flush=True)
        spike.close()
    return exit_code


async def monitor(
    spike: SpikeLogger, account: Any, robot: Any, opts: argparse.Namespace, stop: asyncio.Event
) -> bool:
    """Watch one connected robot until stop. Returns True if the token went bad and a fresh
    login is needed."""
    from pylitterbot.event import EVENT_UPDATE

    spike.robot = robot
    unsubscribe = robot.on(EVENT_UPDATE, spike.on_update)
    spike.log_info("connected", robots=len(account.get_robots(type(robot))))
    spike.observe("start")

    mono = time.monotonic
    poll_seconds = opts.poll_seconds
    poll_backoff = Backoff()
    activity_backoff = Backoff()
    next_poll = mono() + poll_seconds
    next_heartbeat = mono() + HEARTBEAT_SECONDS
    next_activity = mono()  # at start, then every 6 hours
    pets_refresh = False  # pets were just loaded by connect(); refresh them from the 2nd fetch

    try:
        while not stop.is_set():
            now = mono()
            spike.flush()
            if now >= next_activity:
                try:
                    await spike.fetch_activity(account.pets or [], refresh_pets=pets_refresh)
                    pets_refresh = True
                    activity_backoff.reset()
                    next_activity = mono() + ACTIVITY_SECONDS
                except Exception as exc:  # noqa: BLE001
                    delay = activity_backoff.next()
                    spike.log_error("activity", exc, retry_in=delay)
                    next_activity = mono() + max(delay, poll_seconds)
            if now >= next_poll:
                try:
                    await spike.poll()
                    poll_backoff.reset()
                    next_poll = mono() + poll_seconds
                except Exception as exc:  # noqa: BLE001
                    if is_auth_error(exc):
                        spike.log_error("poll", exc)
                        return True
                    # Even a retry stays above the politeness floor.
                    delay = max(poll_backoff.next(), float(MIN_POLL_SECONDS))
                    spike.log_error("poll", exc, retry_in=delay)
                    next_poll = mono() + delay
            if now >= next_heartbeat:
                spike.observe("heartbeat")
                next_heartbeat = mono() + HEARTBEAT_SECONDS
            if await _sleep_or_stop(stop, 1.0):
                break
    finally:
        with contextlib.suppress(Exception):
            unsubscribe()
        spike.flush()
    return False


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    opts = build_parser().parse_args(argv)
    opts.poll_seconds, clamped = clamp_poll_seconds(opts.poll_seconds)
    if clamped:
        print(f"--poll-seconds raised to the minimum of {MIN_POLL_SECONDS}.", file=sys.stderr)
    creds = read_credentials(os.environ if environ is None else environ)
    if isinstance(creds, list):
        print(MISSING_ENV_MESSAGE.format(missing=", ".join(creds)), file=sys.stderr)
        return EXIT_MISSING_ENV
    username, password = creds
    try:
        return asyncio.run(run(opts, username, password))
    except KeyboardInterrupt:
        return EXIT_OK
