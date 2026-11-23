from dataclasses import replace
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import SCENARIOS
from arthur.mapping import to_display_state
from arthur.render import render

CFG = default_config()


def kinds_over_time(name, step_s=5):
    sc = SCENARIOS[name]
    out = []
    t = 0.0
    while t <= sc.duration_s:
        snap, health, now = sc.at(t)
        kind = to_display_state(snap, health, now, CFG).kind
        if not out or out[-1] != kind:
            out.append(kind)
        t += step_s
    return out


def test_normal_visit_sequence():
    assert kinds_over_time("normal_visit") == [
        "ready", "in_use", "waiting", "cleaning", "just_cleaned", "ready",
    ]


def test_visit_that_stays_detected_has_the_same_sequence():
    assert kinds_over_time("visit_stays_detected") == [
        "ready", "in_use", "waiting", "cleaning", "just_cleaned", "ready",
    ]


def test_interrupted_visit_resumes_cleaning():
    assert kinds_over_time("interrupted_visit") == [
        "ready", "in_use", "waiting", "cleaning", "interrupted", "cleaning",
        "just_cleaned", "ready",
    ]
    sc = SCENARIOS["interrupted_visit"]
    steps = [(s.label, s.at_s) for s in sc.steps]
    assert steps == [("ready", 0), ("in_use", 30), ("in_use (timing)", 38), ("waiting", 90),
                     ("cleaning", 570), ("interrupted", 620), ("cleaning", 645),
                     ("just_cleaned", 725), ("ready", 730)]
    assert sc.duration_s == 725 + 120
    est = timedelta(seconds=CFG.timings.clean_cycle_estimate_s)
    first = sc.start + timedelta(seconds=570)
    # The first stretch counts down from 2:10 as before.
    snap, health, now = sc.at(575)
    s = to_display_state(snap, health, now, CFG)
    assert (snap.cycled_s, s.cycle_start, s.cycle_end) == (0.0, first, first + est)
    # The resumed stretch carries on from where it stopped: 80 s left, ending as the robot
    # does (50 s run + 80 s), 25 s later than the first start's estimate.
    snap, health, now = sc.at(645)
    s = to_display_state(snap, health, now, CFG)
    resumed = sc.start + timedelta(seconds=645)
    assert snap.cycled_s == 50.0
    assert (s.kind, s.since) == ("cleaning", resumed)
    assert s.cycle_start == resumed - timedelta(seconds=50)
    assert s.cycle_end == sc.start + timedelta(seconds=725)
    # Every other snapshot carries nothing cycled.
    assert [st.snapshot.cycled_s for st in sc.steps] == [0, 0, 0, 0, 0, 50.0, 50.0, 0, 0]


@pytest.mark.parametrize("name", sorted(set(SCENARIOS) - {"interrupted_visit"}))
def test_other_scenarios_carry_nothing_cycled(name):
    assert all(st.snapshot is None or st.snapshot.cycled_s == 0.0
               for st in SCENARIOS[name].steps)


def test_no_weight_becomes_wait_without_countdown():
    sc = SCENARIOS["no_weight"]
    assert kinds_over_time("no_weight", step_s=1) == ["ready", "in_use", "waiting"]
    snap, health, now = sc.at(20 + CFG.timings.cat_left_fallback_s)
    s = to_display_state(snap, health, now, CFG)
    assert (s.kind, s.deadline) == ("waiting", None)
    snap, health, now = sc.at(20 + CFG.timings.cat_left_fallback_s - 1)
    assert to_display_state(snap, health, now, CFG).kind == "in_use"


def test_outage_sequence():
    assert kinds_over_time("outage") == ["ready", "no_data", "ready"]


def test_sleep_wait_has_no_countdown():
    sc = SCENARIOS["sleep_wait"]
    snap, health, now = sc.at(120)
    s = to_display_state(snap, health, now, CFG)
    assert (s.kind, s.deadline) == ("waiting", None)


@pytest.mark.parametrize("name", ["normal_visit", "visit_stays_detected"])
def test_visit_timings_are_real_shaped(name):
    sc = SCENARIOS[name]
    labels = {s.label: s.at_s for s in sc.steps}
    assert labels["waiting"] - labels["in_use"] == 60  # the weight arrives
    assert labels["cleaning"] - labels["waiting"] == 8 * 60
    assert labels["just_cleaned"] - labels["cleaning"] == 130
    assert ("in_use (timing)" in labels) == (name == "normal_visit")
    # The countdown ends when the clean cycle starts.
    snap, health, now = sc.at(labels["waiting"] + 1)
    s = to_display_state(snap, health, now, CFG)
    assert s.deadline == sc.start + timedelta(seconds=labels["cleaning"])
    # in_use counts from detection, through the CAT_SENSOR_TIMING change.
    snap, health, now = sc.at(labels["waiting"] - 1)
    s = to_display_state(snap, health, now, CFG)
    assert (s.kind, s.since) == ("in_use", sc.start + timedelta(seconds=labels["in_use"]))


SHOW_CFG = replace(CFG, idle=replace(CFG.idle, page_pct=30, scene_pct=20))


def test_idle_hour_starts_rounds_on_the_home_page_in_daytime():
    sc = SCENARIOS["idle_hour"]
    assert sc.duration_s == 3600 and sc.start.minute == sc.start.second == 0
    assert 7 <= CFG.local(sc.start).hour < 21  # not in quiet hours for the whole hour
    cues = {}
    for t in range(360):
        snap, health, now = sc.at(t)
        s = to_display_state(snap, health, now, SHOW_CFG)
        assert s.kind == "ready"
        if s.cue is not None:
            cues.setdefault((s.cue.kind, s.cue.at), t)
    starts = sorted((t, kind) for (kind, _), t in cues.items())
    assert starts == [(45, "page"), (144, "scene"), (225, "page"), (324, "scene")]
    # The repository's config plays the same show; with both shares at 0 it plays none.
    snap, health, now = sc.at(150)
    assert to_display_state(snap, health, now, CFG).cue == \
        to_display_state(snap, health, now, SHOW_CFG).cue
    off = replace(CFG, idle=replace(CFG.idle, page_pct=0, scene_pct=0))
    assert to_display_state(snap, health, now, off).cue is None


def test_a_visit_cuts_into_a_scene_at_once():
    sc = SCENARIOS["visit_mid_scene"]
    in_at = next(s.at_s for s in sc.steps if s.label == "in_use")
    snap, health, now = sc.at(in_at - 0.125)
    s = to_display_state(snap, health, now, SHOW_CFG)
    assert s.kind == "ready" and s.cue.kind == "scene"
    assert 0 < (now - s.cue.at).total_seconds() < s.cue.length_s - 5
    snap, health, now = sc.at(in_at)
    s = to_display_state(snap, health, now, SHOW_CFG)
    assert s.kind == "in_use" and s.cue is None
    assert kinds_over_time("visit_mid_scene", step_s=1) == ["ready", "in_use", "waiting"]


def test_every_scenario_renders_each_second():
    for sc in SCENARIOS.values():
        for t in range(0, int(sc.duration_s) + 1, 1):
            snap, health, now = sc.at(t)
            state = to_display_state(snap, health, now, CFG)
            render(state, now, CFG)
