"""The spike-log source: parsing, timeline, follower, visits, follow and replay semantics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from logsynth import (
    T0,
    activity,
    at,
    error,
    info,
    record,
    state,
    visit_lines,
    write,
)

from arthur.config import load_config
from arthur.mapping import to_display_state
from arthur.sources.logfile import (
    FRESH_FOR_S,
    VISIT_PAD_S,
    RobotLog,
    follow_view,
    parse_line,
    replay_view,
    snapshot_from_record,
)
from arthur.spikelog import HEARTBEAT_SECONDS

CFG = load_config()
JC = CFG.timings.just_cleaned_s


def load(tmp_path, *files, just_cleaned_s=JC):
    for name, lines in files:
        write(tmp_path / name, lines)
    rl = RobotLog(tmp_path, just_cleaned_s=just_cleaned_s)
    rl.refresh()
    return rl


def kind(view):
    return to_display_state(view.snapshot, view.health, view.now, CFG).kind


# --- adapter ------------------------------------------------------------------------------


def test_snapshot_field_mapping_and_types():
    snap = snapshot_from_record(record(
        "CAT_SENSOR_TIMING", waste_drawer_level=63, litter_level=78.0, pet_weight=5.5,
        clean_cycle_wait_time_minutes=8, is_sleeping=True, power_type="DC",
        litter_level_state="LOW", is_laser_dirty=True))
    assert snap.status == "CAT_SENSOR_TIMING" and snap.status_code == "CST"
    assert snap.waste_drawer_level == 63.0 and isinstance(snap.waste_drawer_level, float)
    assert snap.litter_level == 78.0
    assert snap.wait_time_minutes == 8 and isinstance(snap.wait_time_minutes, int)
    assert snap.pet_weight_lbs == 5.5
    assert snap.is_sleeping and snap.is_laser_dirty and snap.power_type == "DC"
    assert snap.litter_level_state == "LOW"
    assert snap.last_seen == datetime(2026, 3, 1, 11, 0, tzinfo=UTC)
    assert snap.visits_today is None


def test_snapshot_tolerates_odd_values():
    snap = snapshot_from_record({"status": "READY", "waste_drawer_level": "<error: X>",
                                 "is_online": None, "last_seen": "garbage",
                                 "pet_weight": None, "clean_cycle_wait_time_minutes": True})
    assert snap.waste_drawer_level == 0.0 and snap.is_online is True
    assert snap.last_seen is None and snap.pet_weight_lbs is None
    assert snap.wait_time_minutes == 7


def test_status_since_and_previous_status(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", [
        state(0, "READY", source="start"),
        state(50, "READY", source="heartbeat"),
        state(100, "CAT_DETECTED"),
        state(110, "CAT_DETECTED", pet_weight=9.9),
        state(160, "CAT_SENSOR_TIMING"),
    ]))
    tl = rl.timeline
    s = tl.at(at(60)).snapshot
    assert s.status == "READY" and s.status_since == at(0) and s.previous_status is None
    s = tl.at(at(130)).snapshot
    assert s.status == "CAT_DETECTED" and s.status_since == at(100)
    assert s.previous_status == "READY" and s.pet_weight_lbs == 9.9
    s = tl.at(at(200)).snapshot
    assert s.status_since == at(160) and s.previous_status == "CAT_DETECTED"
    assert tl.at(at(-1)) is None


def test_last_visit_from_state_lines(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    tl = rl.timeline
    assert tl.at(at(50)).snapshot.last_visit_at is None
    assert tl.at(at(120)).snapshot.last_visit_at is None  # 20 s, no weight: not yet a visit
    assert tl.at(at(130)).snapshot.last_visit_at == at(100)  # visit_min_s
    assert tl.at(at(5000)).snapshot.last_visit_at == at(100)


def test_visit_fields_from_the_tracker(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    tl = rl.timeline
    s = tl.at(at(130)).snapshot
    assert (s.visit_started_at, s.cat_left_at) == (at(100), None)
    s = tl.at(at(200)).snapshot
    assert (s.status, s.visit_started_at, s.cat_left_at) == ("CAT_SENSOR_TIMING", at(100), at(160))
    s = tl.at(at(600)).snapshot  # cleaning: the visit is over
    assert (s.visit_started_at, s.cat_left_at) == (None, None)


def test_blips_are_not_visits(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", [
        state(0, "READY", source="start"),
        state(100, "CAT_DETECTED"),
        state(109, "READY"),  # someone at the box
        state(300, "CAT_DETECTED"),
        state(304, "CLEAN_CYCLE"),  # a manual clean
        state(430, "READY"),
    ]))
    tl = rl.timeline
    for t in (105, 200, 302, 500):
        assert tl.at(at(t)).snapshot.last_visit_at is None


def test_visit_min_s_comes_from_the_caller(tmp_path):
    write(tmp_path / "spike-20260301.jsonl", [state(0, "READY", source="start"),
                                              state(100, "CAT_DETECTED"),
                                              state(115, "READY")])
    rl = RobotLog(tmp_path, visit_min_s=10)
    rl.refresh()
    assert rl.timeline.at(at(200)).snapshot.last_visit_at == at(100)
    rl.timeline.set_visit_min_s(30)
    assert rl.timeline.at(at(200)).snapshot.last_visit_at is None


def test_replay_seek_matches_playing_through(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    tl = rl.timeline
    (v,) = tl.visits()
    played = [replay_view(tl, v, t).snapshot for t in range(int(v.window_s) + 1)]
    for t in (0, 25, 31, 75, 95, 300, 520, 670):
        assert replay_view(tl, v, t).snapshot == played[t]


def test_last_visit_falls_back_to_activity(tmp_path):
    rl = load(
        tmp_path,
        ("spike-20260301.jsonl", [state(0, "READY", source="start"),
                                   state(900, "READY", source="heartbeat")]),
        ("spike-activity-20260301.jsonl", [
            activity(-7200, "Cat Detected"),
            activity(-7140, "Pet Weight Recorded: 9.1 lbs"),  # same visit: ignored
            activity(-3600, "Pet Weight Recorded: 9.2 lbs"),  # no Cat Detected before it
            activity(-3000, "Clean Cycle Complete"),
            activity(600, "Cat Detected"),
        ]),
    )
    tl = rl.timeline
    assert tl.at(at(10)).snapshot.last_visit_at == at(-3600)
    assert tl.at(at(1000)).snapshot.last_visit_at == at(600)


# --- reader / timeline --------------------------------------------------------------------


def test_files_ordered_by_ts_and_bad_lines_counted(tmp_path):
    # File names are local dates; ordering is by ts, not by name.
    write(tmp_path / "spike-20260302.jsonl", [state(300, "CAT_DETECTED")])
    write(tmp_path / "spike-20260301.jsonl", [state(0, "READY", source="start")],
          tail="{not json\n[1,2]\n" + json.dumps({"kind": "state"}) + "\n\n"
          + json.dumps(state(200, "READY", source="heartbeat")) + "\n")
    rl = RobotLog(tmp_path)
    rl.refresh()
    assert rl.skipped == 3
    assert [ln.ts for ln in rl.timeline.lines] == [at(0), at(200), at(300)]
    assert rl.current_file.name == "spike-20260302.jsonl"
    assert rl.timeline.at(at(250)).snapshot.status == "READY"
    assert rl.timeline.at(at(301)).snapshot.status == "CAT_DETECTED"


def test_half_written_line_completed_later(tmp_path):
    path = tmp_path / "spike-20260301.jsonl"
    full = json.dumps(state(100, "CAT_DETECTED"))
    write(path, [state(0, "READY", source="start")], tail=full[:25])
    rl = RobotLog(tmp_path)
    assert rl.refresh() == 1 and rl.skipped == 0
    with open(path, "a") as fh:
        fh.write(full[25:] + "\n")
    assert rl.refresh() == 1 and rl.skipped == 0
    assert rl.timeline.newest.status == "CAT_DETECTED"


def test_empty_directory(tmp_path):
    rl = RobotLog(tmp_path / "missing")
    assert rl.refresh() == 0
    assert not rl.has_files and rl.timeline.empty and rl.timeline.visits() == []
    v = follow_view(rl.timeline, T0)
    assert v.snapshot is None and kind(v) == "no_data" and not v.fresh


# --- follower -----------------------------------------------------------------------------


def test_follower_picks_up_appended_lines_and_new_files(tmp_path):
    path = tmp_path / "spike-20260301.jsonl"
    write(path, [state(0, "READY", source="start")])
    rl = RobotLog(tmp_path)
    rl.refresh()
    size_before = path.stat().st_size
    write(path, [state(100, "CAT_DETECTED")], mode="a")
    assert rl.refresh() == 1
    assert rl._files[path].offset == path.stat().st_size > size_before  # no re-read
    assert rl.refresh() == 0
    write(tmp_path / "spike-20260302.jsonl", [state(200, "CAT_SENSOR_TIMING")])
    write(tmp_path / "spike-activity-20260302.jsonl", [activity(150, "Cat Detected")])
    assert rl.refresh() == 2
    assert rl.current_file.name == "spike-20260302.jsonl"
    assert [ln.status for ln in rl.timeline.states] == [
        "READY", "CAT_DETECTED", "CAT_SENSOR_TIMING"]
    assert rl.timeline.at(at(201)).snapshot.status_since == at(200)


# --- visits -------------------------------------------------------------------------------


def test_visit_detection_and_window(tmp_path):
    lines = visit_lines(100) + [state(2000, "CAT_DETECTED"), state(2010, "CAT_SENSOR_TIMING")]
    rl = load(tmp_path, ("spike-20260301.jsonl", lines))
    v1, v2 = rl.timeline.visits()
    assert v1.start == at(100) and v1.end == at(710) and v1.finished
    assert v1.statuses == ("CAT_DETECTED", "CAT_SENSOR_TIMING", "CLEAN_CYCLE")
    assert v1.window_start == at(100 - VISIT_PAD_S)
    assert v1.window_end == at(710 + JC + VISIT_PAD_S)
    assert v1.duration_s == 610
    # Unfinished: ends at the last line.
    assert not v2.finished and v2.end is None and v2.window_end == at(2010)
    assert v2.duration_s == 10
    assert rl.timeline.visit(v1.id) == v1


def test_visit_without_cat_sensor_timing(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100, wait=False)))
    (v,) = rl.timeline.visits()
    assert v.statuses == ("CAT_DETECTED", "CLEAN_CYCLE")
    assert [s for _, s in rl.timeline.changes(v)] == ["READY", "CAT_DETECTED", "CLEAN_CYCLE",
                                                     "READY"]


def test_visit_window_never_starts_before_the_log(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", [state(0, "CAT_DETECTED", source="start"),
                                                   state(50, "READY")]))
    (v,) = rl.timeline.visits()
    assert v.window_start == at(0)


# --- follow semantics ---------------------------------------------------------------------


def test_follow_fresh_is_healthy(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    now = at(710 + JC + HEARTBEAT_SECONDS)  # quiet, but within the freshness window
    v = follow_view(rl.timeline, now)
    assert v.fresh and v.health.last_contact_at == now and v.health.error is None
    assert kind(v) == "ready"
    assert v.raw["status"] == "READY" and "robotCycleStatus" in v.raw

    # Mid-visit, the live clock counts from the line's time to real now.
    mid = tmp_path / "mid"
    mid.mkdir()
    rl2 = load(mid, ("spike-20260301.jsonl", visit_lines(100)[:2]))
    v = follow_view(rl2.timeline, at(130))
    st = to_display_state(v.snapshot, v.health, v.now, CFG)
    assert st.kind == "in_use" and st.since == at(100)
    assert v.raw["robotStatus"] == "ROBOT_CAT_DETECT"


def test_follow_quiet_past_threshold_is_no_data(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    newest = at(710)
    now = newest + timedelta(seconds=FRESH_FOR_S + 1)
    v = follow_view(rl.timeline, now)
    assert not v.fresh and v.health.last_contact_at == newest
    st = to_display_state(v.snapshot, v.health, now, CFG)
    assert st.kind == "no_data" and st.detail == "timeout"


def test_follow_exit_line_is_not_fresh(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100) + [info(800, "exit")]))
    v = follow_view(rl.timeline, at(801))
    assert not v.fresh and v.health.last_contact_at == at(800)


@pytest.mark.parametrize(("line", "reason"), [
    (error(800, "connect", retry_in=30), "api"),
    (error(800, "poll", retry_in=120), "api"),
    (error(800, "poll", retry_in=None), "auth"),
    (error(800, "login", retry_in=None), "auth"),
])
def test_follow_error_line_is_no_data_until_next_state(tmp_path, line, reason):
    path = tmp_path / "spike-20260301.jsonl"
    write(path, visit_lines(100) + [line, info(801, "connected")])
    rl = RobotLog(tmp_path)
    rl.refresh()
    v = follow_view(rl.timeline, at(810))
    st = to_display_state(v.snapshot, v.health, at(810), CFG)
    assert st.kind == "no_data" and st.detail == reason
    write(path, [state(820, "READY", source="start")], mode="a")
    rl.refresh()
    v = follow_view(rl.timeline, at(830))
    assert v.health.error is None and kind(v) == "ready"


def test_follow_ignores_side_job_errors(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl",
                         visit_lines(100) + [error(800, "activity"), error(801, "flush")]))
    assert follow_view(rl.timeline, at(810)).health.error is None


# --- replay semantics through the real mapping ------------------------------------------------


def kinds_through(tl, visit, step=1.0):
    out = []
    t = 0.0
    while t <= visit.window_s:
        k = kind(replay_view(tl, visit, t))
        if not out or out[-1][0] != k:
            out.append((k, t))
        t += step
    return out


def test_replay_walks_the_visit_in_order(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    tl = rl.timeline
    (v,) = tl.visits()
    seq = kinds_through(tl, v)
    pad = VISIT_PAD_S
    assert seq == [
        ("ready", 0.0),
        ("in_use", pad),
        ("waiting", pad + 60),
        ("cleaning", pad + 480),
        ("just_cleaned", pad + 610),
        ("ready", pad + 610 + JC),
    ]
    # Health is healthy and "now" is log time.
    rv = replay_view(tl, v, pad + 10)
    assert rv.now == at(110) and rv.health.last_contact_at == rv.now
    assert rv.raw["status"] == "CAT_DETECTED"


def test_replay_stops_at_the_end(tmp_path):
    rl = load(tmp_path, ("spike-20260301.jsonl", visit_lines(100)))
    (v,) = rl.timeline.visits()
    end = replay_view(rl.timeline, v, v.window_s + 1000)
    assert end.now == v.window_end and kind(end) == "ready"


def test_parse_line_rejects_naive_and_missing_fields():
    assert parse_line(json.dumps({"ts": "2026-03-01T12:00:00", "kind": "info"})) is None
    assert parse_line(json.dumps({"ts": "2026-03-01T12:00:00Z"})) is None
    assert parse_line(json.dumps({"ts": "2026-03-01T12:00:00Z", "kind": "state",
                                  "record": {}})) is None
    ln = parse_line(json.dumps({"ts": "2026-03-01T12:00:00.5Z", "kind": "info"}))
    assert ln is not None and ln.ts == T0 + timedelta(seconds=0.5)
