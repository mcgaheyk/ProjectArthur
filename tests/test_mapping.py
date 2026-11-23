from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from arthur.config import WeatherConfig, default_config
from arthur.fixtures import BASE, FIXTURES, HEALTHY, NOW, ago
from arthur.mapping import to_display_state
from arthur.model import KINDS, LAYERS, Cue, DisplayState, FeederHealth, Weather
from arthur.schedule import idle_cue
from arthur.sources.tracker import Tracker

CFG = default_config()


def state(snapshot=BASE, health=HEALTHY, now=NOW):
    return to_display_state(snapshot, health, now, CFG)


# --- every status → kind ------------------------------------------------------------------

STATUS_KIND = {
    "OFFLINE": "robot_offline",
    "OFF": "robot_off",
    "READY": "ready",
    "CAT_DETECTED": "in_use",
    "CAT_SENSOR_TIMING": "waiting",
    "CAT_SENSOR_INTERRUPTED": "interrupted",
    "PAUSED": "paused",
    "CLEAN_CYCLE": "cleaning",
    "EMPTY_CYCLE": "emptying",
    "DRAWER_FULL": "drawer_full",
    "BONNET_REMOVED": "bonnet_removed",
    "PINCH_DETECT": "fault",
    "CAT_SENSOR_FAULT": "fault",
    "OVER_TORQUE_FAULT": "fault",
    "POWER_UP": "powering",
    "POWER_DOWN": "powering",
    "UNKNOWN": "fault",
}


def _as(status):
    """BASE in ``status``; in CAT_SENSOR_TIMING the cat has left (its weight came a minute
    ago), since that, not the status, is what makes waiting."""
    snap = replace(BASE, status=status, previous_status="READY")
    if status == "CAT_SENSOR_TIMING":
        snap = replace(snap, visit_started_at=ago(120), cat_left_at=ago(60))
    return snap


@pytest.mark.parametrize(("status", "kind"), sorted(STATUS_KIND.items()))
def test_every_lr4_status_maps(status, kind):
    assert state(_as(status)).kind == kind


def test_mapping_covers_every_kind():
    produced = {state(_as(s)).kind for s in STATUS_KIND}
    produced |= {"no_data", "just_cleaned"}
    assert produced == set(KINDS)


@pytest.mark.parametrize(("status", "code"), [
    ("PINCH_DETECT", "PD"), ("OVER_TORQUE_FAULT", "OTF"), ("CAT_SENSOR_FAULT", "CSF"),
])
def test_fault_codes(status, code):
    assert state(replace(BASE, status=status)).fault_code == code


def test_unknown_status_shows_raw_code():
    s = state(replace(BASE, status="UNKNOWN", status_code="ROBOT_WEIRD"))
    assert (s.kind, s.fault_code) == ("fault", "ROBOT_WEIRD")


def test_unrecognised_status_is_never_ready():
    s = state(replace(BASE, status="DRAWER_FULL_1", status_code="DF1"))
    assert (s.kind, s.fault_code) == ("fault", "DF1")


def test_powering_direction():
    assert state(replace(BASE, status="POWER_UP")).detail == "up"
    assert state(replace(BASE, status="POWER_DOWN")).detail == "down"


# --- layer priority --------------------------------------------------------------------


def test_no_data_beats_everything():
    snap = replace(BASE, status="DRAWER_FULL", is_online=False)
    assert state(snap, FeederHealth(last_contact_at=NOW, error="api")).kind == "no_data"


def test_offline_beats_needs_you_and_live():
    assert state(replace(BASE, status="DRAWER_FULL", is_online=False)).kind == "robot_offline"
    assert state(replace(BASE, status="CAT_DETECTED", is_online=False)).kind == "robot_offline"


def test_off_beats_needs_you():
    assert state(replace(BASE, status="PINCH_DETECT", is_on=False)).kind == "robot_off"


def test_offline_beats_off():
    assert state(replace(BASE, status="OFF", is_on=False, is_online=False)).kind == (
        "robot_offline"
    )


def test_layers_cover_kinds():
    assert set(LAYERS) == set(KINDS)
    assert [LAYERS[k] for k in KINDS] == sorted(LAYERS[k] for k in KINDS)


# --- no_data triggers ------------------------------------------------------------------


def test_no_snapshot_is_no_data():
    s = state(None, FeederHealth(last_contact_at=NOW))
    assert (s.kind, s.detail) == ("no_data", "no_snapshot")


@pytest.mark.parametrize("error", ["auth", "api", "no_robots"])
def test_health_error_is_no_data(error):
    s = state(BASE, FeederHealth(last_contact_at=NOW, error=error))
    assert (s.kind, s.detail) == ("no_data", error)


def test_old_contact_is_no_data():
    limit = CFG.timings.no_data_after_s
    assert state(BASE, FeederHealth(last_contact_at=ago(limit + 1))).kind == "no_data"
    assert state(BASE, FeederHealth(last_contact_at=ago(limit))).kind == "ready"


def test_never_contacted_is_no_data():
    s = state(BASE, FeederHealth(last_contact_at=None))
    assert (s.kind, s.detail) == ("no_data", "timeout")


def test_no_data_does_not_carry_laser_dirty():
    snap = replace(BASE, power_type="DC", is_laser_dirty=True)
    assert state(snap, FeederHealth(last_contact_at=NOW, error="api")).laser_dirty is False


# --- just_cleaned window -----------------------------------------------------------------


def _ready_after_clean(seconds_ago):
    return replace(BASE, status="READY", previous_status="CLEAN_CYCLE",
                   status_since=ago(seconds_ago))


def test_just_cleaned_inside_window():
    s = state(_ready_after_clean(2))
    assert s.kind == "just_cleaned"
    assert s.deadline == ago(2) + timedelta(seconds=CFG.timings.just_cleaned_s)


def test_just_cleaned_window_edges():
    w = CFG.timings.just_cleaned_s
    assert state(_ready_after_clean(0)).kind == "just_cleaned"
    assert state(_ready_after_clean(w - 1)).kind == "just_cleaned"
    assert state(_ready_after_clean(w)).kind == "ready"


def test_ready_after_something_else_is_not_just_cleaned():
    snap = replace(BASE, status="READY", previous_status="CAT_SENSOR_TIMING",
                   status_since=ago(5))
    assert state(snap).kind == "ready"


# --- live-state data -------------------------------------------------------------------


VISIT_STATUSES = ("CAT_DETECTED", "CAT_SENSOR_TIMING")


def _visit(status, *, started=90, left=None, **kw):
    """A visit that began ``started`` s ago, the weight ``left`` s ago (None: not yet)."""
    since = ago(30) if started is None else ago(started - 8)
    return replace(BASE, status=status, status_since=since, previous_status="READY",
                   visit_started_at=None if started is None else ago(started),
                   cat_left_at=None if left is None else ago(left), **kw)


@pytest.mark.parametrize("status", VISIT_STATUSES)
def test_visit_without_weight_is_in_use_from_its_start(status):
    s = state(_visit(status, started=90))
    assert (s.kind, s.since, s.deadline) == ("in_use", ago(90), None)


@pytest.mark.parametrize("status", VISIT_STATUSES)
def test_weight_makes_waiting_with_deadline_from_when_he_left(status):
    s = state(_visit(status, started=90, left=30, wait_time_minutes=8))
    assert (s.kind, s.since) == ("waiting", ago(30))
    assert s.deadline == ago(30) + timedelta(minutes=8)
    assert not s.sleeping


def test_waiting_deadline_follows_any_wait_time():
    for minutes in (3, 8, 15, 60):
        s = state(_visit("CAT_DETECTED", left=10, wait_time_minutes=minutes))
        assert s.deadline == ago(10) + timedelta(minutes=minutes)


@pytest.mark.parametrize("status", VISIT_STATUSES)
def test_sleeping_wait_has_no_deadline(status):
    s = state(_visit(status, left=30, is_sleeping=True))
    assert (s.kind, s.since, s.deadline, s.sleeping) == ("waiting", ago(30), None, True)


@pytest.mark.parametrize("status", VISIT_STATUSES)
def test_no_weight_falls_back_to_wait_without_countdown(status):
    fb = CFG.timings.cat_left_fallback_s
    assert state(_visit(status, started=fb - 1)).kind == "in_use"
    s = state(_visit(status, started=fb))
    assert (s.kind, s.deadline) == ("waiting", None)
    assert s.since == ago(fb) + timedelta(seconds=fb)


def test_fallback_uses_the_configured_time():
    cfg = replace(CFG, timings=replace(CFG.timings, cat_left_fallback_s=300))
    snap = _visit("CAT_DETECTED", started=200)
    assert to_display_state(snap, HEALTHY, NOW, cfg).kind == "in_use"
    assert to_display_state(snap, HEALTHY, NOW + timedelta(seconds=100), cfg).kind == "waiting"


@pytest.mark.parametrize("status", VISIT_STATUSES)
def test_unknown_visit_start_stays_in_use_from_status_since(status):
    # The feeder started mid-visit: no start, so no fallback; time counts from the status.
    s = state(_visit(status, started=None))
    assert (s.kind, s.since) == ("in_use", ago(30))
    snap = replace(_visit(status, started=None), status_since=ago(3600))
    assert state(snap).kind == "in_use"


def test_weight_wins_over_unknown_start():
    s = state(_visit("CAT_DETECTED", started=None, left=20))
    assert (s.kind, s.deadline) == ("waiting", ago(20) + timedelta(minutes=BASE.wait_time_minutes))


def test_cat_left_outside_a_visit_is_ignored():
    snap = replace(BASE, status="CLEAN_CYCLE", status_since=ago(30), cat_left_at=ago(500),
                   visit_started_at=ago(560))
    assert state(snap).kind == "cleaning"


def test_cleaning_cycle_estimate():
    s = state(replace(BASE, status="CLEAN_CYCLE", status_since=ago(30)))
    assert s.cycle_start == ago(30)
    assert s.cycle_end == ago(30) + timedelta(seconds=CFG.timings.clean_cycle_estimate_s)


def test_in_use_since():
    s = state(replace(BASE, status="CAT_DETECTED", status_since=ago(42)))
    assert s.since == ago(42)


def test_offline_age_is_when_we_saw_it_go_offline():
    snap = replace(BASE, status="OFFLINE", is_online=False, status_since=ago(600),
                   previous_status="READY", last_seen=ago(4 * 86400))
    s = state(snap)
    assert (s.kind, s.last_seen) == ("robot_offline", ago(600))


def test_offline_from_the_first_look_has_no_age():
    snap = replace(BASE, status="OFFLINE", is_online=False, status_since=ago(600),
                   previous_status=None, last_seen=ago(4 * 86400))
    s = state(snap)
    assert (s.kind, s.last_seen) == ("robot_offline", None)


def test_robots_last_seen_is_never_shown():
    for status in ("READY", "CAT_DETECTED", "CLEAN_CYCLE", "DRAWER_FULL"):
        assert state(replace(BASE, status=status, last_seen=ago(4 * 86400))).last_seen is None


def test_ready_carries_levels_and_visits():
    s = state()
    assert (s.drawer_pct, s.litter_pct, s.visits_today) == (42.0, 78.0, 3)
    assert s.last_visit_at == BASE.last_visit_at


# --- laser dirty (badges are gone) -------------------------------------------------------


def test_display_state_has_no_badges():
    assert "badges" not in DisplayState.__dataclass_fields__
    assert not hasattr(DisplayState(kind="ready"), "badges")
    assert DisplayState(kind="ready").laser_dirty is False


@pytest.mark.parametrize("status", ["READY", "CAT_DETECTED", "CAT_SENSOR_TIMING", "CLEAN_CYCLE",
                                    "DRAWER_FULL", "PINCH_DETECT", "OFFLINE", "OFF"])
def test_laser_dirty_is_mapped_on_every_state_with_data(status):
    assert state(replace(BASE, status=status, is_laser_dirty=True)).laser_dirty is True
    assert state(replace(BASE, status=status, is_laser_dirty=False)).laser_dirty is False


def test_power_and_websocket_change_nothing_shown():
    plain = state()
    assert state(replace(BASE, power_type="DC")) == plain
    assert state(BASE, FeederHealth(last_contact_at=ago(5), ws_connected=False)) == plain


def test_laser_dirty_fixture():
    fx = FIXTURES["ready_laser_dirty"]
    s = state(fx.snapshot, fx.health, fx.now)
    assert (s.kind, s.laser_dirty) == ("ready", True)
    assert s == replace(state(), laser_dirty=True)


# --- fixtures say what they map to -------------------------------------------------------


@pytest.mark.parametrize("name", list(FIXTURES))
def test_fixture_maps_to_its_kind(name):
    fx = FIXTURES[name]
    assert state(fx.snapshot, fx.health, fx.now).kind == fx.kind


def test_a_fixture_per_kind():
    for kind in KINDS:
        assert kind in FIXTURES and FIXTURES[kind].kind == kind


# --- the idle show: weather and cue (D-0019) ----------------------------------------------

READING = Weather(
    temp_f=41.0, code=3, is_day=True, high_f=47.0, low_f=35.0, fetched_at=ago(600)
)
PAGE_CUE = Cue(kind="page", at=ago(10), length_s=54, n=7, name="weather")


@pytest.mark.parametrize("status", sorted(STATUS_KIND))
def test_weather_and_cue_set_only_on_ready(status):
    s = to_display_state(_as(status), HEALTHY, NOW, CFG, weather=READING, cue=PAGE_CUE)
    if s.kind == "ready":
        assert (s.weather, s.cue) == (READING, PAGE_CUE)
    else:
        assert (s.weather, s.cue) == (None, None)


def test_weather_and_cue_dropped_on_no_data_and_just_cleaned():
    s = to_display_state(BASE, FeederHealth(error="auth"), NOW, CFG, weather=READING,
                         cue=PAGE_CUE)
    assert (s.kind, s.weather, s.cue) == ("no_data", None, None)
    fx = FIXTURES["just_cleaned"]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG, weather=READING, cue=PAGE_CUE)
    assert (s.kind, s.weather, s.cue) == ("just_cleaned", None, None)


def test_stale_and_future_weather_dropped():
    max_age = CFG.weather.max_age_s
    at_limit = replace(READING, fetched_at=NOW - timedelta(seconds=max_age))
    assert to_display_state(BASE, HEALTHY, NOW, CFG, weather=at_limit).weather == at_limit
    just_now = replace(READING, fetched_at=NOW)
    assert to_display_state(BASE, HEALTHY, NOW, CFG, weather=just_now).weather == just_now
    stale = replace(READING, fetched_at=NOW - timedelta(seconds=max_age + 1))
    assert to_display_state(BASE, HEALTHY, NOW, CFG, weather=stale).weather is None
    future = replace(READING, fetched_at=NOW + timedelta(seconds=1))
    assert to_display_state(BASE, HEALTHY, NOW, CFG, weather=future).weather is None


def test_weather_max_age_without_a_weather_table():
    cfg = replace(CFG, weather=None)
    default = WeatherConfig.__dataclass_fields__["max_age_s"].default
    ok = replace(READING, fetched_at=NOW - timedelta(seconds=default))
    old = replace(READING, fetched_at=NOW - timedelta(seconds=default + 1))
    assert to_display_state(BASE, HEALTHY, NOW, cfg, weather=ok).weather == ok
    assert to_display_state(BASE, HEALTHY, NOW, cfg, weather=old).weather is None


SHOW_ON = replace(CFG, idle=replace(CFG.idle, page_pct=30, scene_pct=20, quiet_from_h=0,
                                    quiet_until_h=0))


def test_ready_asks_the_schedule():
    # NOW is a round start; BASE has been ready for an hour, so the page at +45 s plays.
    t = NOW + timedelta(seconds=50)
    s = to_display_state(BASE, FeederHealth(last_contact_at=t), t, SHOW_ON)
    assert s.cue == idle_cue(BASE.status_since, t, SHOW_ON)
    assert s.cue is not None and s.cue.kind == "page" and s.cue.at == NOW + timedelta(seconds=45)


def test_page_cue_without_weather():
    t = NOW + timedelta(seconds=50)
    s = to_display_state(BASE, FeederHealth(last_contact_at=t), t, SHOW_ON, weather=None)
    assert s.weather is None and s.cue is not None and s.cue.kind == "page"


def test_explicit_cue_wins_over_the_schedule():
    scene = Cue(kind="scene", at=NOW, length_s=36, name="rat")
    # At NOW the schedule says home; at +50 s it says page. The given cue wins both times.
    for t in (NOW, NOW + timedelta(seconds=50)):
        s = to_display_state(BASE, FeederHealth(last_contact_at=t), t, SHOW_ON, cue=scene)
        assert s.cue == scene


def test_cue_kind_is_checked():
    with pytest.raises(ValueError):
        Cue(kind="home", at=NOW, length_s=10)


#: The repository config with the idle show switched off: both shares 0.
OFF_CFG = replace(CFG, idle=replace(CFG.idle, page_pct=0, scene_pct=0))


@pytest.mark.parametrize("name", list(FIXTURES))
def test_show_off_gives_every_fixture_no_cue(name):
    fx = FIXTURES[name]
    for s in range(0, 600, 3):
        now = fx.now + timedelta(seconds=s)
        assert to_display_state(fx.snapshot, fx.health, now, OFF_CFG).cue is None


@pytest.mark.parametrize("name", list(FIXTURES))
def test_repository_config_opens_every_fixture_without_a_cue(name):
    """With the show live, every fixture still maps to no cue at its own time: the show
    fixtures carry their own cue, and the rest open on the home page (or are not idle)."""
    fx = FIXTURES[name]
    assert to_display_state(fx.snapshot, fx.health, fx.now, CFG).cue is None


# --- the cleaning countdown carries on after an interruption -------------------------------

# The one interruption logged live (2026-11-21, when each change was seen, UTC).
_CLEAN = datetime(2026, 11, 21, 14, 33, 6, 878000, tzinfo=UTC)
_INTERRUPTED = datetime(2026, 11, 21, 14, 33, 46, 978000, tzinfo=UTC)
_RESUMED = datetime(2026, 11, 21, 14, 34, 12, 248000, tzinfo=UTC)
_READY = datetime(2026, 11, 21, 14, 35, 37, 378000, tzinfo=UTC)


def _real_interruption():
    """(time, snapshot) at each real change, through the tracker as the feeder sees them."""
    tr = Tracker()
    tr.feed({"status": "CAT_SENSOR_TIMING", "pet_weight": 10.6}, _CLEAN - timedelta(minutes=8))
    out = []
    for t, status in ((_CLEAN, "CLEAN_CYCLE"), (_INTERRUPTED, "CAT_SENSOR_INTERRUPTED"),
                      (_RESUMED, "CLEAN_CYCLE"), (_READY, "READY")):
        tr.feed({"status": status, "pet_weight": 10.6}, t)
        o = tr.observed(t)
        out.append((t, replace(BASE, status=status, status_since=o.status_since,
                               previous_status=o.previous_status, cycled_s=o.cycled_s,
                               last_visit_at=o.last_visit_at)))
    return out


def _at(snap, now):
    return state(snap, replace(HEALTHY, last_contact_at=now), now)


def test_resumed_cycle_counts_down_from_where_it_was():
    est = timedelta(seconds=CFG.timings.clean_cycle_estimate_s)
    (t0, first), _, (t2, resumed), (t3, ready) = _real_interruption()
    s = _at(first, t0)
    assert (s.kind, s.since, s.cycle_start, s.cycle_end) == ("cleaning", t0, t0, t0 + est)
    s = _at(resumed, t2)
    assert s.kind == "cleaning"
    assert s.since == _RESUMED  # the screen's animation is still timed from the resume
    assert s.cycle_start == _RESUMED - timedelta(seconds=40.1)
    # 130 s from the cycle's first start plus the 25.27 s it stood still.
    assert s.cycle_end == _CLEAN + est + (_RESUMED - _INTERRUPTED)
    assert (s.cycle_end - _READY).total_seconds() == pytest.approx(4.77)
    assert (s.cycle_end - t2).total_seconds() == pytest.approx(89.9)
    # just_cleaned still follows the resumed cycle.
    s = _at(ready, t3 + timedelta(seconds=1))
    assert (s.kind, s.since) == ("just_cleaned", _READY)


def test_cycled_past_the_estimate_ends_at_the_resume():
    snap = replace(BASE, status="CLEAN_CYCLE", status_since=ago(10), cycled_s=200.0)
    s = state(snap)
    assert (s.since, s.cycle_end) == (ago(10), ago(10))
    assert s.cycle_start == ago(210)


def test_nothing_cycled_is_unchanged():
    snap = replace(BASE, status="CLEAN_CYCLE", status_since=ago(30))
    assert snap.cycled_s == 0.0
    assert state(snap) == state(replace(snap, cycled_s=0.0))
    s = state(snap)
    assert (s.cycle_start, s.cycle_end) == (
        ago(30), ago(30) + timedelta(seconds=CFG.timings.clean_cycle_estimate_s))
