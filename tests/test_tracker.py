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
