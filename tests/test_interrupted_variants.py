"""Interrupted: the default layout plus variants to compare (Arthur comes to have a look at
the globe turning)."""

from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import list_variants, render
from arthur.render.screens import (
    EDGE_RING,
    STROLL_STEPS,
    WALK_Y,
    loop_step,
    stroll_step,
)

CFG = default_config()
S = (52, 16)
NEW = ["stroll", "peek", "stroll_look", "stroll_watch", "stroll_card", "peek_pop", "peek_spin",
       "peek_pov"]
OFF = CFG.rgb("off")
ATTENTION = CFG.rgb("attention")


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
