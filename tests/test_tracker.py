"""The visit tracker: what the feeder observes over a run of records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from arthur.sources.tracker import Tracker, TrackerState

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def rec(status: str, weight: float | None = 9.5) -> dict:
    return {"status": status, "pet_weight": weight}


def run(*steps, visit_min_s: float = 30) -> Tracker:
    """``steps`` are ``(seconds, status, weight)``."""
    tr = Tracker(visit_min_s)
    for s, status, weight in steps:
        tr.feed(rec(status, weight), at(s))
    return tr


# --- status bookkeeping -------------------------------------------------------------------


def test_status_since_and_previous_status():
    tr = run((0, "READY", 9.5))
    o = tr.observed(at(10))
    assert (o.status_since, o.previous_status) == (at(0), None)
    tr.feed(rec("READY"), at(50))  # same status: nothing moves
    assert tr.observed(at(60)).status_since == at(0)
    tr.feed(rec("CAT_DETECTED"), at(100))
    o = tr.observed(at(110))
    assert (o.status_since, o.previous_status) == (at(100), "READY")


# --- visits -------------------------------------------------------------------------------


@pytest.mark.parametrize("second", ["CAT_DETECTED", "CAT_SENSOR_TIMING"])
def test_visit_start_and_weight_with_both_status_variants(second):
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (108, second, 9.5))
    o = tr.observed(at(120))
    assert (o.visit_started_at, o.cat_left_at) == (at(100), None)
    tr.feed(rec(second, 9.9), at(160))
    o = tr.observed(at(170))
    assert (o.visit_started_at, o.cat_left_at) == (at(100), at(160))


def test_switching_between_visit_statuses_is_one_visit():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (106, "CAT_SENSOR_TIMING", 9.5),
             (130, "CAT_DETECTED", 9.5))
    assert tr.observed(at(140)).visit_started_at == at(100)


def test_weight_moving_twice_moves_cat_left():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (160, "CAT_DETECTED", 9.9))
    assert tr.observed(at(170)).cat_left_at == at(160)
    tr.feed(rec("CAT_DETECTED", 10.2), at(400))  # he came back; the robot restarts its wait
    assert tr.observed(at(410)).cat_left_at == at(400)
    tr.feed(rec("CAT_DETECTED", 10.2), at(450))  # same weight: no change
    assert tr.observed(at(460)).cat_left_at == at(400)


def test_weight_changes_outside_a_visit_do_nothing():
    tr = run((0, "READY", 9.5), (50, "READY", 9.9), (60, "CLEAN_CYCLE", 8.0))
    o = tr.observed(at(70))
    assert (o.visit_started_at, o.cat_left_at, o.last_visit_at) == (None, None, None)
    tr.feed(rec("CAT_DETECTED", 8.0), at(100))  # same as the last weight: not left
    assert tr.observed(at(110)).cat_left_at is None


def test_non_numeric_weights_are_ignored():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", None), (110, "CAT_DETECTED", "<error>"),
             (120, "CAT_DETECTED", 9.5))
    assert tr.observed(at(130)).cat_left_at is None
    tr.feed(rec("CAT_DETECTED", 9.8), at(140))
    assert tr.observed(at(150)).cat_left_at == at(140)


def test_visit_end_clears_the_visit():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (160, "CAT_DETECTED", 9.9),
             (640, "CLEAN_CYCLE", 9.9))
    o = tr.observed(at(650))
    assert (o.visit_started_at, o.cat_left_at) == (None, None)
    assert o.last_visit_at == at(100)


def test_first_record_mid_visit_has_unknown_start():
    tr = run((0, "CAT_SENSOR_TIMING", 9.5))
    o = tr.observed(at(500))
    assert (o.visit_started_at, o.cat_left_at, o.last_visit_at) == (None, None, None)
    tr.feed(rec("CAT_SENSOR_TIMING", 9.9), at(30))  # the weight still tells us he left
    o = tr.observed(at(40))
    assert (o.visit_started_at, o.cat_left_at, o.last_visit_at) == (None, at(30), None)
    tr.feed(rec("CLEAN_CYCLE", 9.9), at(500))
    assert tr.observed(at(510)).last_visit_at is None  # its start was never known


def test_first_record_weight_is_not_a_change():
    tr = run((0, "CAT_DETECTED", 9.5))
    assert tr.observed(at(1)).cat_left_at is None


# --- last_visit_at ------------------------------------------------------------------------


def test_visit_qualifies_by_weight_at_once():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (105, "CAT_DETECTED", 9.9))
    assert tr.observed(at(106)).last_visit_at == at(100)


def test_visit_qualifies_by_duration_while_under_way():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5))
    assert tr.observed(at(129)).last_visit_at is None
    assert tr.observed(at(130)).last_visit_at == at(100)
    tr2 = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), visit_min_s=10)
    assert tr2.observed(at(110)).last_visit_at == at(100)


@pytest.mark.parametrize(("end_s", "then"), [(109, "READY"), (104, "CLEAN_CYCLE"),
                                             (114, "READY")])
def test_blips_and_manual_cleans_are_not_visits(end_s, then):
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (end_s, then, 9.5))
    for t in (end_s, end_s + 100, end_s + 10_000):
        assert tr.observed(at(t)).last_visit_at is None


def test_blip_after_a_visit_keeps_the_visit():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (160, "CAT_DETECTED", 9.9),
             (640, "CLEAN_CYCLE", 9.9), (770, "READY", 9.9),
             (2000, "CAT_DETECTED", 9.9), (2004, "CLEAN_CYCLE", 9.9))
    assert tr.observed(at(2010)).last_visit_at == at(100)


def test_long_visit_without_weight_counts_when_it_ends():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (200, "READY", 9.5))
    assert tr.observed(at(300)).last_visit_at == at(100)


# --- a gap in watching ---------------------------------------------------------------------


def test_gap_keeps_the_last_visit_and_weight_and_forgets_the_rest():
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5), (160, "CAT_DETECTED", 9.9),
             (640, "CLEAN_CYCLE", 9.9))
    tr.gap(at(700))
    st = tr.state
    assert (st.status, st.status_since, st.previous_status) == (None, None, None)
    assert (st.visit_started_at, st.cat_left_at, st.weighed) == (None, None, False)
    assert (st.last_visit_at, st.weight) == (at(100), 9.9)


@pytest.mark.parametrize("steps", [
    [(100, "CAT_DETECTED", 9.5), (105, "CAT_DETECTED", 9.9)],  # weighed
    [(100, "CAT_DETECTED", 9.5)],  # lasted 40 s by the time watching stopped
])
def test_gap_commits_a_visit_that_had_qualified(steps):
    tr = run((0, "READY", 9.5), *steps)
    tr.gap(at(140))
    tr.feed(rec("READY", 9.9), at(4000))
    assert tr.observed(at(4001)).last_visit_at == at(100)


def test_gap_drops_a_blip_under_way_when_watching_stopped():
    # 5 s into a detection when the Mac slept: it would qualify by the wake, but not by then.
    tr = run((0, "READY", 9.5), (100, "CAT_DETECTED", 9.5))
    tr.gap(at(105))
    tr.feed(rec("READY", 9.5), at(4000))
    assert tr.observed(at(4001)).last_visit_at is None


def test_after_a_gap_the_next_record_is_a_first_record():
    tr = run((0, "READY", 9.5), (640, "CLEAN_CYCLE", 9.5))
    tr.gap(at(700))
    tr.feed(rec("READY", 9.5), at(4000))
    o = tr.observed(at(4001))
    assert (o.status_since, o.previous_status) == (at(4000), None)  # not "just cleaned"
    # Asleep before a visit, awake in the middle of it: its start is unknown, and a weight
    # unlike the one before the gap does not say he left.
    tr = run((0, "READY", 9.5), (60, "CAT_DETECTED", 9.5))
    tr.gap(at(65))
    tr.feed(rec("CAT_SENSOR_TIMING", 9.9), at(4000))
    o = tr.observed(at(4001))
    assert (o.visit_started_at, o.cat_left_at, o.previous_status) == (None, None, None)
    tr.feed(rec("CAT_SENSOR_TIMING", 10.1), at(4050))  # a later change still says it
    assert tr.observed(at(4051)).cat_left_at == at(4050)


# --- seeding ------------------------------------------------------------------------------


def test_seed_before_the_first_record_mid_visit():
    tr = Tracker()
    tr.seed(visit_started_at=at(-60), cat_left_at=at(-10), last_visit_at=at(-3600))
    tr.feed(rec("CAT_SENSOR_TIMING"), at(0))
    o = tr.observed(at(5))
    assert (o.visit_started_at, o.cat_left_at, o.last_visit_at) == (at(-60), at(-10), at(-60))


def test_seed_is_dropped_when_not_in_a_visit():
    tr = Tracker()
    tr.seed(visit_started_at=at(-60), last_visit_at=at(-3600))
    tr.feed(rec("READY"), at(0))
    o = tr.observed(at(5))
    assert (o.visit_started_at, o.cat_left_at, o.last_visit_at) == (None, None, at(-3600))


def test_seed_after_records_and_last_visit_never_moves_back():
    tr = run((0, "CAT_DETECTED", 9.5))
    tr.seed(visit_started_at=at(-40))
    assert tr.observed(at(1)).last_visit_at == at(-40)  # 41 s long: a visit
    tr.seed(last_visit_at=at(-9999))
    assert tr.observed(at(1)).last_visit_at == at(-40)


def test_states_are_immutable_snapshots():
    tr = run((0, "READY", 9.5))
    before = tr.state
    tr.feed(rec("CAT_DETECTED"), at(100))
    assert isinstance(before, TrackerState) and before.status == "READY"
    assert before.observed(at(200)).visit_started_at is None


# --- a clean cycle carries on after an interruption (cycled_s) ------------------------------

# The one interruption logged live (2026-11-21, when each change was seen, UTC).
REAL_CLEAN = datetime(2026, 11, 21, 14, 33, 6, 878000, tzinfo=UTC)
REAL_INTERRUPTED = datetime(2026, 11, 21, 14, 33, 46, 978000, tzinfo=UTC)
REAL_RESUMED = datetime(2026, 11, 21, 14, 34, 12, 248000, tzinfo=UTC)
REAL_READY = datetime(2026, 11, 21, 14, 35, 37, 378000, tzinfo=UTC)


def test_real_interruption_carries_the_time_already_cycled():
    tr = Tracker()
    tr.feed(rec("CAT_SENSOR_TIMING"), REAL_CLEAN - timedelta(minutes=8))
    seen = []
    for t, status in ((REAL_CLEAN, "CLEAN_CYCLE"), (REAL_INTERRUPTED, "CAT_SENSOR_INTERRUPTED"),
                      (REAL_RESUMED, "CLEAN_CYCLE"), (REAL_READY, "READY")):
        tr.feed(rec(status), t)
        o = tr.observed(t)
        assert o.cycled_s == tr.state.cycled_s
        seen.append(o.cycled_s)
    assert seen[0] == 0.0
    assert seen[1] == pytest.approx(40.1)
    assert seen[2] == pytest.approx(40.1)
    assert seen[3] == 0.0
    assert tr.observed(REAL_READY).previous_status == "CLEAN_CYCLE"


def test_two_interruptions_in_one_cycle_add_up():
    tr = run((0, "READY", 9.5), (100, "CLEAN_CYCLE", 9.5), (130, "CAT_SENSOR_INTERRUPTED", 9.5),
             (150, "CLEAN_CYCLE", 9.5), (170, "CAT_SENSOR_INTERRUPTED", 9.5),
             (200, "CLEAN_CYCLE", 9.5))
    assert tr.observed(at(210)).cycled_s == pytest.approx(30 + 20)
    tr.feed(rec("READY"), at(260))
    assert tr.observed(at(260)).cycled_s == 0.0


@pytest.mark.parametrize("after", ["READY", "CAT_DETECTED", "CAT_SENSOR_TIMING", "PAUSED"])
def test_an_interruption_that_does_not_resume_clears_it(after):
    tr = run((0, "READY", 9.5), (100, "CLEAN_CYCLE", 9.5), (140, "CAT_SENSOR_INTERRUPTED", 9.5))
    assert tr.observed(at(150)).cycled_s == pytest.approx(40)
    tr.feed(rec(after), at(165))
    assert tr.observed(at(170)).cycled_s == 0.0
    tr.feed(rec("CLEAN_CYCLE"), at(200))  # a new cycle starts from nothing
    assert tr.observed(at(210)).cycled_s == 0.0


def test_a_pause_does_not_carry():
    tr = run((0, "READY", 9.5), (100, "CLEAN_CYCLE", 9.5), (140, "PAUSED", 9.5),
             (160, "CLEAN_CYCLE", 9.5))
    assert tr.observed(at(170)).cycled_s == 0.0


def test_a_gap_clears_it():
    tr = run((0, "READY", 9.5), (100, "CLEAN_CYCLE", 9.5), (140, "CAT_SENSOR_INTERRUPTED", 9.5))
    tr.gap(at(150))
    assert tr.state.cycled_s == 0.0
    tr.feed(rec("CLEAN_CYCLE"), at(400))  # a first record again
    o = tr.observed(at(410))
    assert (o.cycled_s, o.previous_status, o.status_since) == (0.0, None, at(400))


@pytest.mark.parametrize("status", ["CLEAN_CYCLE", "CAT_SENSOR_INTERRUPTED"])
def test_a_first_record_has_nothing_cycled(status):
    tr = run((0, status, 9.5))
    assert tr.observed(at(10)).cycled_s == 0.0
    tr.feed(rec("CLEAN_CYCLE" if status != "CLEAN_CYCLE" else "CAT_SENSOR_INTERRUPTED"), at(30))
    # From a first CAT_SENSOR_INTERRUPTED nothing was run before; from a first CLEAN_CYCLE
    # the 30 s seen are counted (its true start is unknown, so it is at least that).
    expected = 30.0 if status == "CLEAN_CYCLE" else 0.0
    assert tr.observed(at(40)).cycled_s == pytest.approx(expected)


def test_cycled_after_is_the_rule():
    from arthur.sources.tracker import CYCLE_HOLD_STATUSES, cycled_after

    assert "PAUSED" not in CYCLE_HOLD_STATUSES
    assert cycled_after("CLEAN_CYCLE", "CAT_SENSOR_INTERRUPTED", 5.0, at(0), at(12)) == 17.0
    assert cycled_after("CAT_SENSOR_INTERRUPTED", "CLEAN_CYCLE", 17.0, at(12), at(40)) == 17.0
    assert cycled_after("CLEAN_CYCLE", "READY", 17.0, at(40), at(100)) == 0.0
    assert cycled_after(None, "CLEAN_CYCLE", 0.0, None, at(0)) == 0.0
    assert cycled_after("CLEAN_CYCLE", "CAT_SENSOR_INTERRUPTED", 0.0, None, at(0)) == 0.0
