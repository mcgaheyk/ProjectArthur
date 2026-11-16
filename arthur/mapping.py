"""RobotSnapshot + FeederHealth → DisplayState. Pure: knows nothing about displays or pixels.

Implements docs/states.md: the highest layer with something to say wins (trust, needs you,
live, idle), and badges are added on top.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from arthur.config import Config
from arthur.model import DisplayState, FeederHealth, RobotSnapshot

#: Our own fault set: pylitterbot has no fault classifier. Status name → short code.
#: ``UNKNOWN`` shows the raw ``status_code``.
FAULT_CODES: dict[str, str] = {
    "PINCH_DETECT": "PD",
    "OVER_TORQUE_FAULT": "OTF",
    "CAT_SENSOR_FAULT": "CSF",
}

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

LOW_LITTER_STATES = frozenset({"LOW", "EMPTY"})


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


def badges_for(snapshot: RobotSnapshot, health: FeederHealth, cfg: Config) -> frozenset[str]:
    """The badges that apply, regardless of which state is showing."""
    out = set()
    if snapshot.waste_drawer_level >= cfg.thresholds.drawer_warn_pct:
        out.add("drawer_nearly_full")
    if snapshot.litter_level_state is not None and (
        snapshot.litter_level_state.upper() in LOW_LITTER_STATES
    ):
        out.add("litter_low")
    if snapshot.is_laser_dirty:
        out.add("laser_dirty")
    if snapshot.power_type == "DC":
        out.add("on_battery")
    if not health.ws_connected:
        out.add("polling_only")
    return frozenset(out)


def _kind_and_fields(
    s: RobotSnapshot, now: datetime, cfg: Config
) -> tuple[str, dict[str, object]]:
    status = s.status
    # Layer 1: trust (no_data is decided before this).
    if not s.is_online or status == "OFFLINE":
        return "robot_offline", {}
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
    kind = LIVE_KINDS.get(status)
    if kind == "waiting":
        deadline = None
        if not s.is_sleeping and s.status_since is not None:
            deadline = s.status_since + timedelta(minutes=s.wait_time_minutes)
        return kind, {"since": s.status_since, "deadline": deadline}
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


def to_display_state(
    snapshot: RobotSnapshot | None, health: FeederHealth, now: datetime, cfg: Config
) -> DisplayState:
    """Decide what to show. Pure function of its arguments."""
    reason = _no_data_reason(snapshot, health, now, cfg)
    if reason is not None or snapshot is None:
        return DisplayState(
            kind="no_data", detail=reason or "no_snapshot", last_seen=health.last_contact_at
        )

    kind, extra = _kind_and_fields(snapshot, now, cfg)
    badges = badges_for(snapshot, health, cfg)
    if kind == "drawer_full":
        badges = badges - {"drawer_nearly_full"}  # the state already says it
    return DisplayState(
        kind=kind,
        drawer_pct=snapshot.waste_drawer_level,
        litter_pct=snapshot.litter_level,
        litter_state=snapshot.litter_level_state,
        last_seen=snapshot.last_seen,
        last_visit_at=snapshot.last_visit_at,
        visits_today=snapshot.visits_today,
        pet_weight_lbs=snapshot.pet_weight_lbs,
        sleeping=snapshot.is_sleeping,
        badges=badges,
        **extra,  # type: ignore[arg-type]
    )
