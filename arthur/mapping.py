"""RobotSnapshot + FeederHealth → DisplayState. Pure: knows nothing about displays or pixels.

Implements docs/states.md: the highest layer with something to say wins (trust, needs you,
live, idle).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from arthur.config import Config, WeatherConfig
from arthur.model import Cue, DisplayState, FeederHealth, RobotSnapshot, Weather
from arthur.schedule import asleep_since, idle_cue, woke_at

#: Our own fault set: pylitterbot has no fault classifier. Status name → short code.
#: ``UNKNOWN`` shows the raw ``status_code``.
FAULT_CODES: dict[str, str] = {
    "PINCH_DETECT": "PD",
    "OVER_TORQUE_FAULT": "OTF",
    "CAT_SENSOR_FAULT": "CSF",
}

#: Statuses that mean a visit is under way. Which of in_use and waiting they show is decided
#: by what the feeder observed (``visit_started_at``, ``cat_left_at``), not by which of the
#: two it is: the real robot sometimes goes CAT_SENSOR_TIMING while the cat is still inside,
#: and sometimes stays CAT_DETECTED until the clean cycle.
VISIT_STATUSES: frozenset[str] = frozenset({"CAT_DETECTED", "CAT_SENSOR_TIMING"})

#: Live statuses → display kind (layer 3). READY is handled separately (just_cleaned).
LIVE_KINDS: dict[str, str] = {
    "CAT_DETECTED": "in_use",
    "CAT_SENSOR_TIMING": "waiting",
    "CLEAN_CYCLE": "cleaning",
    "CAT_SENSOR_INTERRUPTED": "interrupted",
    "PAUSED": "paused",
    "EMPTY_CYCLE": "emptying",
    "POWER_UP": "powering",
    "POWER_DOWN": "powering",
}

#: Every status name the mapping recognises. Anything else is treated as a fault with its raw
#: code, so an unexpected status is never silently shown as "ready".
KNOWN_STATUSES: frozenset[str] = frozenset(
    {"OFFLINE", "OFF", "READY", "DRAWER_FULL", "BONNET_REMOVED", "UNKNOWN"}
    | set(FAULT_CODES)
    | set(LIVE_KINDS)
)

def _no_data_reason(
    snapshot: RobotSnapshot | None, health: FeederHealth, now: datetime, cfg: Config
) -> str | None:
    if health.error is not None:
        return health.error
    if snapshot is None:
        return "no_snapshot"
    if health.last_contact_at is None:
        return "timeout"
    age = (now - health.last_contact_at).total_seconds()
    if age > cfg.timings.no_data_after_s:
        return "timeout"
    return None


def _visit_kind(s: RobotSnapshot, now: datetime, cfg: Config) -> tuple[str, dict[str, object]]:
    """in_use or waiting, from what the feeder observed during the visit."""
    if s.cat_left_at is not None:
        # The weight arrived: he has left, and the robot counts its wait from now.
        deadline = None
        if not s.is_sleeping:
            deadline = s.cat_left_at + timedelta(minutes=s.wait_time_minutes)
        return "waiting", {"since": s.cat_left_at, "deadline": deadline}
    if s.visit_started_at is not None:
        gave_up = s.visit_started_at + timedelta(seconds=cfg.timings.cat_left_fallback_s)
        if now >= gave_up:
            # No weight yet, but it is long past when it usually comes: assume he has left,
            # without a countdown since we do not know when the wait began.
            return "waiting", {"since": gave_up, "deadline": None}
    return "in_use", {"since": s.visit_started_at or s.status_since}


def _kind_and_fields(
    s: RobotSnapshot, now: datetime, cfg: Config
) -> tuple[str, dict[str, object]]:
    status = s.status
    # Layer 1: trust (no_data is decided before this).
    if not s.is_online or status == "OFFLINE":
        # When we saw it go offline; unknown if it was already offline when we first looked.
        # Never the robot's own last_seen, which has been seen frozen for days.
        seen = s.status_since if s.previous_status is not None else None
        return "robot_offline", {"last_seen": seen}
    if not s.is_on or status == "OFF":
        return "robot_off", {}

    # Layer 2: needs you.
    if status == "DRAWER_FULL":
        return "drawer_full", {"since": s.status_since}
    if status in FAULT_CODES:
        return "fault", {"since": s.status_since, "fault_code": FAULT_CODES[status]}
    if status == "UNKNOWN" or status not in KNOWN_STATUSES:
        return "fault", {"since": s.status_since, "fault_code": s.status_code or "?"}
    if status == "BONNET_REMOVED":
        return "bonnet_removed", {"since": s.status_since}

    # Layer 3: live.
    if status in VISIT_STATUSES:
        return _visit_kind(s, now, cfg)
    kind = LIVE_KINDS.get(status)
    if kind == "cleaning":
        end = None
        if s.status_since is not None:
            end = s.status_since + timedelta(seconds=cfg.timings.clean_cycle_estimate_s)
        return kind, {"since": s.status_since, "cycle_start": s.status_since, "cycle_end": end}
    if kind == "powering":
        return kind, {"since": s.status_since, "detail": "up" if status == "POWER_UP" else "down"}
    if kind is not None:
        return kind, {"since": s.status_since}

    # READY: just_cleaned within the window after leaving CLEAN_CYCLE, else idle.
    if s.previous_status == "CLEAN_CYCLE" and s.status_since is not None:
        end = s.status_since + timedelta(seconds=cfg.timings.just_cleaned_s)
        if s.status_since <= now < end:
            return "just_cleaned", {"since": s.status_since, "deadline": end}

    # Layer 4: idle.
    return "ready", {"since": s.status_since}


#: How old a weather reading may be when there is no ``[weather]`` table.
_DEFAULT_WEATHER_MAX_AGE_S: float = WeatherConfig.__dataclass_fields__["max_age_s"].default


def _fresh_weather(weather: Weather | None, now: datetime, cfg: Config) -> Weather | None:
    """``weather`` if it is at most ``max_age_s`` old; None if stale or dated in the future."""
    if weather is None:
        return None
    max_age = cfg.weather.max_age_s if cfg.weather is not None else _DEFAULT_WEATHER_MAX_AGE_S
    age = (now - weather.fetched_at).total_seconds()
    if 0 <= age <= max_age:
        return weather
    return None


def to_display_state(
    snapshot: RobotSnapshot | None,
    health: FeederHealth,
    now: datetime,
    cfg: Config,
    *,
    weather: Weather | None = None,
    cue: Cue | None = None,
) -> DisplayState:
    """Decide what to show. Pure function of its arguments.

    ``weather`` is the latest reading, if any; it is shown only on ready and only while
    fresh. ``cue`` pins the idle show's cutaway (fixtures, tests); without it ready asks the
    schedule. Ready also gets the night's ``asleep_since`` and ``woke_at`` from the schedule.
    Every other kind gets none of these.
    """
    reason = _no_data_reason(snapshot, health, now, cfg)
    if reason is not None or snapshot is None:
        return DisplayState(
            kind="no_data", detail=reason or "no_snapshot", last_seen=health.last_contact_at
        )

    kind, extra = _kind_and_fields(snapshot, now, cfg)
    if kind == "ready":
        extra["weather"] = _fresh_weather(weather, now, cfg)
        since = extra.get("since")
        extra["cue"] = cue if cue is not None else idle_cue(since, now, cfg)  # type: ignore[arg-type]
        extra["asleep_since"] = asleep_since(since, now, cfg)  # type: ignore[arg-type]
        extra["woke_at"] = woke_at(since, now, cfg)  # type: ignore[arg-type]
    return DisplayState(
        kind=kind,
        drawer_pct=snapshot.waste_drawer_level,
        litter_pct=snapshot.litter_level,
        litter_state=snapshot.litter_level_state,
        last_visit_at=snapshot.last_visit_at,
        visits_today=snapshot.visits_today,
        pet_weight_lbs=snapshot.pet_weight_lbs,
        sleeping=snapshot.is_sleeping,
        laser_dirty=snapshot.is_laser_dirty,
        **extra,  # type: ignore[arg-type]
    )
