"""From what a live source sees to what the pipeline takes, on CPython and MicroPython.

Shared by the live sources (:mod:`arthur.sources.whisker`, :mod:`arthur.sources.wire_source`)
and the log reader (:mod:`arthur.sources.logfile`):

- :func:`snapshot_from_record`: a :class:`~arthur.model.RobotSnapshot` from a spike record
  (the dict :func:`arthur.spikelog.build_record` and :func:`arthur.wire.record.build_record`
  make) and the tracker's observed facts.
- :func:`seeds_from_activity` and :func:`latest_weight_since`: what the robot's activity
  history says about visits, for :meth:`arthur.sources.tracker.Tracker.seed` (D-0018,
  D-0025). Activities are pylitterbot's or our client's (``arthur.wire.lr4.Activity``, whose
  ``timestamp`` is an epoch number on MicroPython).
- :func:`hhmm`: a time as HH:MM for the console.
"""

import math
import sys
from datetime import UTC, datetime

from arthur.model import RobotSnapshot
from arthur.sources.spikeformat import action_text

IS_MPY = sys.implementation.name == "micropython"


# =============================================================================================
# Records
# =============================================================================================


def parse_ts(value):
    """An ISO timestamp (``...Z`` or with an offset) as an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # the spike always writes UTC with a zone; a naive time is not ours
    return dt


def _bool(value, default):
    return value if isinstance(value, bool) else default


def _num(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    v = float(value)
    return v if math.isfinite(v) else default


def _str(value, default):
    return value if isinstance(value, str) and value else default


def snapshot_from_record(record, *, status_since=None, previous_status=None,
                         last_visit_at=None, visit_started_at=None, cat_left_at=None):
    """A :class:`RobotSnapshot` from a state line's ``record``. Odd values fall back to the
    model's defaults (the spike writes ``"<error: X>"`` for a property that raised)."""
    defaults = RobotSnapshot(status="UNKNOWN")
    wait = _num(record.get("clean_cycle_wait_time_minutes"), None)
    return RobotSnapshot(
        status=_str(record.get("status"), "UNKNOWN") or "UNKNOWN",
        status_code=_str(record.get("status_code"), None),
        is_online=_bool(record.get("is_online"), defaults.is_online),
        is_on=_bool(record.get("is_on"), defaults.is_on),
        waste_drawer_level=_num(record.get("waste_drawer_level"), defaults.waste_drawer_level),
        litter_level=_num(record.get("litter_level"), defaults.litter_level),
        litter_level_state=_str(record.get("litter_level_state"), None),
        is_sleeping=_bool(record.get("is_sleeping"), defaults.is_sleeping),
        is_laser_dirty=_bool(record.get("is_laser_dirty"), defaults.is_laser_dirty),
        power_type=_str(record.get("power_type"), defaults.power_type) or defaults.power_type,
        wait_time_minutes=int(wait) if wait is not None else defaults.wait_time_minutes,
        last_seen=parse_ts(record.get("last_seen")),
        pet_weight_lbs=_num(record.get("pet_weight"), None),
        status_since=status_since,
        previous_status=previous_status,
        last_visit_at=last_visit_at,
        visits_today=None,
        visit_started_at=visit_started_at,
        cat_left_at=cat_left_at,
    )


# =============================================================================================
# Activity history -> tracker seeds
# =============================================================================================


def activity_time(activity):
    """An activity's time as an aware datetime, or None. pylitterbot's and our client's
    activities carry a datetime on CPython; ours carry epoch seconds on MicroPython."""
    ts = getattr(activity, "timestamp", None)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else None
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts, UTC)
    return None


def _times(activities, prefix):
    out = []
    for a in activities:
        t = activity_time(a)
        if t is not None and action_text(a.action).startswith(prefix):
            out.append(t)
    out.sort()
    return out


def latest_weight_since(activities, since):
    """The newest "Pet Weight Recorded" entry at or after ``since``, or None."""
    times = [t for t in _times(activities, "Pet Weight Recorded") if t >= since]
    return times[-1] if times else None


def seeds_from_activity(activities, *, mid_visit):
    """What the activity history says about visits, as :meth:`Tracker.seed` keywords.

    ``last_visit_at`` is the newest "Cat Detected" entry. When ``mid_visit`` (the robot is in
    a visit whose start we did not see), that entry is the visit's start unless a clean cycle
    came after it (then it belongs to an earlier visit and the current one is not in the
    history yet), and the newest "Pet Weight Recorded" entry after it, if any, is when the
    cat left. These are the robot's timestamps, used only because we were not watching.
    """
    acts = list(activities)
    detected = _times(acts, "Cat Detected")
    if not detected:
        return {}
    last = detected[-1]
    out = {"last_visit_at": last}
    if mid_visit:
        cleaned_after = [t for t in _times(acts, "Clean Cycle") if t > last]
        if not cleaned_after:
            out["visit_started_at"] = last
            left = latest_weight_since(acts, last)
            if left is not None:
                out["cat_left_at"] = left
    return out


def hhmm(t, local=None):
    """``t`` as HH:MM local time, for the console. ``local`` turns an aware datetime into
    local time (``Config.local`` on the clock); without it, CPython's system zone."""
    if local is None:
        return t.astimezone().strftime("%H:%M")
    lt = local(t)
    return f"{lt.hour:02d}:{lt.minute:02d}"
