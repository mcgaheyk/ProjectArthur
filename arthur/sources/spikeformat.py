"""The spike log's lines, built the same way on CPython and MicroPython (D-0029).

``logs/spike-*.jsonl`` and ``logs/spike-activity-*.jsonl`` are written by both live sources:
:class:`arthur.sources.whisker.WhiskerSource` (pylitterbot, through
:class:`arthur.spikelog.SpikeLogger`) and :class:`arthur.sources.wire_source.WireSource` (our
own client, on the Mac or on the clock). The studio reads them back
(:mod:`arthur.sources.logfile`). What a line holds is decided here, once:

- :class:`Recorder` decides which observations become state lines (noisy-only changes at most
  every :data:`NOISY_MIN_INTERVAL` seconds, held and flushed later, never dropped).
- :func:`error_line`, :func:`info_line` and :func:`activity_entries` build the other lines.
- :func:`scrub` takes tokens out of text, :func:`redact` identifying values out of raw data.
- :func:`dumps` writes a line as text: on CPython ``json.dumps``; on MicroPython a small
  encoder whose floats go through :func:`arthur.wire.util.float_text`, so both Pythons write
  the same characters (MicroPython's own ``repr`` prints ``0.07`` as ``0.07000000000000001``).
- A *sink* takes the finished lines: ``state(line)`` for ``spike-*.jsonl`` lines and
  ``activity(entry)`` for ``spike-activity-*.jsonl`` entries, both dicts, and ``close()``.
  On CPython that is :class:`arthur.spikelog.DailyFiles`; on the clock the caller passes its
  own, or :class:`MemorySink` keeps the newest lines in memory.

On CPython ``scrub``, ``redact``, ``utc_iso_ms`` and ``to_jsonable`` are the regular-expression
and ``datetime`` code :mod:`arthur.spikelog` always had, unchanged. MicroPython's ``re`` lacks
what those expressions use, so there the ``_portable`` versions below stand in; the tests
check they answer the same over the real logs and a set of made-up strings.
"""

import sys

from arthur.wire.util import float_text

IS_MPY = sys.implementation.name == "micropython"

#: Fields that change without the robot doing anything interesting. When only these change,
#: write at most one line every NOISY_MIN_INTERVAL seconds (the latest value is kept and
#: flushed later, never dropped).
NOISY_FIELDS = frozenset({"wifiRssi", "weightSensor", "last_seen", "litterLevel"})
NOISY_MIN_INTERVAL = 5.0

REDACTED = "**REDACTED**"


# =============================================================================================
# Small helpers, the same on both Pythons
# =============================================================================================


def format_duration(seconds):
    """``12.5s``, ``4m05s``, ``1h02m03s``; ``?`` for None."""
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


def diff_records(old, new):
    """{field: [old, new]} for every field that differs. Keys in ``old``'s order, then new
    ones (on MicroPython a dict's order is its hash order: the line's ``changed`` is read as a
    mapping, never by position)."""
    old = old or {}
    out = {}
    for k in old:
        if old.get(k) != new.get(k):
            out[k] = [old.get(k), new.get(k)]
    for k in new:
        if k not in old and new.get(k) is not None:
            out[k] = [None, new.get(k)]
    return out


def is_noisy_only(changed):
    if not changed:
        return False
    for k in changed:
        if k not in NOISY_FIELDS:
            return False
    return True


def _utc_iso_ms_portable(dt):
    """``utc_iso_ms`` from the datetime's own fields (no ``isoformat(timespec=...)``)."""
    from datetime import UTC

    u = dt.astimezone(UTC)
    return (f"{u.year:04d}-{u.month:02d}-{u.day:02d}T{u.hour:02d}:{u.minute:02d}:"
            f"{u.second:02d}.{u.microsecond // 1000:03d}Z")


def _round3_portable(x):
    """``round(x, 3)``. MicroPython's ``round`` with digits is not correctly rounded; for the
    elapsed seconds a line carries (whole milliseconds from the clock's tick counter, or the
    tests' made-up times) this gives CPython's answer."""
    return round(x * 1000) / 1000


# -- scrub -------------------------------------------------------------------------------------

_TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


def _is_space(c):
    return c.isspace()


def _scrub_query(text):
    """``(header|payload)=[^&\\s'"]+`` -> ``header=**REDACTED**`` (or ``payload=``)."""
    out = ""
    i = 0
    while True:
        h = text.find("header=", i)
        p = text.find("payload=", i)
        if h < 0 and p < 0:
            return out + text[i:]
        if h < 0 or (0 <= p < h):
            j, key = p, "payload="
        else:
            j, key = h, "header="
        k = j + len(key)
        e = k
        while e < len(text) and text[e] not in "&'\"" and not _is_space(text[e]):
            e += 1
        if e == k:  # nothing after the '=': no match starting here
            out += text[i:j + 1]
            i = j + 1
            continue
        out += text[i:j] + key + REDACTED
        i = e


def _scrub_bearer(text):
    """``Bearer\\s+[A-Za-z0-9._\\-]+`` -> ``Bearer **REDACTED**``."""
    out = ""
    i = 0
    while True:
        j = text.find("Bearer", i)
        if j < 0:
            return out + text[i:]
        k = j + 6
        s = k
        while s < len(text) and _is_space(text[s]):
            s += 1
        e = s
        while e < len(text) and (text[e] in _TOKEN_CHARS or text[e] == "."):
            e += 1
        if s == k or e == s:
            out += text[i:k]
            i = k
            continue
        out += text[i:j] + "Bearer " + REDACTED
        i = e


def _jwt_part(text, i):
    """End of a run of ``[A-Za-z0-9_\\-]`` starting at ``i``."""
    while i < len(text) and text[i] in _TOKEN_CHARS:
        i += 1
    return i


def _scrub_jwt(text):
    """``eyJ[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]*`` -> ``**REDACTED**``."""
    out = ""
    i = 0
    while True:
        j = text.find("eyJ", i)
        if j < 0:
            return out + text[i:]
        a = _jwt_part(text, j + 3)
        ok = a > j + 3 and a < len(text) and text[a] == "."
        if ok:
            b = _jwt_part(text, a + 1)
            ok = b > a + 1 and b < len(text) and text[b] == "."
        if not ok:
            out += text[i:j + 1]
            i = j + 1
            continue
        c = _jwt_part(text, b + 1)
        out += text[i:j] + REDACTED
        i = c


def _scrub_portable(text):
    """:func:`scrub` without regular expressions (MicroPython)."""
    text = _scrub_query(text)
    text = _scrub_bearer(text)
    return _scrub_jwt(text)


# -- redact ------------------------------------------------------------------------------------

_KEY_PARTS = ("serial", "name", "email", "token", "password", "session", "ipaddress", "ssid",
              "account", "owner", "phone")


def _redact_key(key):
    """``_REDACT_KEY.search(key)``, case-insensitive."""
    k = key.lower()
    if k.endswith("id") or k.startswith(("ip", "user")):
        return True
    if k.startswith("mac") or k.endswith(("mac", "macaddress")):
        return True
    for part in _KEY_PARTS:
        if part in k:
            return True
    return False


def _is_word(c):
    return c.isalpha() or c.isdigit() or c == "_"


def _is_ascii_digit(c):
    return "0" <= c <= "9"


def _is_hex(c):
    return "0" <= c <= "9" or "a" <= c <= "f" or "A" <= c <= "F"


def _is_ascii_letter(c):
    return "A" <= c <= "Z" or "a" <= c <= "z"


def _has_email(s):
    """``[^@\\s]+@[^@\\s]+\\.[A-Za-z]{2,}`` somewhere in ``s``."""
    n = len(s)
    for i in range(1, n):
        if s[i] != "@" or s[i - 1] == "@" or _is_space(s[i - 1]):
            continue
        j = i + 1
        while j < n and s[j] != "@" and not _is_space(s[j]):
            if (s[j] == "." and j > i + 1 and j + 2 < n and _is_ascii_letter(s[j + 1])
                    and _is_ascii_letter(s[j + 2])):
                return True
            j += 1
    return False


def _has_ipv4(s):
    r"""``\b\d{1,3}(\.\d{1,3}){3}\b`` somewhere in ``s`` (ASCII digits)."""
    n = len(s)
    for a in range(n):
        if not _is_ascii_digit(s[a]) or (a > 0 and _is_word(s[a - 1])):
            continue
        i = a
        ok = True
        for group in range(4):
            j = i
            while j < n and _is_ascii_digit(s[j]) and j - i < 3:
                j += 1
            if j == i:
                ok = False
                break
            if group < 3:
                if j >= n or s[j] != ".":
                    ok = False
                    break
                i = j + 1
            else:
                i = j
        if ok and (i >= n or not _is_word(s[i])):
            return True
    return False


def _has_mac(s):
    r"""``\b[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}\b`` somewhere in ``s``."""
    n = len(s)
    for a in range(n - 16):
        if a > 0 and _is_word(s[a - 1]):
            continue
        i = a
        ok = True
        for group in range(6):
            if i + 2 > n or not (_is_hex(s[i]) and _is_hex(s[i + 1])):
                ok = False
                break
            i += 2
            if group < 5:
                if i >= n or s[i] not in ":-":
                    ok = False
                    break
                i += 1
        if ok and (i >= n or not _is_word(s[i])):
            return True
    return False


def _redact_portable(data):
    """:func:`redact` without regular expressions (MicroPython)."""
    from arthur.wire.record import to_jsonable

    if isinstance(data, dict):
        out = {}
        for key in data:
            value = data[key]
            if _redact_key(str(key)) and value not in (None, ""):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = _redact_portable(value)
        return out
    if isinstance(data, (list, tuple)):
        return [_redact_portable(v) for v in data]
    if isinstance(data, str) and (_has_email(data) or _has_ipv4(data) or _has_mac(data)):
        return REDACTED
    return to_jsonable(data)


# -- JSON --------------------------------------------------------------------------------------

_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b",
            "\f": "\\f"}


def _json_str(s):
    out = '"'
    start = 0
    for i in range(len(s)):
        c = s[i]
        if c in _ESCAPES or c < " ":
            out += s[start:i] + (_ESCAPES.get(c) or f"\\u{ord(c):04x}")
            start = i + 1
    return out + s[start:] + '"'


def _json_float(v):
    import math

    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Infinity" if v > 0 else "-Infinity"
    return float_text(v)


def _dumps_portable(obj, sort_keys=False):
    """``json.dumps(obj, ensure_ascii=False, default=str, sort_keys=...)`` as CPython writes
    it, with floats through :func:`float_text`."""
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return _json_float(obj)
    if isinstance(obj, str):
        return _json_str(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ", ".join(_dumps_portable(v, sort_keys) for v in obj) + "]"
    if isinstance(obj, dict):
        keys = list(obj)
        if sort_keys:
            keys = sorted(keys)
        parts = []
        for k in keys:
            if isinstance(k, str):
                ks = k
            elif k is None:
                ks = "null"
            elif k is True:
                ks = "true"
            elif k is False:
                ks = "false"
            elif isinstance(k, float):
                ks = _json_float(k)
            else:
                ks = str(k)
            parts.append(_json_str(ks) + ": " + _dumps_portable(obj[k], sort_keys))
        return "{" + ", ".join(parts) + "}"
    return _json_str(str(obj))


# =============================================================================================
# Each Python's own
# =============================================================================================

if not IS_MPY:  # ================================================================== CPython
    import json
    import re
    from collections.abc import Mapping
    from datetime import UTC, date, datetime
    from enum import Enum

    def to_jsonable(value):
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

    def utc_iso_ms(dt):
        """UTC ISO timestamp with milliseconds and a Z suffix."""
        return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def round3(x):
        return round(x, 3)

    _SCRUB_PATTERNS = (
        (re.compile(r"(header|payload)=[^&\s'\"]+"), r"\1=" + REDACTED),
        (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer " + REDACTED),
        (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"), REDACTED),
    )

    def scrub(text):
        """Remove tokens from text that may be logged (the WebSocket URL carries the bearer
        token base64-encoded in its `header=` query parameter)."""
        for pattern, replacement in _SCRUB_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

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

    def redact(data):
        """Redact identifying values by key pattern (and emails/IPs/MACs by value); keep
        keys."""
        if isinstance(data, Mapping):
            out = {}
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

    def dumps(obj, sort_keys=False):
        """One JSON line, as :class:`arthur.spikelog.DailyJsonl` writes it."""
        return json.dumps(obj, ensure_ascii=False, default=str, sort_keys=sort_keys)

    def _default_wall():
        return datetime.now(UTC)

    import time as _time

    _default_mono = _time.monotonic

else:  # ======================================================================== MicroPython
    from arthur.wire import util as _util
    from arthur.wire.record import to_jsonable

    utc_iso_ms = _utc_iso_ms_portable
    round3 = _round3_portable
    scrub = _scrub_portable
    redact = _redact_portable
    dumps = _dumps_portable

    def _default_wall():
        from datetime import UTC, datetime

        return datetime.now(UTC)

    _default_mono = _util.mono


# =============================================================================================
# Lines
# =============================================================================================


class Recorder:
    """Decides which observations become log lines.

    Compares against the last *written* record, so a line's `changed` is always relative to
    the previous line. Noisy-only changes are rate-limited; a suppressed one is held as
    pending and written by `flush_pending` once the interval has passed.
    """

    def __init__(self, wall=None, mono=None, noisy_interval=NOISY_MIN_INTERVAL):
        self._wall = wall or _default_wall
        self._mono = mono or _default_mono
        self._t0 = self._mono()
        self.noisy_interval = noisy_interval
        self.last_written = None
        self._last_write_mono = None
        self._pending = None

    def elapsed(self):
        return self._mono() - self._t0

    def wall(self):
        """The wall time lines are stamped with."""
        return self._wall()

    def stamp(self):
        """``(ts, t)`` for a line written now."""
        return utc_iso_ms(self.wall()), round3(self.elapsed())

    def _line(self, record, source, changed, raw):
        ts, t = self.stamp()
        line = {
            "ts": ts,
            "t": t,
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

    def _commit(self, record):
        self.last_written = dict(record)
        self._last_write_mono = self._mono()
        self._pending = None

    def observe(self, record, source, raw=None):
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

    def flush_pending(self):
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


def error_name(exc):
    """The ``error`` field: an exception's class name, or ``Error`` for a plain message."""
    return type(exc).__name__ if isinstance(exc, BaseException) else "Error"


def error_line(recorder, where, exc, retry_in=None, name=None):
    """An ``error`` line. ``exc`` is an exception or a message; ``name`` overrides the
    ``error`` field (the studio reads ``NoRobotFound`` there)."""
    ts, t = recorder.stamp()
    return {
        "ts": ts,
        "t": t,
        "kind": "error",
        "where": where,
        "error": name or error_name(exc),
        "message": scrub(str(exc)),
        "retry_in": retry_in,
    }


def info_line(recorder, event, extra=None):
    """An ``info`` line: ``event`` and any extra fields."""
    ts, t = recorder.stamp()
    line = {"ts": ts, "t": t, "kind": "info", "event": event}
    if extra:
        for k in extra:
            line[k] = extra[k]
    return line


def activity_time_text(act):
    """An activity's timestamp as the log writes it: ``datetime.isoformat()`` of the parsed
    time. Our client's activities carry it as ``iso`` (on MicroPython ``timestamp`` is a
    number); pylitterbot's are ``datetime``s."""
    iso = getattr(act, "iso", None)
    if isinstance(iso, str):
        return iso
    return to_jsonable(act.timestamp)


def action_text(action):
    """An activity's action as the history shows it ("Cat Detected", "Pet Weight Recorded:
    9.5 lbs"): a ``LitterBoxStatus``'s or our ``Action``'s text, or the string itself."""
    return getattr(action, "text", None) or str(action)


def action_code(action):
    """The status code (``CD``, ``CCP``) of a status action, else None: pylitterbot's
    ``LitterBoxStatus.value`` or our ``Action.value``; a plain string has none."""
    if isinstance(action, str):
        return None
    code = getattr(action, "value", None)
    return code if isinstance(code, str) else None


def activity_entries(activities, fetched_at, seen):
    """The ``spike-activity`` entries for activities not in ``seen`` (a set of
    ``(timestamp text, action text)``, updated), oldest first, and the newest timestamp among
    them (whatever type the activities carry), or None."""
    from arthur.compat import stable_sorted

    out = []
    newest = None
    for act in stable_sorted(activities, key=activity_time_text):
        text = action_text(act.action)
        ts = activity_time_text(act)
        key = (str(ts), text)
        if key in seen:
            continue
        seen.add(key)
        entry = {"fetched": fetched_at, "kind": "activity", "timestamp": ts, "action": text}
        code = action_code(act.action)
        if code is not None:
            entry["code"] = code
        out.append(entry)
        if newest is None or act.timestamp > newest:
            newest = act.timestamp
    return out, newest


class MemorySink:
    """A sink that keeps the newest ``limit`` lines of each log as text (the clock has no
    room for daily files). ``states`` and ``activities`` are lists of JSON lines."""

    def __init__(self, limit=500):
        self.limit = limit
        self.states = []
        self.activities = []

    def _add(self, lines, obj):
        lines.append(dumps(obj))
        if len(lines) > self.limit:
            del lines[0:len(lines) - self.limit]

    def state(self, line):
        self._add(self.states, line)

    def activity(self, entry):
        self._add(self.activities, entry)

    def close(self):
        pass
