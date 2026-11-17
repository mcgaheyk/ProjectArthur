"""Interrupted: the default layout plus variants to compare (Arthur comes to have a look at
the globe turning)."""

from dataclasses import replace
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import list_variants, render
from arthur.render.font import large_font
from arthur.render.screens import (
    EDGE_RING,
    STROLL_STEPS,
    WALK_Y,
    loop_step,
    stroll_step,
    turn_caption,
)

CFG = default_config()
S = (52, 16)
NEW = ["stroll", "peek", "stroll_look", "stroll_watch", "stroll_card", "peek_pop", "peek_spin",
       "peek_pov", "stroll_pov"]
OFF = CFG.rgb("off")
ATTENTION = CFG.rgb("attention")
FUR = CFG.rgb("fur")


def _state(name="interrupted"):
    fx = FIXTURES[name]
    return to_display_state(fx.snapshot, fx.health, fx.now, CFG), fx.now


def _lit(f):
    return {i for i, p in enumerate(f.pixels) if p != OFF}


def _at(variant, n, step, loop=0):
    """The frame at ``step`` of loop ``loop`` of an ``n``-second loop, counted from since."""
    state, _ = _state()
    t = state.since + timedelta(seconds=loop * n + step)
    assert loop_step(state, t, n) == (loop, step)
    return render(state, t, CFG, variant=variant)


def test_variants_in_order_default_first():
    assert list_variants("interrupted", S) == ["default", *NEW]


@pytest.mark.parametrize("variant", NEW)
def test_each_variant_moves(variant):
    state, now = _state()
    frames = {render(state, now + timedelta(seconds=t), CFG, variant=variant).to_rgb888()
              for t in range(12)}
    assert len(frames) >= 2


def test_no_interrupted_variant_has_an_edge_ring():
    assert not [v for (_size, kind, v) in EDGE_RING if kind == "interrupted"]


@pytest.mark.parametrize("variant", NEW)
def test_variants_stay_off_the_border(variant):
    state, now = _state()
    w, h = S
    for t in range(12):
        f = render(state, now + timedelta(seconds=t), CFG, variant=variant)
        border = [f.get(x, y) for x in range(w) for y in (0, h - 1)]
        border += [f.get(x, y) for x in (0, w - 1) for y in range(h)]
        assert all(p == OFF for p in border), (variant, t)


@pytest.mark.parametrize("variant", NEW)
def test_differs_from_cleaning_in_shape(variant):
    cs, cnow = _state("cleaning")
    a = _lit(render(cs, cnow, CFG))
    state, now = _state()
    b = _lit(render(state, now, CFG, variant=variant))
    assert len(a ^ b) > len(a & b)


def test_loops_count_from_when_the_state_began():
    state, _ = _state()
    assert stroll_step(state, state.since) == 0
    assert loop_step(state, state.since, 8) == (0, 0)
    assert loop_step(state, state.since + timedelta(seconds=17), 8) == (2, 1)


def _at_stroll_step(step):
    state, now = _state()
    for t in range(len(STROLL_STEPS)):
        if stroll_step(state, now + timedelta(seconds=t)) == step:
            return render(state, now + timedelta(seconds=t), CFG, variant="stroll")
    raise AssertionError(step)


def _cat_pixels_left_of_20(f):
    # The cat walks on rows WALK_Y..13; the caption is above (rows 1-5).
    return [(x, y) for x in range(20) for y in range(WALK_Y, 14) if f.get(x, y) != OFF]


def test_stroll_walks_in_from_the_left():
    assert _cat_pixels_left_of_20(_at_stroll_step(0))
    assert not _cat_pixels_left_of_20(_at_stroll_step(5))


def test_peek_pop_pops_up_somewhere_new_each_loop():
    xs = set()
    for loop in range(3):
        f = _at("peek_pop", 8, 3, loop)
        xs.add(min(x for x in range(52) for y in range(7, 15) if f.get(x, y) != OFF))
    assert len(xs) == 3


@pytest.mark.parametrize(("variant", "n"), [("stroll_card", 10), ("peek_pov", 10)])
def test_words_then_scene(variant, n):
    words = _at(variant, n, 0)
    assert any(words.get(x, y) == ATTENTION for x in range(52) for y in range(2, 9))
    scene = _at(variant, n, 5)
    assert ATTENTION not in scene.pixels


# --- stroll_pov: one 21-second episode, repeating ------------------------------------------

EP = 21


def _story(t, shift=0):
    """stroll_pov ``t`` seconds into an interruption that began ``shift`` seconds after the
    fixture's."""
    state, now = _state()
    state = replace(state, since=now + timedelta(seconds=shift))
    return render(state, state.since + timedelta(seconds=t), CFG, variant="stroll_pov")


def _pts(f, colour=None, xs=range(52), ys=range(16)):
    return {(x, y) for x in xs for y in ys
            if (f.get(x, y) == colour if colour else f.get(x, y) != OFF)}


def _face(f):
    return _pts(f, FUR, xs=range(16, 36), ys=range(11))


def _words(f):
    return _pts(f, ATTENTION, ys=range(2, 9))


def test_stroll_pov_opens_with_him_walking_in():
    f = _story(0)
    assert _pts(f, FUR, xs=range(20))
    assert ATTENTION not in f.pixels


def test_stroll_pov_then_only_the_paws():
    assert not _pts(_story(4), ys=range(11))


def test_stroll_pov_then_his_face():
    assert _face(_story(7))


def test_stroll_pov_then_the_title():
    f = _story(10)
    assert _words(f)
    assert not _face(f)


def test_stroll_pov_back_to_his_face_then_a_second_phrase():
    assert _face(_story(13))
    second = _words(_story(16))
    assert second and second != _words(_story(10))


def test_stroll_pov_rests_on_the_empty_globe():
    allowed = {CFG.rgb("cream"), CFG.rgb("sand")}
    for t in (19, 20):
        f = _story(t)
        lit = _pts(f)
        assert lit and all(38 <= x <= 49 and 2 <= y <= 13 for x, y in lit), t
        assert {f.get(x, y) for x, y in lit} <= allowed, t


def test_stroll_pov_repeats_each_episode():
    for t in range(EP):
        a, b = _story(t), _story(t + EP)
        if 16 <= t <= 18:
            assert _words(a) != _words(b), t
        else:
            assert a == b, t


def test_stroll_pov_second_phrase_is_never_the_title():
    title = _words(_story(10))
    for episode in range(8):
        assert _words(_story(episode * EP + 16)) != title, episode


def test_stroll_pov_follows_since():
    # Every beat lands on the same second of the story. On a phrase beat only the phrase may
    # differ: the order the phrases take turns in is shuffled per visit, seeded from since.
    for t in range(2 * EP):
        a, b = _story(t), _story(t, shift=37)
        if 16 <= t % EP <= 18:
            assert _pts(a) - _pts(a, ATTENTION) == _pts(b) - _pts(b, ATTENTION), t
            assert _words(a) and _words(b), t
        else:
            assert a == b, t


def test_turn_caption_can_exclude_captions():
    state, _ = _state()
    font = large_font()
    seen = {turn_caption(state, CFG, "interrupted_look", k, S, 50, font, fallback="X",
                         exclude=("INTERRUPTION",)) for k in range(20)}
    assert "INTERRUPTION" not in seen and len(seen) > 1
    every = set(CFG.captions["interrupted_look"])
    assert turn_caption(state, CFG, "interrupted_look", 0, S, 50, font, fallback="X",
                        exclude=tuple(every)) == "X"
