"""The idle show's schedule (arthur/schedule.py, D-0019)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from arthur import schedule
from arthur.config import Idle, default_config
from arthur.fixtures import FIXTURES, NOW, SCENARIO_START, SCENARIOS
from arthur.mapping import to_display_state
from arthur.schedule import idle_cue, is_quiet, slots

BASE_CFG = default_config()
NY = ZoneInfo("America/New_York")
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def cfg_with(*, tz=NY, **idle):
    return replace(BASE_CFG, idle=Idle(**idle), tz=tz)


def _day(round_s, page_pct, scene_pct):
    """Idle settings with quiet hours off."""
    return {"round_s": round_s, "page_pct": page_pct, "scene_pct": scene_pct,
            "quiet_from_h": 0, "quiet_until_h": 0}


#: (Idle settings, expected (R, slots)). Quiet hours off so the daytime checks see every slot.
SETTINGS = {
    "kyle": (
        _day(180, 30, 20),
        (180, [("page", 45, 54), ("scene", 144, 36)]),
    ),
    "no_scene": (
        _day(120, 25, 0),
        (120, [("page", 45, 30)]),
    ),
    "no_page": (
        _day(60, 0, 40),
        (60, [("scene", 36, 24)]),
    ),
    # R = 181: page floor(54.3) = 54, scene floor(36.2) = 36, home 91 → 45 then 46.
    "odd": (
        _day(181, 30, 20),
        (181, [("page", 45, 54), ("scene", 145, 36)]),
    ),
    # A fractional round length is cut to whole seconds; 33.3 % of 97 = 32.301 → 32.
    "fractional": (
        _day(97.9, 33.3, 11),
        (97, [("page", 27, 32), ("scene", 87, 10)]),
    ),
}
IDS = list(SETTINGS)


@pytest.fixture(params=IDS)
def setting(request):
    idle, expected = SETTINGS[request.param]
    return cfg_with(**idle), expected


def _expected_cue(cfg, expected, now):
    """The cue a fully settled, daytime ``now`` should get, from the expected layout."""
    r, layout = expected
    secs = (now - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(seconds=1)
    n, pos = divmod(secs, r)
    for kind, start, length in layout:
        if start <= pos < start + length:
            return kind, datetime.fromtimestamp(n * r + start, UTC), float(length), n
    return None


def test_slot_layout(setting):
    cfg, expected = setting
    assert slots(cfg) == expected


def test_kyles_round_matches_d0019():
    cfg = cfg_with(round_s=180, page_pct=30, scene_pct=20)
    r, layout = slots(cfg)
    # home 0-45, page 45-99, home 99-144, scene 144-180
    assert layout == [("page", 45, 54), ("scene", 144, 36)]
    assert r - 54 - 36 == 90


def test_same_inputs_same_cue(setting):
    cfg, _ = setting
    now = datetime(2026, 11, 18, 15, 2, 37, 250_000, tzinfo=UTC)
    for k in range(400):
        t = now + timedelta(seconds=k)
        assert idle_cue(LONG_AGO, t, cfg) == idle_cue(LONG_AGO, t, cfg)


def test_cue_constant_through_each_slot_and_layout(setting):
    cfg, expected = setting
    r, _ = expected
    start = datetime(2026, 11, 18, 14, 0, 0, tzinfo=UTC)  # 09:00 in New York
    seen: dict[datetime, set] = {}
    for q in range(4 * r * 4):  # four rounds in quarter seconds
        now = start + timedelta(seconds=q / 4)
        cue = idle_cue(LONG_AGO, now, cfg)
        want = _expected_cue(cfg, expected, now)
        if want is None:
            assert cue is None, now
            continue
        kind, at, length, n = want
        assert cue is not None, now
        assert (cue.kind, cue.at, cue.length_s, cue.n) == (kind, at, length, n)
        assert cue.at <= now < cue.at + timedelta(seconds=cue.length_s)
        seen.setdefault(cue.at, set()).add((cue.kind, cue.length_s, cue.n))
    assert seen
    assert all(len(v) == 1 for v in seen.values())


def test_rounds_start_on_the_hour_at_home(setting):
    cfg, expected = setting
    r, _ = expected
    if 3600 % r:
        pytest.skip("round does not divide the hour")
    for h in range(24):
        now = datetime(2026, 11, 18, h, 0, 0, tzinfo=UTC)
        assert idle_cue(LONG_AGO, now, cfg) is None


def _shares(cfg, start, hours, step_s=1):
    page = scene = total = 0
    for k in range(0, int(hours * 3600), step_s):
        cue = idle_cue(LONG_AGO, start + timedelta(seconds=k), cfg)
        total += 1
        if cue is not None:
            if cue.kind == "page":
                page += 1
            else:
                scene += 1
    return 100 * page / total, 100 * scene / total


@pytest.mark.parametrize("name", IDS)
def test_daytime_shares_match_config(name):
    idle, _ = SETTINGS[name]
    cfg = cfg_with(**idle)
    page, scene = _shares(cfg, datetime(2026, 11, 18, 5, 0, tzinfo=UTC), 24)
    assert abs(page - idle["page_pct"]) <= 2
    assert abs(scene - idle["scene_pct"]) <= 2


@pytest.mark.parametrize("name", IDS)
def test_night_has_no_scenes_but_keeps_pages(name):
    idle, _ = SETTINGS[name]
    cfg = cfg_with(**{**idle, "quiet_from_h": 22, "quiet_until_h": 7})
    # 23:00 to 06:00 New York, 2026-11-18/19 (EST): 04:00 to 11:00 UTC.
    page, scene = _shares(cfg, datetime(2026, 11, 19, 4, 0, tzinfo=UTC), 7)
    assert scene == 0
    assert abs(page - idle["page_pct"]) <= 2
    # Daytime the same day still has its scenes.
    page, scene = _shares(cfg, datetime(2026, 11, 18, 14, 0, tzinfo=UTC), 7)
    assert abs(scene - idle["scene_pct"]) <= 2


# --- settle -------------------------------------------------------------------------------

KYLE = cfg_with(round_s=180, page_pct=30, scene_pct=20, settle_s=45)
ROUND = datetime(2026, 11, 18, 15, 0, 0, tzinfo=UTC)  # a round start, 10:00 in New York


def at(s):
    return ROUND + timedelta(seconds=s)


def test_nothing_without_since():
    assert idle_cue(None, at(50), KYLE) is None
    assert idle_cue(None, at(150), KYLE) is None


def test_slot_plays_when_it_began_exactly_at_settle():
    since = at(0)  # page slot begins at 45 = since + 45
    cue = idle_cue(since, at(45), KYLE)
    assert cue is not None and cue.kind == "page" and cue.at == at(45)
    assert idle_cue(since, at(98.9), KYLE) == cue


def test_slot_skipped_whole_when_it_began_before_settle():
    since = at(0.5)  # settled at 45.5, after the page began at 45
    for s in (45, 46, 60, 98.9):
        assert idle_cue(since, at(s), KYLE) is None
    # The next slot, the scene at 144, plays.
    cue = idle_cue(since, at(144), KYLE)
    assert cue is not None and cue.kind == "scene" and cue.at == at(144)


def test_since_inside_a_slot_skips_it_and_plays_the_next():
    since = at(60)  # idle began during the page slot (45-99)
    for s in (60, 70, 98):
        assert idle_cue(since, at(s), KYLE) is None
    # Settled at 105; the scene at 144 began after that.
    cue = idle_cue(since, at(150), KYLE)
    assert cue is not None and cue.kind == "scene" and cue.at == at(144)


def test_since_inside_a_scene_skips_it_and_plays_the_next_page():
    since = at(150)
    assert idle_cue(since, at(170), KYLE) is None
    cue = idle_cue(since, at(180 + 50), KYLE)
    assert cue is not None and cue.kind == "page" and cue.at == at(180 + 45)


def test_settle_that_lands_between_slots():
    since = at(100)  # settled at 145: the scene at 144 began a second too early
    assert idle_cue(since, at(150), KYLE) is None
    cue = idle_cue(since, at(180 + 45), KYLE)
    assert cue is not None and cue.kind == "page"


def test_nothing_when_both_shares_are_zero():
    cfg = cfg_with(round_s=180, page_pct=0, scene_pct=0)
    for k in range(0, 3600, 7):
        assert idle_cue(LONG_AGO, ROUND + timedelta(seconds=k), cfg) is None


def test_repository_config_with_the_shares_at_zero_never_cues():
    off = replace(BASE_CFG, idle=replace(BASE_CFG.idle, page_pct=0, scene_pct=0))
    for k in range(0, 3 * 3600, 5):
        assert idle_cue(LONG_AGO, ROUND + timedelta(seconds=k), off) is None


def test_repository_config_gives_kyles_split_over_a_day():
    """Round 180 s: page 54 s at 0:45, scene 36 s at 2:24 (D-0019, D-0020). Over a whole day
    the page has 30 % of the time; the scene has 20 % of the daytime and none of the night."""
    assert slots(BASE_CFG) == (180, [("page", 45, 54), ("scene", 144, 36)])
    start = datetime(2026, 11, 18, tzinfo=NY).astimezone(UTC)
    seconds = {"page": 0, "scene": 0, "night_scene": 0}
    for k in range(24 * 3600):
        now = start + timedelta(seconds=k)
        cue = idle_cue(LONG_AGO, now, BASE_CFG)
        if cue is not None:
            seconds[cue.kind] += 1
            if cue.kind == "scene" and is_quiet(now, BASE_CFG):
                seconds["night_scene"] += 1
    day_s = 24 * 3600
    quiet_h = (24 - BASE_CFG.idle.quiet_from_h) + BASE_CFG.idle.quiet_until_h
    assert seconds["page"] == day_s * 30 // 100
    assert seconds["scene"] == (day_s - quiet_h * 3600) * 20 // 100
    assert seconds["night_scene"] == 0


# --- quiet hours --------------------------------------------------------------------------


def _ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=NY).astimezone(UTC)


@pytest.mark.parametrize(
    ("hour", "quiet"),
    [(21, False), (22, True), (23, True), (0, True), (3, True), (6, True), (7, False),
     (12, False)],
)
def test_is_quiet_wraps_midnight(hour, quiet):
    cfg = cfg_with(quiet_from_h=22, quiet_until_h=7)
    assert is_quiet(_ny(2026, 11, 18, hour), cfg) is quiet
    assert is_quiet(_ny(2026, 11, 18, hour, 59), cfg) is quiet


def test_is_quiet_boundaries_to_the_second():
    cfg = cfg_with(quiet_from_h=22, quiet_until_h=7)
    assert not is_quiet(_ny(2026, 11, 18, 21, 59) + timedelta(seconds=59), cfg)
    assert is_quiet(_ny(2026, 11, 18, 22), cfg)
    assert is_quiet(_ny(2026, 11, 19, 6, 59) + timedelta(seconds=59), cfg)
    assert not is_quiet(_ny(2026, 11, 19, 7), cfg)


@pytest.mark.parametrize(
    ("hour", "quiet"), [(0, False), (1, False), (2, True), (5, True), (6, False), (23, False)]
)
def test_is_quiet_without_wrap(hour, quiet):
    cfg = cfg_with(quiet_from_h=2, quiet_until_h=6)
    assert is_quiet(_ny(2026, 11, 18, hour), cfg) is quiet


def test_equal_hours_mean_no_quiet_hours():
    cfg = cfg_with(quiet_from_h=7, quiet_until_h=7)
    assert not any(is_quiet(_ny(2026, 11, 18, h), cfg) for h in range(24))


def test_quiet_hours_follow_local_time_not_utc():
    cfg = cfg_with(quiet_from_h=22, quiet_until_h=7, tz=ZoneInfo("UTC"))
    # 18:00 in New York is 23:00 UTC: quiet when the zone is UTC, not when it is New York.
    assert is_quiet(_ny(2026, 11, 18, 18), cfg)
    assert not is_quiet(_ny(2026, 11, 18, 18), cfg_with(quiet_from_h=22, quiet_until_h=7))


@pytest.mark.parametrize(
    ("day", "label"),
    [((2026, 11, 1), "fall back"), ((2027, 3, 14), "spring forward")],
)
def test_quiet_hours_across_clock_changes(day, label):
    cfg = cfg_with(round_s=180, page_pct=30, scene_pct=20, quiet_from_h=22, quiet_until_h=7)
    y, mo, d = day
    # From 21:00 local the evening before to 09:00 local on the change day, every second.
    start = (datetime(y, mo, d, 21, 0, tzinfo=NY) - timedelta(days=1)).astimezone(UTC)
    end = datetime(y, mo, d, 9, 0, tzinfo=NY).astimezone(UTC)
    scenes_by_hour: dict[int, int] = {}
    pages = 0
    t = start
    while t < end:
        local = t.astimezone(NY)
        quiet = local.hour >= 22 or local.hour < 7
        assert is_quiet(t, cfg) is quiet, (label, local)
        cue = idle_cue(LONG_AGO, t, cfg)
        if cue is not None:
            if cue.kind == "scene":
                # A scene's slot never begins in quiet hours.
                start_local = cue.at.astimezone(NY)
                assert not (start_local.hour >= 22 or start_local.hour < 7), (label, local)
                scenes_by_hour[start_local.hour] = scenes_by_hour.get(start_local.hour, 0) + 1
            else:
                pages += 1
        t += timedelta(seconds=1)
    # Scenes on both sides of the night, none inside it; pages throughout.
    assert set(scenes_by_hour) == {21, 7, 8}
    assert pages > 0


def test_a_scene_begun_before_quiet_hours_plays_to_its_end():
    cfg = cfg_with(round_s=180, page_pct=30, scene_pct=20, quiet_from_h=22, quiet_until_h=7)
    # 21:59:24 NY is a round's 144th second (rounds are on the hour): a scene slot that runs
    # to 22:00:00, all of it before quiet hours.
    t = _ny(2026, 11, 18, 21, 59) + timedelta(seconds=24)
    cue = idle_cue(LONG_AGO, t, cfg)
    assert cue is not None and cue.kind == "scene"
    # 22:02:24 is the next scene slot, which begins in quiet hours.
    assert idle_cue(LONG_AGO, t + timedelta(seconds=180), cfg) is None


# --- the fixtures and scenarios stay on the home page ------------------------------------

SHOW_ON = cfg_with(round_s=180, page_pct=30, scene_pct=20)


@pytest.mark.parametrize("start", [NOW, SCENARIO_START], ids=["fixtures", "scenarios"])
def test_no_cue_for_30_s_after_fixture_and_scenario_times(start):
    for q in range(30 * 8):
        t = start + timedelta(seconds=q / 8)
        assert idle_cue(LONG_AGO, t, SHOW_ON) is None, t


def test_fixtures_and_scenarios_map_without_a_cue_with_the_show_on():
    for fx in FIXTURES.values():
        for s in range(30):
            st = to_display_state(fx.snapshot, fx.health, fx.now + timedelta(seconds=s), SHOW_ON)
            assert st.cue is None, (fx.name, s)
    for sc in SCENARIOS.values():
        for s in range(30):
            snap, health, now = sc.at(s)
            assert to_display_state(snap, health, now, SHOW_ON).cue is None, (sc.name, s)


def test_schedule_is_pure():
    src = schedule.__file__
    with open(src) as fh:
        text = fh.read()
    for banned in ("arthur.render", "arthur.sim", "import time", "datetime.now", "utcnow"):
        assert banned not in text, banned
