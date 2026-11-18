"""Arthur asleep at night (D-0019): the quiet-hours helpers in arthur/schedule.py, the night
fields mapping sets on ready, and the sleeping, falling-asleep and waking home page."""

import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from arthur import schedule
from arthur.config import Idle, default_config
from arthur.fixtures import BASE, FIXTURES, HEALTHY, SCENARIOS
from arthur.mapping import to_display_state
from arthur.model import Cue
from arthur.render import idle_show as show
from arthur.render import render
from arthur.render.icons import draw_icon
from arthur.render.screens import (
    BREATH_S,
    FALL_ASLEEP,
    IDLE_POSES,
    IDLE_X,
    SLEEP_POSES,
    SLEEP_Z,
    SLEEP_Z_AT,
    SLEEP_Z_SMALL,
    SLEEP_Z_SMALL_AT,
    TAIL_X,
    WAKE_SCRIPT,
    _f52,
    draw_idle_arthur,
    home_page,
    idle_pose,
    night_pose,
)
from arthur.schedule import (
    WAKE_S,
    asleep_since,
    last_quiet_end,
    quiet_window,
    woke_at,
)
from arthur.timebase import SHOW_FPS

CFG = default_config()
NY = ZoneInfo("America/New_York")
OFF = CFG.rgb("off")
PALETTE = set(CFG.palette.colors.values())
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def ny(*a: int) -> datetime:
    return datetime(*a, tzinfo=NY).astimezone(UTC)


def cfg_with(quiet_from_h=22, quiet_until_h=7, **idle):
    base = {"round_s": 180, "page_pct": 0, "scene_pct": 0,
            "quiet_from_h": quiet_from_h, "quiet_until_h": quiet_until_h, **idle}
    return replace(CFG, idle=Idle(**base), tz=NY)


NIGHT = cfg_with()


def test_repository_config_is_22_to_7_in_new_york():
    assert (CFG.idle.quiet_from_h, CFG.idle.quiet_until_h) == (22, 7)
    assert CFG.tz.key == "America/New_York"


# --- schedule: the quiet window as instants --------------------------------------------------


def test_window_across_midnight():
    want = (ny(2026, 11, 18, 22), ny(2026, 11, 19, 7))
    assert want == (datetime(2026, 11, 19, 3, tzinfo=UTC), datetime(2026, 11, 19, 12, tzinfo=UTC))
    for t in (ny(2026, 11, 18, 22), ny(2026, 11, 18, 23, 30), ny(2026, 11, 19, 0),
              ny(2026, 11, 19, 3, 15), ny(2026, 11, 19, 6, 59, 59)):
        assert quiet_window(t, NIGHT) == want, t
    for t in (ny(2026, 11, 18, 21, 59, 59), ny(2026, 11, 19, 7), ny(2026, 11, 19, 12)):
        assert quiet_window(t, NIGHT) is None, t


def test_window_on_the_fall_back_night_is_ten_hours():
    # 2026-11-01: clocks go back at 02:00 EDT. 22:00 EDT is 02:00 UTC, 07:00 EST is 12:00 UTC.
    start, end = quiet_window(ny(2026, 11, 1, 3), NIGHT)
    assert start == datetime(2026, 11, 1, 2, tzinfo=UTC)
    assert end == datetime(2026, 11, 1, 12, tzinfo=UTC)
    assert end - start == timedelta(hours=10)
    # The same window from both sides of the change, including the repeated hour.
    for utc_h in (2, 5, 6, 7, 11):
        assert quiet_window(datetime(2026, 11, 1, utc_h, 30, tzinfo=UTC), NIGHT) == (start, end)
    assert quiet_window(datetime(2026, 11, 1, 12, tzinfo=UTC), NIGHT) is None


def test_window_on_the_spring_forward_night_is_eight_hours():
    # 2027-03-14: clocks go forward at 02:00 EST. 22:00 EST is 03:00 UTC, 07:00 EDT 11:00 UTC.
    start, end = quiet_window(ny(2027, 3, 14, 4), NIGHT)
    assert start == datetime(2027, 3, 14, 3, tzinfo=UTC)
    assert end == datetime(2027, 3, 14, 11, tzinfo=UTC)
    assert end - start == timedelta(hours=8)
    for utc_h in (3, 6, 7, 8, 10):
        assert quiet_window(datetime(2027, 3, 14, utc_h, 30, tzinfo=UTC), NIGHT) == (start, end)
    assert quiet_window(datetime(2027, 3, 14, 11, tzinfo=UTC), NIGHT) is None


def test_window_that_does_not_wrap_midnight():
    cfg = cfg_with(1, 5)
    assert quiet_window(ny(2026, 11, 18, 3), cfg) == (ny(2026, 11, 18, 1), ny(2026, 11, 18, 5))
    assert quiet_window(ny(2026, 11, 18, 0, 59), cfg) is None
    assert quiet_window(ny(2026, 11, 18, 5), cfg) is None


def test_no_window_when_the_hours_are_equal():
    cfg = cfg_with(7, 7)
    for h in range(24):
        assert quiet_window(ny(2026, 11, 18, h), cfg) is None
    assert last_quiet_end(ny(2026, 11, 18, 7), cfg) is None
    assert asleep_since(LONG_AGO, ny(2026, 11, 18, 3), cfg) is None
    assert woke_at(LONG_AGO, ny(2026, 11, 18, 7), cfg) is None


def test_last_quiet_end():
    assert last_quiet_end(ny(2026, 11, 18, 7), NIGHT) == ny(2026, 11, 18, 7)
    assert last_quiet_end(ny(2026, 11, 18, 6, 59), NIGHT) == ny(2026, 11, 17, 7)
    assert last_quiet_end(ny(2026, 11, 18, 23), NIGHT) == ny(2026, 11, 18, 7)
    # Across both changes: 07:00 local is 12:00 UTC (EST) and 11:00 UTC (EDT).
    assert last_quiet_end(datetime(2026, 11, 1, 12, 0, 1, tzinfo=UTC), NIGHT) == datetime(
        2026, 11, 1, 12, tzinfo=UTC)
    assert last_quiet_end(datetime(2027, 3, 14, 11, 0, 1, tzinfo=UTC), NIGHT) == datetime(
        2027, 3, 14, 11, tzinfo=UTC)


def test_asleep_since_is_the_later_of_the_window_start_and_idle():
    t = ny(2026, 11, 19, 3)
    assert asleep_since(LONG_AGO, t, NIGHT) == ny(2026, 11, 18, 22)
    assert asleep_since(None, t, NIGHT) == ny(2026, 11, 18, 22)
    after_visit = ny(2026, 11, 19, 2, 47, 13)
    assert asleep_since(after_visit, t, NIGHT) == after_visit
    assert asleep_since(LONG_AGO, ny(2026, 11, 19, 12), NIGHT) is None


def test_woke_at_lasts_wake_s_seconds():
    end = ny(2026, 11, 19, 7)
    for s in range(WAKE_S):
        assert woke_at(LONG_AGO, end + timedelta(seconds=s + 0.5), NIGHT) == end
    assert woke_at(LONG_AGO, end + timedelta(seconds=WAKE_S), NIGHT) is None
    assert woke_at(LONG_AGO, end - timedelta(seconds=1), NIGHT) is None
    assert woke_at(None, end, NIGHT) == end
    # Idle began after the night: he was never asleep, so he does not wake.
    assert woke_at(end, end + timedelta(seconds=1), NIGHT) is None


# --- mapping ---------------------------------------------------------------------------------


def _map(snapshot, now, cfg=NIGHT):
    return to_display_state(snapshot, replace(HEALTHY, last_contact_at=now), now, cfg)


def test_mapping_sets_the_night_fields_on_ready_only():
    t = ny(2026, 11, 19, 3)
    idle = replace(BASE, status_since=ny(2026, 11, 18, 20))
    s = _map(idle, t)
    assert s.kind == "ready" and s.asleep_since == ny(2026, 11, 18, 22) and s.woke_at is None
    visit = replace(BASE, status="CAT_DETECTED", status_code="CD", status_since=t,
                    previous_status="READY", visit_started_at=t)
    s = _map(visit, t + timedelta(seconds=5))
    assert s.kind == "in_use" and s.asleep_since is None and s.woke_at is None
    # By day: neither.
    s = _map(idle, ny(2026, 11, 19, 13))
    assert s.asleep_since is None and s.woke_at is None


def test_mapping_sleep_begins_when_idle_resumes_at_night():
    resumed = ny(2026, 11, 19, 3, 2, 10)
    idle = replace(BASE, status_since=resumed, previous_status="CLEAN_CYCLE")
    s = _map(idle, resumed + timedelta(seconds=CFG.timings.just_cleaned_s + 1))
    assert s.kind == "ready" and s.asleep_since == resumed


def test_mapping_wake_field_only_for_its_seconds():
    end = ny(2026, 11, 19, 7)
    idle = replace(BASE, status_since=ny(2026, 11, 18, 20))
    for s in range(WAKE_S):
        st = _map(idle, end + timedelta(seconds=s))
        assert st.woke_at == end and st.asleep_since is None
    assert _map(idle, end + timedelta(seconds=WAKE_S)).woke_at is None
    assert _map(idle, end - timedelta(seconds=1)).woke_at is None


# --- render ----------------------------------------------------------------------------------

DAY = FIXTURES["ready"].display_state(CFG)
T0 = ny(2026, 11, 19, 3)  # asleep since T0 in the tests below
ASLEEP = replace(DAY, asleep_since=T0)


def frame_at(state, s):
    return render(state, T0 + timedelta(seconds=s), CFG, variant="arthur")


def pose_frame(pose, now=T0, state=DAY):
    return home_page(state, now, CFG, pose)


def test_wake_script_matches_the_mapping():
    assert len(WAKE_SCRIPT) == WAKE_S


def test_yawn_then_sleep_second_by_second():
    want = ["yawn", "yawn"] + ["sleep_in"] * 6 + ["sleep_out"] * 6 + ["sleep_in"] * 6
    assert [night_pose(ASLEEP, T0 + timedelta(seconds=s)) for s in range(len(want))] == want
    assert FALL_ASLEEP == ("yawn", "yawn")
    for s, pose in enumerate(want):
        assert frame_at(ASLEEP, s) == pose_frame(pose, T0 + timedelta(seconds=s)), s
    # The yawn is the seated yawn, tail and all; then he is curled up.
    assert frame_at(ASLEEP, 0) == home_page(DAY, T0, CFG, "yawn")


def test_breath_cycle_is_12_seconds_half_in_half_out():
    assert BREATH_S == 12
    poses = [night_pose(ASLEEP, T0 + timedelta(seconds=2 + s)) for s in range(10 * BREATH_S)]
    for i in range(0, len(poses), BREATH_S):
        assert poses[i:i + BREATH_S] == ["sleep_in"] * 6 + ["sleep_out"] * 6
    # Fractions of a second (the idle show's 1/8 s beat) follow the whole second.
    assert night_pose(ASLEEP, T0 + timedelta(seconds=7.875)) == "sleep_in"
    assert night_pose(ASLEEP, T0 + timedelta(seconds=8)) == "sleep_out"


def _art_cells(art, at):
    x0, y0 = at
    return [(x0 + x, y0 + y) for y, row in enumerate(art) for x, ch in enumerate(row) if ch != "."]


def test_the_zs_show_only_on_the_out_breath():
    z_cells = _art_cells(SLEEP_Z, SLEEP_Z_AT) + _art_cells(SLEEP_Z_SMALL, SLEEP_Z_SMALL_AT)
    cream = CFG.rgb("cream")
    for s in range(2, 2 + 2 * BREATH_S):
        f = frame_at(ASLEEP, s)
        lit = [f.get(x, y) == cream for x, y in z_cells]
        if night_pose(ASLEEP, T0 + timedelta(seconds=s)) == "sleep_out":
            assert all(lit), s
        else:
            assert not any(lit), s


def test_the_breath_moves_only_the_back():
    a, b = pose_frame("sleep_in"), pose_frame("sleep_out")
    z_cells = set(_art_cells(SLEEP_Z, SLEEP_Z_AT) + _art_cells(SLEEP_Z_SMALL, SLEEP_Z_SMALL_AT))
    moved = {(x, y) for x in range(52) for y in range(16)
             if a.get(x, y) != b.get(x, y) and (x, y) not in z_cells}
    assert 3 <= len(moved) <= 10  # visible, gentle
    assert all(9 <= x <= 15 and 6 <= y <= 8 for x, y in moved), moved


def test_waking_second_by_second_then_the_day_script():
    end = ny(2026, 11, 19, 7)
    waking = replace(DAY, woke_at=end)
    want = ["sleep_wake", "sleep_wake", "yawn", "yawn"]
    for s, pose in enumerate(want):
        now = end + timedelta(seconds=s)
        assert night_pose(waking, now) == pose
        assert render(waking, now, CFG, variant="arthur") == pose_frame(pose, now), s
    for s in range(len(want), 70):
        now = end + timedelta(seconds=s)
        assert night_pose(waking, now) is None
        assert render(waking, now, CFG, variant="arthur") == render(DAY, now, CFG,
                                                                     variant="arthur"), s


def test_eyes_open_is_the_curl_with_green_eyes():
    awake, out = pose_frame("sleep_wake"), pose_frame("sleep_out")
    eye = CFG.rgb("eye")
    assert any(awake.get(x, y) == eye for x in range(16) for y in range(16))
    assert not any(out.get(x, y) == eye for x in range(16) for y in range(16))


@pytest.mark.parametrize("state", [DAY, FIXTURES["ready_laser_dirty"].display_state(CFG),
                                   FIXTURES["ready_drawer_high"].display_state(CFG)],
                         ids=["ready", "laser", "drawer_high"])
def test_bars_and_bottom_line_are_the_days(state):
    night = replace(state, asleep_since=T0)
    for s in range(2, 2 + BREATH_S):
        now = T0 + timedelta(seconds=s)
        f, day = render(night, now, CFG, variant="arthur"), render(state, now, CFG,
                                                                     variant="arthur")
        for x in range(IDLE_X - 1, 52):
            for y in range(16):
                assert f.get(x, y) == day.get(x, y), (s, x, y)


def test_no_seated_tail_while_asleep():
    for pose in SLEEP_POSES:
        f = pose_frame(pose)
        assert all(f.get(x, y) == OFF for x in range(16, IDLE_X) for y in range(16)), pose
        # Not the seated pose's tail either, where it overlaps the box.
        seated = _f52()
        draw_idle_arthur(seated, CFG, "rest")
        icon_only = _f52()
        draw_icon(icon_only, IDLE_POSES["rest"][0], 0, 0, CFG, size=16)
        tail = {(x, y) for x in range(TAIL_X, 16) for y in range(16)
                if seated.get(x, y) != icon_only.get(x, y)}
        assert tail and any(f.get(x, y) != seated.get(x, y) for x, y in tail), pose


@pytest.mark.parametrize("pose", list(SLEEP_POSES))
def test_sleep_frames_are_clean(pose):
    f = render(replace(DAY, asleep_since=T0), T0 + timedelta(seconds=100), CFG,
               variant="arthur")
    for g in (f, pose_frame(pose)):
        assert all(g.get(x, y) == OFF for x in range(52) for y in (0, 15))
        assert all(g.get(x, y) == OFF for x in (0, 51) for y in range(16))
        assert set(g.pixels) <= PALETTE


def test_at_most_12_changed_frames_a_minute_asleep():
    for start in (2, 60, 3600 + 17):
        frames = [frame_at(ASLEEP, start + s) for s in range(61)]
        changed = sum(a != b for a, b in itertools.pairwise(frames))
        assert changed <= 12, (start, changed)
    frames = [frame_at(ASLEEP, 2 + s) for s in range(61)]
    assert sum(a != b for a, b in itertools.pairwise(frames)) == 10


def test_by_day_the_home_page_is_unchanged():
    for s in range(0, 300, 7):
        now = T0 + timedelta(seconds=s)
        assert night_pose(DAY, now) is None
        assert home_page(DAY, now, CFG) == home_page(DAY, now, CFG, idle_pose(now))


def test_a_forced_pose_wins_day_and_night():
    for state in (DAY, ASLEEP):
        assert home_page(state, T0 + timedelta(seconds=30), CFG, "right") == home_page(
            DAY, T0, CFG, "right")


@pytest.mark.parametrize("variant", ["vacant", "numbers"])
def test_other_variants_do_not_sleep(variant):
    for s in range(30):
        now = T0 + timedelta(seconds=s)
        assert render(ASLEEP, now, CFG, variant=variant) == render(DAY, now, CFG,
                                                                    variant=variant)


# --- the engine by night ---------------------------------------------------------------------


def test_a_page_cue_over_the_sleeping_page_starts_and_ends_asleep():
    cue = Cue(kind="page", at=T0 + timedelta(seconds=45), length_s=54.0, n=0, name="weather")
    st = replace(ASLEEP, cue=cue)
    n = show.slot_frames(cue)
    frame_us = 1_000_000 // SHOW_FPS

    def t(k):
        return cue.at + timedelta(microseconds=k * frame_us)

    for k in (0, n - 1, n, -1):
        f = render(st, t(k), CFG, variant="arthur")
        assert f == home_page(ASLEEP, t(k), CFG), k
        assert night_pose(ASLEEP, t(k)) in ("sleep_in", "sleep_out")
    # In between it is the page, not the sleeping cat.
    assert render(st, t(n // 2), CFG, variant="arthur") != home_page(ASLEEP, t(n // 2), CFG)


def test_no_scene_is_scheduled_at_night():
    cfg = cfg_with(page_pct=30, scene_pct=20)
    idle = replace(BASE, status_since=ny(2026, 11, 18, 20))
    for s in range(0, 9 * 3600, 30):
        st = _map(idle, ny(2026, 11, 18, 22) + timedelta(seconds=s), cfg)
        assert st.asleep_since is not None
        assert st.cue is None or st.cue.kind == "page"


# --- fixture and scenarios -------------------------------------------------------------------


def test_asleep_fixture():
    fx = FIXTURES["ready_asleep"]
    st = fx.display_state(CFG)
    assert st.kind == "ready" and st.asleep_since is not None and st.woke_at is None
    assert CFG.local(fx.now).hour == 3
    poses = {night_pose(st, fx.time_at(s)) for s in range(30)}
    assert poses == {"sleep_in", "sleep_out"}


def _poses(name, ts):
    sc = SCENARIOS[name]
    out = []
    for t in ts:
        snap, health, now = sc.at(t)
        st = to_display_state(snap, health, now, CFG)
        assert st.kind == "ready"
        out.append(night_pose(st, now))
    return out


def test_night_falls_scenario():
    sc = SCENARIOS["night_falls"]
    assert CFG.local(sc.start).strftime("%H:%M") == "21:57"
    assert _poses("night_falls", [179, 180, 181, 182, 188]) == [
        None, "yawn", "yawn", "sleep_in", "sleep_out"]


def test_morning_scenario():
    sc = SCENARIOS["morning"]
    assert CFG.local(sc.start).strftime("%H:%M") == "06:57"
    assert set(_poses("morning", range(180))) == {"sleep_in", "sleep_out"}
    assert _poses("morning", [180, 181, 182, 183, 184]) == [
        "sleep_wake", "sleep_wake", "yawn", "yawn", None]


def test_visit_scenarios_play_by_day():
    for name in ("normal_visit", "interrupted_visit", "outage", "sleep_wait", "no_weight"):
        sc = SCENARIOS[name]
        assert 7 <= CFG.local(sc.start).hour < 21, name
        assert schedule.quiet_window(sc.start + timedelta(seconds=sc.duration_s), CFG) is None


def test_the_studio_tile_breathes_in_a_seamless_12_second_loop():
    from arthur.sim.pipeline import ReloadingPipeline

    frames = ReloadingPipeline().current.fixture_loop("ready_asleep", (52, 16), "arthur")
    assert len(frames) == BREATH_S and len({f.to_rgb888() for f in frames}) == 2
