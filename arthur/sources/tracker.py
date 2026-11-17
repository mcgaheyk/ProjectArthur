"""What the feeder observes about the robot over time, beyond what one record says.

A :class:`Tracker` is fed the robot's records in order, each with the time the feeder saw it,
and holds the facts no single record carries: when the status began, the status before it,
when the visit under way began, when the cat left, and when the last real visit was.
:meth:`Tracker.observed` answers them at a given ``now``.

Pure: no I/O, no pylitterbot. The input is a plain mapping with the spike record's keys
(``status``, ``pet_weight``), so the spike-log source and the live Whisker source share it.

The rules (measured on the real robot's log, 2026-11-15 to 17):

- ``status_since`` / ``previous_status``: dated from when the change was seen (D-0015). The
  first record's status has no known start: ``status_since`` is when it was seen and
  ``previous_status`` is None.
- A visit is a run of :data:`VISIT_STATUSES`. ``status`` alone does not say whether the cat
  is still inside: some visits go to ``CAT_SENSOR_TIMING`` seconds after detection while he
  is still in, others stay ``CAT_DETECTED`` throughout.
- A visit starts when the status enters that set from outside it. When the very first record
  is already in a visit, its start is unknown (None) unless seeded.
- The cat has left when, during a visit, a record's ``pet_weight`` differs from the one
  before it. The robot counts its wait from then. A later change in the same visit moves it
  (he came back and the robot restarted its wait).
- The visit ends when the status leaves the set; its start and leaving time are cleared.
- ``last_visit_at`` becomes a visit's start only once the visit qualifies: a weight arrived,
  or it has lasted ``visit_min_s``. Detections of a few seconds with no weight (someone at
  the box, a manual clean) do not count.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

#: Statuses that mean a visit is under way.
VISIT_STATUSES: frozenset[str] = frozenset({"CAT_DETECTED", "CAT_SENSOR_TIMING"})

#: Default for ``visit_min_s`` (the config's ``[timings] visit_min_s``).
DEFAULT_VISIT_MIN_S: float = 30.0


@dataclass(frozen=True)
class Observed:
    """The observed facts at one moment, ready to put on a ``RobotSnapshot``."""

    status_since: datetime | None = None
    previous_status: str | None = None
    visit_started_at: datetime | None = None
    cat_left_at: datetime | None = None
    last_visit_at: datetime | None = None


def _weight(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


@dataclass(frozen=True)
class TrackerState:
    """Everything a :class:`Tracker` holds after some records. Immutable, so a source can keep
    one per record and answer "what was observed at time t" without replaying."""

    status: str | None = None
    status_since: datetime | None = None
    previous_status: str | None = None
    weight: float | None = None  # the last numeric pet_weight seen
    visit_started_at: datetime | None = None
    cat_left_at: datetime | None = None
    weighed: bool = False  # a weight change arrived in the visit under way
    last_visit_at: datetime | None = None  # latest qualified visit start (or seeded)

    @property
    def in_visit(self) -> bool:
        return self.status in VISIT_STATUSES

    def qualifies(self, now: datetime, visit_min_s: float) -> bool:
        """True if the visit under way counts as a visit at ``now``."""
        if not self.in_visit or self.visit_started_at is None:
            return False
        if self.weighed:
            return True
        return (now - self.visit_started_at).total_seconds() >= visit_min_s

    def observed(self, now: datetime, visit_min_s: float = DEFAULT_VISIT_MIN_S) -> Observed:
        last = self.last_visit_at
        if self.qualifies(now, visit_min_s):
            last = _later(last, self.visit_started_at)
        in_visit = self.in_visit
        return Observed(
            status_since=self.status_since,
            previous_status=self.previous_status,
            visit_started_at=self.visit_started_at if in_visit else None,
            cat_left_at=self.cat_left_at if in_visit else None,
            last_visit_at=last,
        )

    def feed(
        self, record: Mapping[str, Any], seen_at: datetime, visit_min_s: float
    ) -> TrackerState:
        """The state after seeing ``record`` at ``seen_at``."""
        raw_status = record.get("status")
        status = raw_status if isinstance(raw_status, str) and raw_status else "UNKNOWN"
        weight = _weight(record.get("pet_weight"))
        st = self
        first = st.status is None

        # Status bookkeeping.
        if first:
            st = replace(st, status=status, status_since=seen_at, previous_status=None)
            if status not in VISIT_STATUSES:
                st = replace(st, visit_started_at=None, cat_left_at=None, weighed=False)
        elif status != st.status:
            was_visit = st.status in VISIT_STATUSES
            now_visit = status in VISIT_STATUSES
            if was_visit and not now_visit:
                # The visit ends: keep it if it qualified by the time we saw it end.
                if st.qualifies(seen_at, visit_min_s):
                    st = replace(st, last_visit_at=_later(st.last_visit_at, st.visit_started_at))
                st = replace(st, visit_started_at=None, cat_left_at=None, weighed=False)
            elif now_visit and not was_visit:
                st = replace(st, visit_started_at=seen_at, cat_left_at=None, weighed=False)
            st = replace(st, previous_status=st.status, status=status, status_since=seen_at)

        # Weight: a change during a visit means the cat has left.
        if weight is not None:
            if (not first and st.in_visit and st.weight is not None
                    and weight != st.weight):
                st = replace(st, cat_left_at=seen_at, weighed=True)
            st = replace(st, weight=weight)
        return st


class Tracker:
    """Feed it ``(record, seen_at)`` in order; ask :meth:`observed` at any ``now``."""

    def __init__(self, visit_min_s: float = DEFAULT_VISIT_MIN_S) -> None:
        self.visit_min_s = float(visit_min_s)
        self.state = TrackerState()

    def feed(self, record: Mapping[str, Any], seen_at: datetime) -> TrackerState:
        self.state = self.state.feed(record, seen_at, self.visit_min_s)
        return self.state

    def seed(
        self,
        *,
        visit_started_at: datetime | None = None,
        cat_left_at: datetime | None = None,
        last_visit_at: datetime | None = None,
    ) -> None:
        """Fill in what the records cannot say, from elsewhere (the activity history at
        startup). ``visit_started_at`` and ``cat_left_at`` replace the current visit's values
        when given (a weight seen at seeding counts as the visit's weight); they are dropped
        by the next record that is not in a visit. ``last_visit_at`` moves the last visit
        later, never earlier."""
        st = self.state
        if visit_started_at is not None:
            st = replace(st, visit_started_at=visit_started_at)
        if cat_left_at is not None:
            st = replace(st, cat_left_at=cat_left_at, weighed=True)
        if last_visit_at is not None:
            st = replace(st, last_visit_at=_later(st.last_visit_at, last_visit_at))
        self.state = st

    def observed(self, now: datetime) -> Observed:
        return self.state.observed(now, self.visit_min_s)
