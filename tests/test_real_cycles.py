"""Regression on real data: three cycles from the spike's log of the real robot.

``tests/data/real-cycles.jsonl`` holds the state lines (``raw`` removed) from shortly before
each detection to 30 s after the return to READY, plus the line in force before that:

- 2026-11-16T04:22:50Z: a visit where the status goes CAT_SENSOR_TIMING after 6 s;
- 2026-11-16T13:19:08Z: a visit where it stays CAT_DETECTED until the clean cycle;
- 2026-11-16T22:03:51Z: a manual clean (CAT_DETECTED for 4 s, no weight).

Each is replayed through the log source and the mapping, second by second, as the studio's
Replay does.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from arthur.config import default_config
from arthur.mapping import to_display_state
from arthur.sources.logfile import RobotLog, parse_ts, replay_view

CFG = default_config()
DATA = Path(__file__).parent / "data" / "real-cycles.jsonl"
VISIT_STATUSES = ("CAT_DETECTED", "CAT_SENSOR_TIMING")
TOLERANCE_S = 10.0


@dataclass(frozen=True)
class Cycle:
    """The moments that matter in one real cycle, read straight from the lines."""

    detect: datetime
    weight: datetime | None  # the line where pet_weight changed during the visit
    clean: datetime | None  # the first CLEAN_CYCLE line (None: a blip with no cycle)
    ready: datetime  # the return to READY


def cycles_in(lines: list[dict]) -> list[Cycle]:
    out: list[Cycle] = []
    states = [ln for ln in lines if ln.get("kind") == "state"]
    i = 0
    while i < len(states):
        ln = states[i]
        if ln["record"]["status"] not in VISIT_STATUSES:
            i += 1
            continue
        detect = parse_ts(ln["ts"])
        prev_w = states[i - 1]["record"].get("pet_weight") if i else None
        weight = clean = ready = None
        j = i
        while j < len(states):
            rec = states[j]["record"]
            t = parse_ts(states[j]["ts"])
            if (rec["status"] in VISIT_STATUSES and weight is None and clean is None
                    and prev_w is not None and rec.get("pet_weight") != prev_w):
                weight = t
            if rec["status"] == "CLEAN_CYCLE" and clean is None:
                clean = t
            if rec["status"] == "READY":
                ready = t
                break
            prev_w = rec.get("pet_weight")
            j += 1
        if ready is not None:
            out.append(Cycle(detect, weight, clean, ready))
        i = j + 1
    return out


@dataclass(frozen=True)
class Result:
    cycle: Cycle
    in_use_s: int  # seconds shown as in_use
    in_use_until_weight: bool  # in_use exactly from detection until the weight line
    deadline_error_s: float | None  # deadline minus the real clean start
    due_s: int  # seconds shown waiting with the deadline passed ("DUE")
    clean_end_error_s: float | None  # estimated cycle end minus the real return to READY
    last_visit_before: datetime | None
    last_visit_after: datetime | None


def replay(rl: RobotLog, cycle: Cycle) -> Result:
    tl = rl.timeline
    visit = next(v for v in tl.visits() if v.start <= cycle.detect <= (v.end or v.last))
    in_use = due = 0
    until_weight = True
    deadline_err = clean_err = None
    end = cycle.weight or cycle.clean or cycle.ready
    before = after = None
    t = 0.0
    while t <= visit.window_s:
        view = replay_view(tl, visit, t)
        now = view.now
        s = to_display_state(view.snapshot, view.health, now, CFG)
        if now < cycle.detect:
            before = s.last_visit_at
        if cycle.detect <= now < end:
            until_weight &= s.kind == "in_use"
        if s.kind == "in_use" and cycle.detect <= now < cycle.ready:
            in_use += 1
        if cycle.weight is not None and cycle.clean and cycle.weight <= now < cycle.clean:
            assert s.kind == "waiting", (now, s)
            if s.deadline is not None:
                deadline_err = (s.deadline - cycle.clean).total_seconds()
                if now >= s.deadline:
                    due += 1
        if cycle.clean is not None and cycle.clean <= now < cycle.ready:
            assert s.kind == "cleaning", (now, s)
            clean_err = (s.cycle_end - cycle.ready).total_seconds()
        if now >= cycle.ready:
            after = s.last_visit_at
        t += 1.0
    return Result(cycle, in_use, until_weight, deadline_err, due, clean_err, before, after)


def load(directory: Path) -> RobotLog:
    rl = RobotLog(directory, just_cleaned_s=CFG.timings.just_cleaned_s,
                  visit_min_s=CFG.timings.visit_min_s)
    rl.refresh()
    return rl


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> list[Result]:
    d = tmp_path_factory.mktemp("real")
    shutil.copy(DATA, d / "spike-20261116.jsonl")
    lines = [json.loads(x) for x in DATA.read_text().splitlines() if x.strip()]
    rl = load(d)
    return [replay(rl, c) for c in cycles_in(lines)]


def test_the_data_has_the_three_cycles(results):
    assert [r.cycle.detect.isoformat()[:19] for r in results] == [
        "2026-11-16T04:22:50", "2026-11-16T13:19:08", "2026-11-16T22:03:51"]
    assert [r.cycle.weight is not None for r in results] == [True, True, False]


def test_the_data_has_no_raw_blobs():
    for line in DATA.read_text().splitlines():
        obj = json.loads(line)
        assert "raw" not in obj and "raw" not in obj.get("record", {})


@pytest.mark.parametrize("i", [0, 1])
def test_visit_is_in_use_until_the_weight_then_counts_down_to_the_clean(results, i):
    r = results[i]
    assert r.in_use_until_weight
    assert r.in_use_s == pytest.approx((r.cycle.weight - r.cycle.detect).total_seconds(), abs=1)
    assert r.deadline_error_s is not None and abs(r.deadline_error_s) <= TOLERANCE_S
    assert r.due_s <= TOLERANCE_S


@pytest.mark.parametrize("i", [0, 1, 2])
def test_cleaning_ends_near_the_real_return_to_ready(results, i):
    r = results[i]
    assert r.clean_end_error_s is not None and abs(r.clean_end_error_s) <= TOLERANCE_S


def test_visits_set_the_last_visit(results):
    for r in results[:2]:
        assert r.last_visit_after == r.cycle.detect


def test_manual_clean_does_not_change_the_last_visit(results):
    r = results[2]
    assert r.last_visit_before == results[1].cycle.detect
    assert r.last_visit_after == r.last_visit_before
