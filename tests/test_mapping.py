from dataclasses import replace
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import BASE, FIXTURES, HEALTHY, NOW, ago
from arthur.mapping import to_display_state
from arthur.model import BADGES, KINDS, LAYERS, FeederHealth

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


@pytest.mark.parametrize(("status", "kind"), sorted(STATUS_KIND.items()))
def test_every_lr4_status_maps(status, kind):
    snap = replace(BASE, status=status, previous_status="READY")
    assert state(snap).kind == kind


def test_mapping_covers_every_kind():
    produced = {state(replace(BASE, status=s, previous_status="READY")).kind
                for s in STATUS_KIND}
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


def test_no_data_has_no_badges():
    snap = replace(BASE, power_type="DC", is_laser_dirty=True)
    assert state(snap, FeederHealth(last_contact_at=NOW, error="api")).badges == frozenset()


# --- just_cleaned window -----------------------------------------------------------------


def _ready_after_clean(seconds_ago):
    return replace(BASE, status="READY", previous_status="CLEAN_CYCLE",
                   status_since=ago(seconds_ago))


def test_just_cleaned_inside_window():
    s = state(_ready_after_clean(10))
    assert s.kind == "just_cleaned"
    assert s.deadline == ago(10) + timedelta(seconds=CFG.timings.just_cleaned_s)


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


def test_waiting_deadline_from_wait_time():
    snap = replace(BASE, status="CAT_SENSOR_TIMING", status_since=ago(60), wait_time_minutes=15)
    s = state(snap)
    assert s.kind == "waiting"
    assert s.deadline == ago(60) + timedelta(minutes=15)
    assert not s.sleeping


def test_sleeping_wait_has_no_deadline():
    snap = replace(BASE, status="CAT_SENSOR_TIMING", status_since=ago(60), is_sleeping=True)
    s = state(snap)
    assert (s.kind, s.deadline, s.sleeping) == ("waiting", None, True)


def test_cleaning_cycle_estimate():
    s = state(replace(BASE, status="CLEAN_CYCLE", status_since=ago(30)))
    assert s.cycle_start == ago(30)
    assert s.cycle_end == ago(30) + timedelta(seconds=CFG.timings.clean_cycle_estimate_s)


def test_in_use_since():
    s = state(replace(BASE, status="CAT_DETECTED", status_since=ago(42)))
    assert s.since == ago(42)


def test_offline_carries_last_seen():
    s = state(replace(BASE, status="OFFLINE", is_online=False, last_seen=ago(600)))
    assert s.last_seen == ago(600)


def test_ready_carries_levels_and_visits():
    s = state()
    assert (s.drawer_pct, s.litter_pct, s.visits_today) == (42.0, 78.0, 3)
    assert s.last_visit_at == BASE.last_visit_at


# --- badges ----------------------------------------------------------------------------


@pytest.mark.parametrize(("changes", "health", "badge"), [
    ({"waste_drawer_level": 80.0}, HEALTHY, "drawer_nearly_full"),
    ({"litter_level_state": "LOW"}, HEALTHY, "litter_low"),
    ({"litter_level_state": "EMPTY"}, HEALTHY, "litter_low"),
    ({"is_laser_dirty": True}, HEALTHY, "laser_dirty"),
    ({"power_type": "DC"}, HEALTHY, "on_battery"),
    ({}, FeederHealth(last_contact_at=NOW, ws_connected=False), "polling_only"),
])
def test_each_badge(changes, health, badge):
    assert state(replace(BASE, **changes), health).badges == frozenset({badge})


def test_no_badges_when_all_is_well():
    assert state().badges == frozenset()
    assert state(replace(BASE, waste_drawer_level=79.9, litter_level_state="REFILL",
                         power_type="NC")).badges == frozenset()


def test_badges_ride_on_live_states():
    s = state(replace(BASE, status="CAT_DETECTED", power_type="DC"))
    assert (s.kind, s.badges) == ("in_use", frozenset({"on_battery"}))


def test_drawer_full_drops_redundant_badge():
    s = state(replace(BASE, status="DRAWER_FULL", waste_drawer_level=100.0, power_type="DC"))
    assert s.badges == frozenset({"on_battery"})


def test_all_badges_fixture():
    fx = FIXTURES["ready_badges"]
    assert state(fx.snapshot, fx.health, fx.now).badges == frozenset(BADGES)


# --- fixtures say what they map to -------------------------------------------------------


@pytest.mark.parametrize("name", list(FIXTURES))
def test_fixture_maps_to_its_kind(name):
    fx = FIXTURES[name]
    assert state(fx.snapshot, fx.health, fx.now).kind == fx.kind


def test_a_fixture_per_kind():
    for kind in KINDS:
        assert kind in FIXTURES and FIXTURES[kind].kind == kind
