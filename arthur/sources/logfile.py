"""A source that reads the spike's logs (``logs/spike-YYYYMMDD.jsonl``) instead of Whisker.

The spike (:mod:`arthur.spikelog`, run by ``scripts/spike_log.py``) holds the one connection
to Whisker and appends what the robot does. This module owns everything about that file
format, so the studio can drive the real pipeline from it:

- :class:`RobotLog` reads every ``spike-2*.jsonl`` (state, info and error lines) and
  ``spike-activity-*.jsonl`` (activity history) in a directory, and picks up appended lines on
  :meth:`RobotLog.refresh` without re-reading whole files: it remembers each file's byte
  offset, consumes only up to the last newline (a half-written line is left for later) and
  notices a new day's file.
- :class:`Timeline` holds the lines ordered by ``ts`` and answers "which snapshot was in force
  at time t" (like ``Scenario.at``) and "where are the visits".
- :func:`follow_view` and :func:`replay_view` turn that into ``(snapshot, health, now)`` for
  live following and for replaying one visit.

Pure standard library: no pylitterbot, no aiohttp. Bad data never raises; malformed lines are
skipped and counted.

Feeder-observed fields come from the log's own history, through the same
:class:`~arthur.sources.tracker.Tracker` the live source will use, fed every state line in
``ts`` order (one frozen tracker state is kept per line, so a replay seek gives the same answer
as playing through):

- ``status_since`` / ``previous_status``: from the line where the status last changed. For the
  very first status in the log, ``status_since`` is the first state line's ``ts`` and
  ``previous_status`` is None.
- ``visit_started_at`` / ``cat_left_at``: the visit under way, and the line where its pet
  weight changed (see the tracker's rules).
- ``last_visit_at``: the latest of (a) the tracker's, the start of the latest visit that
  qualified (a weight, or ``visit_min_s`` long) at the moment in question, and (b) the latest
  "Cat Detected" activity entry at or before that moment (a "Pet Weight Recorded" entry
  counts only when no "Cat Detected" precedes it within 15 minutes, since it is the same
  visit a minute later).
- ``visits_today`` stays None.
"""

from __future__ import annotations

import bisect
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from arthur.model import FeederHealth, RobotSnapshot
from arthur.sources.records import (  # noqa: F401  (re-exported)
    _bool,
    _num,
    _str,
    parse_ts,
    snapshot_from_record,
)
from arthur.sources.tracker import DEFAULT_VISIT_MIN_S, Observed, TrackerState

# arthur.spikelog imports only the standard library at module level (pylitterbot is imported
# lazily inside its network functions), so taking the constant from it is safe.
from arthur.spikelog import HEARTBEAT_SECONDS

#: The log counts as fresh (the spike is alive and polling) while its newest line is younger
#: than this. The spike writes at least a heartbeat line every HEARTBEAT_SECONDS.
FRESH_FOR_S: float = HEARTBEAT_SECONDS + 120

#: Seconds of context shown before a visit's first non-READY line, and after its "just
#: cleaned" window, when replaying.
VISIT_PAD_S: float = 30.0

STATE_GLOB = "spike-2*.jsonl"
ACTIVITY_GLOB = "spike-activity-*.jsonl"

#: Raw robot fields shown next to the mapped state in the studio.
RAW_FIELDS: tuple[str, ...] = (
    "status",
    "robotStatus",
    "robotCycleState",
    "robotCycleStatus",
    "displayCode",
    "catDetect",
)

#: Error lines written by the spike, by ``where``, that mean it lost contact with Whisker.
#: Others (activity, pet-weight-history, update-callback, flush) are side jobs: the robot's
#: state still arrives, so they do not affect health.
_CONTACT_ERRORS = frozenset({"login", "connect", "poll"})
_PET_WEIGHT_SAME_VISIT = timedelta(minutes=15)

SPIKE_COMMAND = "uv run --env-file .env python scripts/spike_log.py"


# =========================================================================================
# Parsing
# =========================================================================================


@dataclass(frozen=True)
class LogLine:
    """One parsed line. ``data`` is the whole JSON object."""

    ts: datetime
    kind: str
    data: dict[str, Any]
    seq: int = 0  # read order, to keep equal timestamps stable

    @property
    def record(self) -> dict[str, Any]:
        r = self.data.get("record")
        return r if isinstance(r, dict) else {}

    @property
    def status(self) -> str:
        s = _str(self.record.get("status"), None) or _str(self.data.get("status"), None)
        return s or "UNKNOWN"


def parse_line(text: str, seq: int = 0) -> LogLine | None:
    """Parse one state-log line. None for anything malformed (the caller counts it)."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    ts = parse_ts(obj.get("ts"))
    kind = obj.get("kind")
    if ts is None or not isinstance(kind, str):
        return None
    if kind == "state":
        rec = obj.get("record")
        if not isinstance(rec, dict):
            return None
        if not (_str(rec.get("status"), None) or _str(obj.get("status"), None)):
            return None
    return LogLine(ts, kind, obj, seq)


@dataclass(frozen=True)
class Activity:
    at: datetime
    action: str


def parse_activity(text: str) -> Activity | None:
    """Parse one activity-log line (``kind`` "activity" or "pet_weight"). None if malformed."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    at = parse_ts(obj.get("timestamp"))
    if at is None:
        return None
    if obj.get("kind") == "pet_weight":
        return Activity(at, "Pet Weight Recorded")
    action = obj.get("action")
    if obj.get("kind") != "activity" or not isinstance(action, str):
        return None
    return Activity(at, action)


def raw_fields(record: dict[str, Any] | None) -> dict[str, Any]:
    """The raw robot fields worth comparing with the mapped state."""
    rec = record or {}
    return {k: rec.get(k) for k in RAW_FIELDS}


# =========================================================================================
# Timeline
# =========================================================================================


@dataclass(frozen=True)
class Visit:
    """A run of consecutive non-READY statuses, and the window to replay it in."""

    start: datetime  # first non-READY line
    end: datetime | None  # the line where the status returned to READY; None if unfinished
    last: datetime  # last line of the visit (end, or the log's last line when unfinished)
    window_start: datetime
    window_end: datetime
    statuses: tuple[str, ...]  # in the order first seen

    @property
    def id(self) -> str:
        return self.start.isoformat()

    @property
    def finished(self) -> bool:
        return self.end is not None

    @property
    def duration_s(self) -> float:
        return ((self.end or self.last) - self.start).total_seconds()

    @property
    def window_s(self) -> float:
        return max(0.0, (self.window_end - self.window_start).total_seconds())


@dataclass(frozen=True)
class StateAt:
    """The state line in force at some moment, with the snapshot built from it."""

    line: LogLine
    snapshot: RobotSnapshot
    observed: Observed = field(default_factory=Observed)


class Timeline:
    """Log lines ordered by ``ts``, with the derived per-line history."""

    def __init__(
        self, just_cleaned_s: float = 60.0, visit_min_s: float = DEFAULT_VISIT_MIN_S
    ) -> None:
        self.just_cleaned_s = float(just_cleaned_s)
        self.visit_min_s = float(visit_min_s)
        self.lines: list[LogLine] = []
        self.states: list[LogLine] = []
        self.activity: list[Activity] = []
        self._tracked: list[TrackerState] = []  # the tracker's state after each state line
        self._visit_starts: list[datetime] = []  # activity-based visit times, sorted
        self._visits: list[Visit] | None = None

    # --- building -------------------------------------------------------------------

    def add(self, lines: Iterable[LogLine] = (), activity: Iterable[Activity] = ()) -> None:
        lines = list(lines)
        activity = list(activity)
        if lines:
            in_order = not self.lines or min(ln.ts for ln in lines) >= self.lines[-1].ts
            self.lines.extend(lines)
            if not in_order:
                self.lines.sort(key=lambda ln: (ln.ts, ln.seq))
            else:
                lines.sort(key=lambda ln: (ln.ts, ln.seq))
                self.lines[-len(lines):] = lines
            self.states = [ln for ln in self.lines if ln.kind == "state"]
            self._derive()
        if activity:
            self.activity.extend(activity)
            self.activity.sort(key=lambda a: a.at)
            self._derive_activity()
        if lines or activity:
            self._visits = None

    def _derive(self) -> None:
        # The visit length that qualifies a visit is applied when the visit ends, so a change
        # of visit_min_s re-derives (see set_visit_min_s).
        out: list[TrackerState] = []
        st = TrackerState()
        for ln in self.states:
            rec = ln.record
            if not rec.get("status"):
                rec = {**rec, "status": ln.status}
            st = st.feed(rec, ln.ts, self.visit_min_s)
            out.append(st)
        self._tracked = out

    def _derive_activity(self) -> None:
        starts: list[datetime] = []
        last_cd: datetime | None = None
        for a in self.activity:
            if a.action.startswith("Cat Detected"):
                starts.append(a.at)
                last_cd = a.at
            elif a.action.startswith("Pet Weight Recorded") and (
                last_cd is None or a.at - last_cd > _PET_WEIGHT_SAME_VISIT
            ):
                starts.append(a.at)
        self._visit_starts = starts

    def set_visit_min_s(self, seconds: float) -> None:
        """Change how long a detection must last to count as a visit."""
        if float(seconds) != self.visit_min_s:
            self.visit_min_s = float(seconds)
            self._derive()

    def set_just_cleaned_s(self, seconds: float) -> None:
        """Change the "just cleaned" length (it sets the end of each visit's window)."""
        if float(seconds) != self.just_cleaned_s:
            self.just_cleaned_s = float(seconds)
            self._visits = None

    # --- queries --------------------------------------------------------------------

    @property
    def empty(self) -> bool:
        return not self.lines

    @property
    def newest(self) -> LogLine | None:
        return self.lines[-1] if self.lines else None

    @property
    def first_state(self) -> LogLine | None:
        return self.states[0] if self.states else None

    def _index_at(self, t: datetime) -> int | None:
        i = bisect.bisect_right(self.states, t, key=lambda ln: ln.ts) - 1
        return i if i >= 0 else None

    def _activity_visit(self, t: datetime) -> datetime | None:
        i = bisect.bisect_right(self._visit_starts, t) - 1
        return self._visit_starts[i] if i >= 0 else None

    def _state(self, i: int, moment: datetime) -> StateAt:
        ln = self.states[i]
        obs = self._tracked[i].observed(moment, self.visit_min_s)
        candidates = [d for d in (obs.last_visit_at, self._activity_visit(moment))
                      if d is not None]
        obs = replace(obs, last_visit_at=max(candidates) if candidates else None)
        snap = snapshot_from_record(
            ln.record,
            status_since=obs.status_since,
            previous_status=obs.previous_status,
            last_visit_at=obs.last_visit_at,
            visit_started_at=obs.visit_started_at,
            cat_left_at=obs.cat_left_at,
        )
        return StateAt(ln, snap, obs)

    def at(self, t: datetime) -> StateAt | None:
        """The state line in force at ``t`` (the last one at or before it), or None."""
        i = self._index_at(t)
        return None if i is None else self._state(i, t)

    def latest(self, moment: datetime | None = None) -> StateAt | None:
        """The newest state line; ``moment`` (default its own time) bounds the activity look-up."""
        if not self.states:
            return None
        i = len(self.states) - 1
        m = self.states[i].ts if moment is None else max(moment, self.states[i].ts)
        return self._state(i, m)

    def contact_error(self) -> str | None:
        """``"auth"``, ``"api"`` or ``"no_robots"`` when the spike's newest news is that it lost Whisker (an
        error line after the last state line), else None."""
        for ln in reversed(self.lines):
            if ln.kind == "state":
                return None
            if ln.kind != "error" or ln.data.get("where") not in _CONTACT_ERRORS:
                continue
            where = ln.data.get("where")
            if ln.data.get("error") == "NoRobotFound":
                return "no_robots"
            # The spike logs a rejected login at "login", and a token gone bad mid-run at
            # "poll" without a retry (it logs in again rather than retrying the poll).
            if where == "login" or (where == "poll" and ln.data.get("retry_in") is None):
                return "auth"
            return "api"
        return None

    def visits(self) -> list[Visit]:
        """Every visit, oldest first."""
        if self._visits is None:
            self._visits = self._find_visits()
        return self._visits

    def changes(self, visit: Visit) -> list[tuple[datetime, str]]:
        """``(ts, status)`` for each status change inside the visit's window, plus the status
        in force at its start."""
        out: list[tuple[datetime, str]] = []
        start = self.at(visit.window_start)
        cur = start.line.status if start else None
        if cur is not None:
            out.append((visit.window_start, cur))
        for ln in self.states:
            if ln.ts <= visit.window_start:
                continue
            if ln.ts > visit.window_end:
                break
            if ln.status != cur:
                cur = ln.status
                out.append((ln.ts, cur))
        return out

    def visit(self, visit_id: str) -> Visit | None:
        return next((v for v in self.visits() if v.id == visit_id), None)

    def _find_visits(self) -> list[Visit]:
        out: list[Visit] = []
        if not self.states:
            return out
        first_ts = self.states[0].ts
        last_ts = self.lines[-1].ts
        pad = timedelta(seconds=VISIT_PAD_S)
        tail = timedelta(seconds=self.just_cleaned_s + VISIT_PAD_S)
        start: datetime | None = None
        seen: list[str] = []
        for ln in self.states:
            s = ln.status
            if s != "READY":
                if start is None:
                    start, seen = ln.ts, []
                if s not in seen:
                    seen.append(s)
            elif start is not None:
                out.append(Visit(start, ln.ts, ln.ts, max(first_ts, start - pad), ln.ts + tail,
                                 tuple(seen)))
                start = None
        if start is not None:
            out.append(Visit(start, None, last_ts, max(first_ts, start - pad),
                             max(last_ts, start), tuple(seen)))
        return out


# =========================================================================================
# Reading and following files
# =========================================================================================


@dataclass
class _FileState:
    offset: int = 0
    size: int = 0


class RobotLog:
    """The spike's logs in one directory, followed incrementally.

    Call :meth:`refresh` to pick up new lines and files. Never writes to the directory.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        just_cleaned_s: float = 60.0,
        visit_min_s: float = DEFAULT_VISIT_MIN_S,
    ) -> None:
        self.directory = Path(directory)
        self.timeline = Timeline(just_cleaned_s, visit_min_s)
        self.skipped = 0
        self._files: dict[Path, _FileState] = {}
        self._seq = 0

    @property
    def state_files(self) -> list[Path]:
        return sorted(p for p in self._files if not p.name.startswith("spike-activity-"))

    @property
    def current_file(self) -> Path | None:
        files = self.state_files
        return files[-1] if files else None

    @property
    def has_files(self) -> bool:
        return bool(self.state_files)

    @property
    def line_count(self) -> int:
        return len(self.timeline.lines)

    def _reset(self) -> None:
        self.timeline = Timeline(self.timeline.just_cleaned_s, self.timeline.visit_min_s)
        self.skipped = 0
        self._files = {}

    def refresh(self) -> int:
        """Read whatever was appended since the last call. Returns the number of new lines."""
        try:
            found = sorted(self.directory.glob(STATE_GLOB)) + sorted(
                self.directory.glob(ACTIVITY_GLOB))
        except OSError:
            found = []
        found = [p for p in found if p.is_file()]
        # A file that shrank or vanished was replaced: start over rather than guess.
        for p, st in self._files.items():
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            if size < st.offset or p not in found:
                self._reset()
                break
        new_lines: list[LogLine] = []
        new_activity: list[Activity] = []
        for p in found:
            st = self._files.setdefault(p, _FileState())
            chunk = self._read_new(p, st)
            if not chunk:
                continue
            is_activity = p.name.startswith("spike-activity-")
            for raw in chunk.split(b"\n"):
                if not raw.strip():
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self.skipped += 1
                    continue
                if is_activity:
                    a = parse_activity(text)
                    if a is None:
                        self.skipped += 1
                    else:
                        new_activity.append(a)
                    continue
                self._seq += 1
                ln = parse_line(text, self._seq)
                if ln is None:
                    self.skipped += 1
                else:
                    new_lines.append(ln)
        if new_lines or new_activity:
            self.timeline.add(new_lines, new_activity)
        return len(new_lines) + len(new_activity)

    @staticmethod
    def _read_new(p: Path, st: _FileState) -> bytes:
        """Bytes from the remembered offset up to the last newline; advances the offset."""
        try:
            size = p.stat().st_size
            st.size = size
            if size <= st.offset:
                return b""
            with open(p, "rb") as fh:
                fh.seek(st.offset)
                data = fh.read(size - st.offset)
        except OSError:
            return b""
        cut = data.rfind(b"\n")
        if cut < 0:
            return b""  # only a half-written line so far
        st.offset += cut + 1
        return data[: cut + 1]


# =========================================================================================
# Follow and replay
# =========================================================================================


@dataclass(frozen=True)
class LogView:
    """What the log says at one moment, ready for the mapping."""

    snapshot: RobotSnapshot | None
    health: FeederHealth
    now: datetime
    line: LogLine | None = None  # the state line in force
    fresh: bool = True
    newest_ts: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def raw(self) -> dict[str, Any]:
        return raw_fields(self.line.record if self.line else None)


def is_fresh(tl: Timeline, now: datetime) -> bool:
    """True while the spike is evidently running: its newest line (any kind) is younger than
    :data:`FRESH_FOR_S` and is not its clean "exit" line."""
    newest = tl.newest
    if newest is None:
        return False
    if newest.kind == "info" and newest.data.get("event") == "exit":
        return False
    return (now - newest.ts).total_seconds() <= FRESH_FOR_S


def follow_view(tl: Timeline, now: datetime) -> LogView:
    """Live: what the real clock would show at real time ``now``.

    While the log is fresh, a quiet log means "no change" (the spike is still polling), so the
    feeder counts as in contact now. When it is not fresh, last contact is the newest line's
    time, so the mapping's own timeout produces NO DATA. An error line newer than the last
    state line sets ``health.error``.
    """
    newest = tl.newest
    fresh = is_fresh(tl, now)
    st = tl.latest(now)
    if newest is None:
        return LogView(None, FeederHealth(last_contact_at=None, ws_connected=True), now,
                       fresh=False)
    contact = now if fresh else newest.ts
    health = FeederHealth(last_contact_at=contact, ws_connected=True, error=tl.contact_error())
    return LogView(
        st.snapshot if st else None, health, now, st.line if st else None, fresh, newest.ts
    )


def replay_view(tl: Timeline, visit: Visit, t_s: float) -> LogView:
    """Replay: the moment ``t_s`` seconds into ``visit``'s window, with a healthy feeder."""
    t_s = min(max(0.0, t_s), visit.window_s)
    now = visit.window_start + timedelta(seconds=t_s)
    st = tl.at(now)
    health = FeederHealth(last_contact_at=now, ws_connected=True)
    return LogView(st.snapshot if st else None, health, now, st.line if st else None, True,
                   tl.newest.ts if tl.newest else None)
