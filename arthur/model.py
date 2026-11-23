"""Plain data passed between pipeline stages. No third-party imports.

``RobotSnapshot`` is what the source (pylitterbot, later) reports, flattened to plain values.
``FeederHealth`` is how the feeder's own connection is doing. ``DisplayState`` is what the
mapping decides to show; the renderer turns it into pixels.

All datetimes should be timezone-aware UTC.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arthur.compat import record

if TYPE_CHECKING:
    from datetime import datetime

#: Every display-state id, in layer order (see docs/states.md). ``stale`` is not one: the clock
#: draws it.
KINDS: tuple[str, ...] = (
    # layer 1: trust
    "no_data",
    "robot_offline",
    "robot_off",
    # layer 2: needs you
    "drawer_full",
    "fault",
    "bonnet_removed",
    # layer 3: live
    "in_use",
    "waiting",
    "cleaning",
    "interrupted",
    "paused",
    "emptying",
    "powering",
    "just_cleaned",
    # layer 4: idle
    "ready",
)

LAYERS: dict[str, int] = dict(
    [(k, 1) for k in ("no_data", "robot_offline", "robot_off")]
    + [(k, 2) for k in ("drawer_full", "fault", "bonnet_removed")]
    + [
        (k, 3)
        for k in (
            "in_use",
            "waiting",
            "cleaning",
            "interrupted",
            "paused",
            "emptying",
            "powering",
            "just_cleaned",
        )
    ]
    + [("ready", 4)]
)

#: Values of ``FeederHealth.error``.
HEALTH_ERRORS: tuple[str, ...] = ("auth", "api", "no_robots")


@record(
    "status status_code is_online is_on waste_drawer_level litter_level litter_level_state"
    " is_sleeping is_laser_dirty power_type wait_time_minutes last_seen pet_weight_lbs"
    " status_since previous_status last_visit_at visits_today visit_started_at cat_left_at"
    " cycled_s"
)
class RobotSnapshot:
    """Library-agnostic facts about the robot at one moment.

    ``status`` is the NAME of pylitterbot's ``LitterBoxStatus`` (e.g. ``"CAT_DETECTED"``);
    ``status_code`` is the raw code (e.g. ``"CD"``, or the raw ``robotStatus`` when the status
    is ``UNKNOWN``). The fields from ``status_since`` on are observed by the feeder, not reported
    by the robot. ``last_seen`` is the robot's own and has been seen frozen for days: never
    show it.
    """

    status: str
    status_code: str | None = None
    is_online: bool = True
    is_on: bool = True
    waste_drawer_level: float = 0.0
    litter_level: float = 100.0
    litter_level_state: str | None = None  # OVERFILL, OPTIMAL, REFILL, LOW, EMPTY
    is_sleeping: bool = False
    is_laser_dirty: bool = False
    power_type: str = "AC"  # "AC", "DC" or "NC"
    # clean_cycle_wait_time_minutes, whatever the robot reports. The docs list 3, 7, 15, 25
    # and 30, but a real LR4 has reported 8 and waited 8 minutes.
    wait_time_minutes: int = 7
    last_seen: datetime | None = None
    pet_weight_lbs: float | None = None
    # Feeder-observed timing:
    status_since: datetime | None = None  # when the current status began
    previous_status: str | None = None  # status before the current one
    last_visit_at: datetime | None = None
    visits_today: int | None = None
    # The visit under way (status CAT_DETECTED or CAT_SENSOR_TIMING): when it began (None
    # when unknown, e.g. the feeder started mid-visit) and when the cat left, which is when
    # the pet-weight update arrived (None until then). See arthur/sources/tracker.py.
    visit_started_at: datetime | None = None
    cat_left_at: datetime | None = None
    # Seconds of CLEAN_CYCLE already run in the cycle under way before the current status
    # began: nonzero only when the cycle resumed after CAT_SENSOR_INTERRUPTED, so the cleaning
    # countdown carries on from where it was. See tracker.cycled_after.
    cycled_s: float = 0.0


@record("last_contact_at ws_connected error")
class FeederHealth:
    """The feeder's own view of its connection to Whisker."""

    last_contact_at: datetime | None = None  # last successful data from Whisker
    ws_connected: bool = True
    error: str | None = None  # None, "auth", "api" or "no_robots"


#: Values of ``Cue.kind``.
CUE_KINDS: tuple[str, ...] = ("page", "scene")


@record("temp_f code is_day high_f low_f fetched_at")
class Weather:
    """One weather reading for the idle show's page (D-0019). The temperatures are in the
    config's ``[weather] units``, Fahrenheit unless it says Celsius; the ``_f`` names are
    from before that setting."""

    temp_f: float
    code: int  # WMO weather code
    is_day: bool
    high_f: float | None
    low_f: float | None
    fetched_at: datetime  # aware UTC


@record("kind at length_s n name")
class Cue:
    """A cutaway from the idle home page that the renderer should play (D-0019).

    ``at`` is when its slot began and ``length_s`` the slot's length, so the renderer can
    place any ``now`` inside it. ``n`` is the round number, which seeds which scene plays and
    its variations. ``name`` pins one page or scene by name (fixtures and tests).
    """

    kind: str  # "page" or "scene"
    at: datetime  # when its slot began, aware UTC
    length_s: float
    n: int = 0
    name: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in CUE_KINDS:
            raise ValueError(f"unknown cue kind {self.kind!r}")


@record(
    "kind drawer_pct litter_pct litter_state since deadline cycle_start cycle_end fault_code"
    " detail last_seen last_visit_at visits_today pet_weight_lbs sleeping laser_dirty weather"
    " cue asleep_since woke_at"
)
class DisplayState:
    """What to show. Pure data: time-dependent text is computed by the renderer from ``now``.

    Which fields are set depends on ``kind``:

    - ``since``: when the current status began (interrupted, paused, emptying, powering,
      just_cleaned, drawer_full, fault, bonnet_removed); in_use → when the visit began;
      waiting → when the cat left (or when we gave up waiting for the weight).
    - ``last_seen``: robot_offline → when the feeder saw it go offline (None when it was
      already offline at the first observation); no_data → the feeder's last contact.
    - ``deadline``: waiting → when the clean cycle should start (None while sleeping or when
      the cat leaving was not seen); just_cleaned → when it returns to ready.
    - ``cycle_start`` / ``cycle_end``: cleaning → when the cycle would have begun had it not
      stood still (``status_since`` less the ``cycled_s`` run before an interruption), and the
      estimated end, ``clean_cycle_estimate_s`` after that but never before the status began.
      ``since`` stays when the current status began.
    - ``fault_code``: fault → ``PD``, ``OTF``, ``CSF`` or the raw code.
    - ``detail``: extra word for the kind: powering → ``"up"``/``"down"``; no_data → the reason
      (``"auth"``, ``"api"``, ``"no_robots"``, ``"timeout"``, ``"no_snapshot"``).
    - ``laser_dirty``: the robot's ``is_laser_dirty`` (every kind but no_data).
    - ``weather``: ready → the latest weather reading if it is fresh, else None.
    - ``cue``: ready → the idle show's page or scene to play now (see arthur/schedule.py),
      None on the home page. Both are None for every other kind.
    - ``asleep_since``: ready in quiet hours → when Arthur's current sleep began, the later of
      the quiet hours' start and ``since``; None by day and for every other kind. The home
      page yawns for two seconds from it, then shows him curled up asleep.
    - ``woke_at``: ready, for a few seconds after the quiet hours end (``schedule.WAKE_S``),
      when they ended, if he was asleep then; else None. The home page wakes him from it
      (eyes open while curled, then the yawn) before the day's script takes over.
    """

    kind: str
    drawer_pct: float | None = None
    litter_pct: float | None = None
    litter_state: str | None = None
    since: datetime | None = None
    deadline: datetime | None = None
    cycle_start: datetime | None = None
    cycle_end: datetime | None = None
    fault_code: str | None = None
    detail: str | None = None
    last_seen: datetime | None = None
    last_visit_at: datetime | None = None
    visits_today: int | None = None
    pet_weight_lbs: float | None = None
    sleeping: bool = False
    laser_dirty: bool = False
    weather: Weather | None = None
    cue: Cue | None = None
    asleep_since: datetime | None = None
    woke_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown display kind {self.kind!r}")
