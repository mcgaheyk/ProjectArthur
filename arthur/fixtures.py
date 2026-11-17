"""Named example inputs for every display state, plus timed scenarios.

Used by the tests, the preview CLI and the simulator. Pure data: no I/O, no pylitterbot.

- :data:`FIXTURES`: name → :class:`Fixture` ``(snapshot, health, now, kind)``. There is at
  least one per display kind (the fixture named after the kind), plus extras.
- :data:`SCENARIOS`: name → :class:`Scenario`, a list of timed :class:`Step` s. A player
  advances a clock ``t`` (seconds from the start) and calls ``scenario.at(t)`` to get the
  ``(snapshot, health, now)`` to map and render.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from arthur.model import FeederHealth, RobotSnapshot

#: The fixed "now" every fixture uses (an even second, so 2-frame blinks are in their "on"
#: phase).
NOW = datetime(2026, 11, 15, 18, 0, 0, tzinfo=UTC)


def ago(seconds: float, now: datetime = NOW) -> datetime:
    return now - timedelta(seconds=seconds)


#: A healthy feeder that heard from Whisker a few seconds ago.
HEALTHY = FeederHealth(last_contact_at=ago(5), ws_connected=True, error=None)

#: An ordinary idle robot. Fixtures are variations of this.
BASE = RobotSnapshot(
    status="READY",
    status_code="RDY",
    is_online=True,
    is_on=True,
    waste_drawer_level=42.0,
    litter_level=78.0,
    litter_level_state="OPTIMAL",
    is_sleeping=False,
    is_laser_dirty=False,
    power_type="AC",
    wait_time_minutes=8,
    last_seen=ago(5),
    pet_weight_lbs=10.4,
    status_since=ago(2 * 3600),
    previous_status="CLEAN_CYCLE",
    last_visit_at=ago(2 * 3600 + 600),
    visits_today=3,
)


@dataclass(frozen=True)
class Fixture:
    name: str
    snapshot: RobotSnapshot | None
    health: FeederHealth
    now: datetime
    kind: str  # the display kind the mapping should produce
    description: str = ""


def _fx(
    name: str,
    kind: str,
    description: str,
    snapshot: RobotSnapshot | None = BASE,
    health: FeederHealth = HEALTHY,
    now: datetime = NOW,
    **changes: object,
) -> Fixture:
    snap = replace(snapshot, **changes) if (snapshot is not None and changes) else snapshot
    return Fixture(name, snap, health, now, kind, description)


_LIST: list[Fixture] = [
    # --- layer 1: trust
    _fx("no_data", "no_data", "API error: cannot reach Whisker",
        health=FeederHealth(last_contact_at=ago(400), ws_connected=False, error="api")),
    _fx("no_data_auth", "no_data", "Login failed",
        snapshot=None, health=FeederHealth(last_contact_at=None, ws_connected=False,
                                           error="auth")),
    _fx("no_data_timeout", "no_data", "No successful contact for 15 minutes",
        health=FeederHealth(last_contact_at=ago(900), ws_connected=False, error=None)),
    _fx("no_data_no_robots", "no_data", "Account has no robots",
        snapshot=None, health=FeederHealth(last_contact_at=ago(5), error="no_robots")),
    _fx("robot_offline", "robot_offline", "Robot lost Wi-Fi 25 minutes ago",
        status="OFFLINE", status_code="OFFLINE", is_online=False, status_since=ago(25 * 60),
        previous_status="READY"),
    _fx("robot_offline_unknown", "robot_offline",
        "Robot was already offline when the feeder started: no age",
        status="OFFLINE", status_code="OFFLINE", is_online=False, status_since=ago(25 * 60),
        previous_status=None),
    _fx("robot_off", "robot_off", "Robot switched off",
        status="OFF", status_code="OFF", is_on=False, status_since=ago(3600)),
    # --- layer 2: needs you
    _fx("drawer_full", "drawer_full", "Drawer full flag set",
        status="DRAWER_FULL", status_code="DFS", waste_drawer_level=100.0,
        status_since=ago(1800)),
    _fx("fault", "fault", "Pinch detected",
        status="PINCH_DETECT", status_code="PD", status_since=ago(120)),
    _fx("fault_unknown", "fault", "Unknown status: raw code shown",
        status="UNKNOWN", status_code="XYZ", status_since=ago(60)),
    _fx("bonnet_removed", "bonnet_removed", "Bonnet is off",
        status="BONNET_REMOVED", status_code="BR", status_since=ago(45)),
    # --- layer 3: live
    _fx("in_use", "in_use", "Arthur has been in for 47 s, no weight yet",
        status="CAT_DETECTED", status_code="CD", status_since=ago(47), previous_status="READY",
        visit_started_at=ago(47)),
    _fx("in_use_timing", "in_use",
        "Arthur in for 47 s; the robot already says CAT_SENSOR_TIMING, but no weight yet",
        status="CAT_SENSOR_TIMING", status_code="CST", status_since=ago(39),
        previous_status="CAT_DETECTED", visit_started_at=ago(47)),
    _fx("waiting", "waiting", "Weight came 2:18 ago (he left); 8-minute wait",
        status="CAT_SENSOR_TIMING", status_code="CST", status_since=ago(190),
        previous_status="CAT_DETECTED", visit_started_at=ago(198), cat_left_at=ago(138),
        last_visit_at=ago(198)),
    _fx("waiting_detected", "waiting",
        "Weight came 2:18 ago; the robot still says CAT_DETECTED, as it often does",
        status="CAT_DETECTED", status_code="CD", status_since=ago(198),
        previous_status="READY", visit_started_at=ago(198), cat_left_at=ago(138),
        last_visit_at=ago(198)),
    _fx("waiting_no_weight", "waiting",
        "Detected 2:30 ago and no weight came: assume he left, no countdown (WAIT)",
        status="CAT_DETECTED", status_code="CD", status_since=ago(150),
        previous_status="READY", visit_started_at=ago(150), last_visit_at=ago(150)),
    _fx("waiting_sleeping", "waiting", "Waiting while in sleep mode: no countdown",
        status="CAT_SENSOR_TIMING", status_code="CST", status_since=ago(190), is_sleeping=True,
        previous_status="CAT_DETECTED", visit_started_at=ago(198), cat_left_at=ago(138),
        last_visit_at=ago(198)),
    _fx("cleaning", "cleaning", "Clean cycle 60 s in (of ~130 s)",
        status="CLEAN_CYCLE", status_code="CCP", status_since=ago(60),
        previous_status="CAT_SENSOR_TIMING"),
    _fx("interrupted", "interrupted", "Arthur has just stepped back in mid-cycle",
        status="CAT_SENSOR_INTERRUPTED", status_code="CSI", status_since=ago(0),
        previous_status="CLEAN_CYCLE"),
    _fx("paused", "paused", "Cycle paused",
        status="PAUSED", status_code="P", status_since=ago(30), previous_status="CLEAN_CYCLE"),
    _fx("emptying", "emptying", "Empty cycle started by a person",
        status="EMPTY_CYCLE", status_code="EC", status_since=ago(20), previous_status="READY"),
    _fx("powering", "powering", "Powering up",
        status="POWER_UP", status_code="PWRU", status_since=ago(10), previous_status="OFF"),
    _fx("powering_down", "powering", "Powering down",
        status="POWER_DOWN", status_code="PWRD", status_since=ago(5), previous_status="READY"),
    _fx("just_cleaned", "just_cleaned", "Ready 2 s after a clean cycle",
        status="READY", status_code="RDY", status_since=ago(2), previous_status="CLEAN_CYCLE",
        waste_drawer_level=44.0),
    # --- layer 4: idle
    _fx("ready", "ready", "Idle, drawer 42%, litter OPTIMAL (green)",
        litter_level_state="OPTIMAL"),
    _fx("ready_refill", "ready", "Idle, litter 55% and the robot says REFILL (yellow)",
        litter_level=55.0, litter_level_state="REFILL"),
    _fx("ready_litter_low", "ready", "Idle, litter 35% and the robot says LOW (red)",
        litter_level=35.0, litter_level_state="LOW"),
    _fx("ready_overfill", "ready", "Idle, litter 100% and the robot says OVERFILL (yellow)",
        litter_level=100.0, litter_level_state="OVERFILL"),
    _fx("ready_drawer_high", "ready", "Idle, drawer 85% (red), litter OPTIMAL",
        waste_drawer_level=85.0, litter_level_state="OPTIMAL"),
    _fx("ready_laser_dirty", "ready", "Idle, the robot says the laser is dirty",
        is_laser_dirty=True),
]

FIXTURES: dict[str, Fixture] = {f.name: f for f in _LIST}


# =========================================================================================
# Scenarios
# =========================================================================================


@dataclass(frozen=True)
class Step:
    """From ``at_s`` seconds after the scenario start, the source reports ``snapshot``.

    ``health`` None means "healthy, and the feeder heard from Whisker just now" — the player
    gets ``last_contact_at == now``. Otherwise the given health is used as-is.
    """

    at_s: float
    label: str
    snapshot: RobotSnapshot | None
    health: FeederHealth | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    start: datetime
    steps: tuple[Step, ...]
    duration_s: float

    def step_at(self, t: float) -> Step:
        """The step in force ``t`` seconds after the start."""
        current = self.steps[0]
        for s in self.steps:
            if s.at_s <= t:
                current = s
            else:
                break
        return current

    def at(self, t: float) -> tuple[RobotSnapshot | None, FeederHealth, datetime]:
        """``(snapshot, health, now)`` at ``t`` seconds after the start."""
        now = self.start + timedelta(seconds=t)
        step = self.step_at(t)
        health = step.health or FeederHealth(last_contact_at=now, ws_connected=True)
        return step.snapshot, health, now


SCENARIO_START = datetime(2026, 11, 15, 9, 0, 0, tzinfo=UTC)


def _t(s: float) -> datetime:
    return SCENARIO_START + timedelta(seconds=s)


def _visit(
    name: str,
    description: str,
    *,
    timing_after_s: float | None,
    interrupted: bool = False,
) -> Scenario:
    """A visit shaped like the real ones in the spike's log.

    Detection; the status either goes CAT_SENSOR_TIMING ``timing_after_s`` later (while he is
    still inside) or, with None, stays CAT_DETECTED throughout; the pet weight changes 60 s
    after detection, which is when he has left; the robot waits its 8 minutes from then;
    a 130 s clean cycle. Interrupted: he steps back in 50 s into the cycle and it resumes
    cleaning 25 s later, as the two interruptions in the robot's history did.
    """
    base = replace(
        BASE,
        status_since=_t(-3 * 3600),
        last_visit_at=_t(-3 * 3600 - 600),
        last_seen=_t(0),
        visits_today=2,
        waste_drawer_level=42.0,
        wait_time_minutes=8,
    )
    steps: list[Step] = [Step(0, "ready", base)]

    def add(at: float, label: str, status: str | None = None, code: str | None = None,
            **kw: object) -> None:
        prior = steps[-1].snapshot
        assert prior is not None
        changes: dict[str, object] = {"last_seen": _t(at), **kw}
        if status is not None and status != prior.status:
            changes.update(status=status, status_code=code, status_since=_t(at),
                           previous_status=prior.status)
        steps.append(Step(at, label, replace(prior, **changes)))

    in_at = 30
    add(in_at, "in_use", "CAT_DETECTED", "CD", visit_started_at=_t(in_at), cat_left_at=None)
    if timing_after_s is not None:
        add(in_at + timing_after_s, "in_use (timing)", "CAT_SENSOR_TIMING", "CST")
    out_at = in_at + 60
    add(out_at, "waiting", cat_left_at=_t(out_at), pet_weight_lbs=10.6,
        last_visit_at=_t(in_at), visits_today=3)
    clean_at = out_at + 8 * 60
    add(clean_at, "cleaning", "CLEAN_CYCLE", "CCP", visit_started_at=None, cat_left_at=None)
    if interrupted:
        int_at = clean_at + 50
        add(int_at, "interrupted", "CAT_SENSOR_INTERRUPTED", "CSI")
        clean_at = int_at + 25
        add(clean_at, "cleaning", "CLEAN_CYCLE", "CCP")
    done_at = clean_at + 130
    add(done_at, "just_cleaned", "READY", "RDY", waste_drawer_level=44.0)
    # just_cleaned turns into ready by itself after just_cleaned_s; a final step marks it.
    steps.append(Step(done_at + 5, "ready", steps[-1].snapshot))
    return Scenario(name, description, SCENARIO_START, tuple(steps), done_at + 120)


def _outage() -> Scenario:
    base = replace(BASE, status_since=_t(-3600), last_seen=_t(0))
    return Scenario(
        "outage",
        "Whisker API errors for two minutes, then recovery",
        SCENARIO_START,
        (
            Step(0, "ready", base),
            Step(30, "no_data", base,
                 FeederHealth(last_contact_at=_t(25), ws_connected=False, error="api")),
            Step(150, "ready", base),
        ),
        200,
    )


def _sleep_wait() -> Scenario:
    base = replace(BASE, status_since=_t(-3600), last_seen=_t(0), is_sleeping=True)
    in_use = replace(base, status="CAT_DETECTED", status_code="CD", status_since=_t(20),
                     previous_status="READY", visit_started_at=_t(20))
    wait = replace(in_use, status="CAT_SENSOR_TIMING", status_code="CST", status_since=_t(28),
                   previous_status="CAT_DETECTED")
    left = replace(wait, cat_left_at=_t(80), pet_weight_lbs=10.6, last_visit_at=_t(20))
    return Scenario(
        "sleep_wait",
        "A visit during sleep mode: waiting shows no countdown",
        SCENARIO_START,
        (
            Step(0, "ready", base),
            Step(20, "in_use", in_use),
            Step(28, "in_use (timing)", wait),
            Step(80, "waiting", left),
        ),
        200,
    )


def _no_weight() -> Scenario:
    base = replace(BASE, status_since=_t(-3600), last_seen=_t(0))
    in_use = replace(base, status="CAT_DETECTED", status_code="CD", status_since=_t(20),
                     previous_status="READY", visit_started_at=_t(20))
    counted = replace(in_use, last_visit_at=_t(20))  # visit_min_s (30 s) after detection
    return Scenario(
        "no_weight",
        "A visit whose weight never arrives: in use, then WAIT with no countdown from 120 s",
        SCENARIO_START,
        (
            Step(0, "ready", base),
            Step(20, "in_use", in_use),
            Step(50, "in_use (a visit now)", counted),
            Step(140, "waiting (no weight)", counted),
        ),
        240,
    )


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        _visit("normal_visit", "ready → in_use (CAT_DETECTED, CAT_SENSOR_TIMING after 8 s) → "
               "weight at 60 s: waiting 8 min → cleaning 130 s → just_cleaned → ready",
               timing_after_s=8),
        _visit("visit_stays_detected", "As a normal visit, but the robot stays CAT_DETECTED "
               "until the clean cycle, as it does in about half the real visits",
               timing_after_s=None),
        _visit("interrupted_visit", "As a normal visit, but Arthur steps back in 50 s into "
               "the cycle: cleaning → interrupted → cleaning again", timing_after_s=8,
               interrupted=True),
        _outage(),
        _sleep_wait(),
        _no_weight(),
    )
}
