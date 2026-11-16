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


def test_interrupted_visit_sequence():
    assert kinds_over_time("interrupted_visit") == [
        "ready", "in_use", "waiting", "cleaning", "interrupted", "waiting", "cleaning",
        "just_cleaned", "ready",
    ]


def test_outage_sequence():
    assert kinds_over_time("outage") == ["ready", "no_data", "ready"]


def test_sleep_wait_has_no_countdown():
    sc = SCENARIOS["sleep_wait"]
    snap, health, now = sc.at(120)
    s = to_display_state(snap, health, now, CFG)
    assert (s.kind, s.deadline) == ("waiting", None)


def test_normal_visit_timings():
    sc = SCENARIOS["normal_visit"]
    labels = {s.label: s.at_s for s in sc.steps}
    assert labels["waiting"] - labels["in_use"] == 90
    assert labels["cleaning"] - labels["waiting"] == 7 * 60
    assert labels["just_cleaned"] - labels["cleaning"] == 150


def test_every_scenario_renders_each_second():
    for sc in SCENARIOS.values():
        for t in range(0, int(sc.duration_s) + 1, 1):
            snap, health, now = sc.at(t)
            state = to_display_state(snap, health, now, CFG)
            render(state, now, CFG)
