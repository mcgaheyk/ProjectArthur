"""The stretch scene (arthur/render/scenes/stretch.py): what is particular to it.

The engine's tests (tests/test_idle_show.py) already run it for palette, border, fitting the
slot at 24, 36 and 54 s and ending seated.
"""

import itertools
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW
from arthur.model import Cue
from arthur.render import idle_show as show
from arthur.render import render
from arthur.render.scenes import stretch as S
from arthur.render.scenes._common import (
    FLOOR,
    SPOT_X,
    STRIDES_LEFT,
    STRIDES_RIGHT,
    Seat,
    Sprite,
    cells,
)

CFG = default_config()
READY = FIXTURES["ready"].display_state(CFG)
SLOT_LENGTHS = (24, 36, 54)
ROUNDS = range(24)
#: Changed frames allowed in a 36 s slot of this scene, glance and wipes included. Measured
#: 70-81 over rounds 0-19: the calm one of the scenes.
CHANGED_FRAME_BUDGET = 100
#: Columns a frame the middle of his lit pixels may move between two standing or walking
#: pictures (about a quarter of his 12-column body): the walk moves him one column every
#: two frames, a change of pose a little more.
CENTRE_MAX = 3
#: A round with each ending (round 2 is the first knead round).
USUAL_ROUND, KNEAD_ROUND = 0, 2
ORDER = ["quiet", "walk", "stand", "down", "bow", "knead", "rock", "hind", "up", "sit",
         "home", "settle"]


def ctx(n: int, length: float = 36) -> show.SceneContext:
    frames = round(length * show.SHOW_FPS) - show.SCENE_FRAMES_AROUND
    return show.SceneContext("stretch", CFG, n, frames)


def shots(n: int, length: float = 36) -> list:
    return S.shots(ctx(n, length))


def part(ss, name: str) -> list:
    return [s for s in ss if s.part == name]


def all_cases():
    return [(n, length) for n in ROUNDS for length in SLOT_LENGTHS]


def test_the_rounds_named_in_the_report_give_each_ending():
    assert not S.kneads(ctx(USUAL_ROUND))
    assert S.kneads(ctx(KNEAD_ROUND))
    assert not any(S.kneads(ctx(n)) for n in range(KNEAD_ROUND))
    assert FIXTURES["idle_stretch"].cue.n == USUAL_ROUND
    assert FIXTURES["idle_stretch_knead"].cue.n == KNEAD_ROUND


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_shots_fill_the_body_in_order(n, length):
    ss = shots(n, length)
    assert len(ss) == ctx(n, length).frames
    order = [k for k, _ in itertools.groupby(s.part for s in ss)]
    assert order == [p for p in ORDER if p in order]
    assert {"quiet", "walk", "stand", "down", "bow", "rock", "hind", "up", "home",
            "settle"} <= set(order)
    assert ("knead" in order) == S.kneads(ctx(n, length))
    assert ss[0].arthur in (Seat("right"), Seat("rest"))
    assert all(s == ss[-1] for s in ss[-S.REST_END:])
    assert ss[-1].arthur == Seat("rest")
    assert all(s.prey is None and s.extra is None for s in ss)  # no second animal


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_quiet_part_keeps_three_seconds_and_ends_with_a_yawn(n, length):
    quiet = part(shots(n, length), "quiet")
    assert len(quiet) >= S.QUIET_MIN
    assert all(isinstance(s.arthur, Seat) and s.arthur.dx == 0 for s in quiet)
    poses = [s.arthur.pose for s in quiet]
    assert poses[-S.YAWN_FRAMES - S.AFTER_YAWN:] == (
        ["yawn"] * S.YAWN_FRAMES + ["rest"] * S.AFTER_YAWN)
    assert "yawn" not in poses[:-S.YAWN_FRAMES - S.AFTER_YAWN]
    # Every beat of the quiet part but a blink or an ear twitch lasts at least a second.
    runs = [(k, len(list(g))) for k, g in itertools.groupby(poses)]
    assert all(r >= 4 or k in ("blink", "ear", "half") for k, r in runs[1:]), runs


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_every_lit_pixel_is_on_the_stage(n, length):
    for s in shots(n, length):
        c = cells(s.arthur, CFG)
        # cells() draws on a 50x14 stage, so check the art itself is not clipped.
        if isinstance(s.arthur, Sprite):
            lit = sum(ch != "." for row in s.arthur.rows for ch in row)
            assert len(c) == lit, (s.part, s.arthur.x)
        assert c


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_bow_is_held_at_least_two_seconds(n, length):
    ss = shots(n, length)
    bow = part(ss, "bow")
    assert len(bow) >= 2 * show.SHOW_FPS
    arts = [s.arthur.rows for s in bow]
    assert arts[0] == S.BOW  # eyes open first
    assert S.BOW_SHUT in arts and S.BOW_DEEP in arts
    # At most about two changes a second: every held picture at least 4 frames.
    runs = [len(list(g)) for _, g in itertools.groupby(arts)]
    assert min(runs) >= S.SEG_MIN, runs
    if length == 36:
        assert S.BOW_TAIL in arts
        assert 22 <= len(bow) <= 30


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_hind_feet_stay_planted_from_the_stand_through_the_bow(n, length):
    ss = shots(n, length)
    planted = [s for s in ss if s.part in ("stand", "down", "bow", "knead", "rock")]
    for s in planted:
        floor = {x for x, y in cells(s.arthur, CFG) if y == FLOOR and x < S.STRETCH_X + 6}
        assert floor == {S.STRETCH_X + 2, S.STRETCH_X + 3}, s.part
    # And the stand lines up with where the walk stopped.
    walk = part(ss, "walk")
    assert walk[-1].arthur.x == S.WALK_TO
    body = {x for x, y in cells(walk[-1].arthur, CFG) if y == FLOOR - 3}
    stand = {x for x, y in cells(part(ss, "stand")[0].arthur, CFG) if y == FLOOR - 3}
    assert body == stand


def test_the_bow_is_centred_on_the_stage():
    lit = cells(Sprite(S.BOW, S.STRETCH_X, FLOOR - len(S.BOW) + 1), CFG)
    left, right = min(x for x, _ in lit), max(x for x, _ in lit)
    assert abs(left - (show.STAGE_W - 1 - right)) <= 1


def _centre(actor) -> float:
    c = cells(actor, CFG)
    return sum(x for x, _ in c) / len(c)


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_no_sprite_moves_faster_than_its_limit(n, length):
    ss = shots(n, length)
    for a, b in itertools.pairwise(ss):
        if isinstance(a.arthur, Sprite) and isinstance(b.arthur, Sprite):
            assert abs(_centre(b.arthur) - _centre(a.arthur)) <= CENTRE_MAX, b.part
            strides = STRIDES_RIGHT + STRIDES_LEFT
            if a.arthur.rows in strides and b.arthur.rows in strides:
                assert abs(b.arthur.x - a.arthur.x) <= 1
        if isinstance(a.arthur, Seat) and isinstance(b.arthur, Seat):
            assert a.arthur.dx == b.arthur.dx  # seated, he never slides


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_he_walks_out_to_the_middle_and_back_slowly(n, length):
    ss = shots(n, length)
    walk = [s.arthur.x for s in part(ss, "walk")]
    assert walk[0] == SPOT_X and walk[-1] == S.WALK_TO
    assert len(walk) == S.WALK_FRAMES_PER_PX * (S.WALK_TO - SPOT_X + 1)
    home = [s.arthur.x for s in part(ss, "home") if isinstance(s.arthur, Sprite)]
    assert home[0] == S.HOME_FROM and home[-1] == SPOT_X
    assert all(b <= a for a, b in itertools.pairwise(home))


@pytest.mark.parametrize("n", ROUNDS)
def test_knead_presses_alternate(n):
    knead = part(shots(n), "knead")
    if not S.kneads(ctx(n)):
        assert not knead
        return
    runs = [(k, len(list(g))) for k, g in itertools.groupby(s.arthur.rows for s in knead)]
    assert 4 <= len(runs) <= 6
    assert all(r in (2, 3) for _, r in runs)
    assert all(a[0] != b[0] for a, b in itertools.pairwise(runs))


def test_the_sit_in_the_middle_comes_on_some_rounds_and_lasts_one_to_three_seconds():
    sits = [part(shots(n), "sit") for n in range(40)]
    some = [s for s in sits if s]
    assert 0.2 <= len(some) / len(sits) <= 0.7
    for s in some:
        assert show.SHOW_FPS <= len(s) <= 3 * show.SHOW_FPS
        assert {x.arthur.dx for x in s} == {S.MID_DX}
        assert any(x.arthur.pose == "blink" for x in s)


def test_a_short_slot_gives_up_the_extras_first():
    for n in ROUNDS:
        assert not part(shots(n, 24), "sit")


def test_knead_on_about_a_quarter_of_rounds_and_rounds_repeat():
    kneaded = [S.kneads(ctx(n)) for n in range(400)]
    assert 0.18 <= sum(kneaded) / len(kneaded) <= 0.32
    for n in (KNEAD_ROUND, 7, 123):
        assert shots(n) == shots(n)


def test_rounds_vary():
    ss = [tuple(shots(n)) for n in range(12)]
    assert len(set(ss)) == len(ss)
    quiets = [tuple(part(s, "quiet")) for s in ss]
    assert len(set(quiets)) == len(quiets)
    holds = {tuple(x.arthur.rows for x in part(s, "bow")) for s in ss}
    assert len(holds) > 3


def test_changed_frames_stay_under_budget():
    for n in range(20):
        cue = Cue(kind="scene", at=NOW, length_s=36.0, n=n, name="stretch")
        frames = [render(READY.__class__(**{**READY.__dict__, "cue": cue}),
                         NOW + timedelta(microseconds=k * 125_000), CFG, variant="arthur")
                  for k in range(show.slot_frames(cue) + 1)]
        changed = sum(a != b for a, b in itertools.pairwise(frames))
        assert changed <= CHANGED_FRAME_BUDGET, (n, changed)
