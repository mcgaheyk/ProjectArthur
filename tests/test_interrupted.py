"""Interrupted: he strolls up, looks in at us from inside the box, INTERRUPTION. One episode
that repeats from the top for as long as the state lasts."""

from dataclasses import replace
from datetime import timedelta

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import list_variants, render
from arthur.render.screens import EDGE_RING, interrupted_beat, interrupted_episode

CFG = default_config()
S = (52, 16)
OFF = CFG.rgb("off")
ATTENTION = CFG.rgb("attention")
FUR = CFG.rgb("fur")
#: The episode's length with the default timings.
EP = 21


def _state(name="interrupted"):
    fx = FIXTURES[name]
    return to_display_state(fx.snapshot, fx.health, fx.now, CFG), fx.now


def _story(t, shift=0, cfg=CFG):
    """The frame ``t`` seconds into an interruption that began ``shift`` seconds after the
    fixture's."""
    state, now = _state()
    state = replace(state, since=now + timedelta(seconds=shift))
    return render(state, state.since + timedelta(seconds=t), cfg)


def _pts(f, colour=None, xs=range(52), ys=range(16)):
    return {(x, y) for x in xs for y in ys
            if (f.get(x, y) == colour if colour else f.get(x, y) != OFF)}


def _face(f):
    return _pts(f, FUR, xs=range(16, 36), ys=range(11))


def _words(f):
    return _pts(f, ATTENTION, ys=range(2, 9))


def test_one_layout():
    assert list_variants("interrupted", S) == ["default"]
    assert not [v for (_size, kind, v) in EDGE_RING if kind == "interrupted"]


def test_episode_is_21_seconds_with_the_defaults():
    assert len(interrupted_episode(CFG)) == EP


def test_stays_off_the_border():
    w, h = S
    for t in range(EP):
        f = _story(t)
        border = [f.get(x, y) for x in range(w) for y in (0, h - 1)]
        border += [f.get(x, y) for x in (0, w - 1) for y in range(h)]
        assert all(p == OFF for p in border), t


def test_differs_from_cleaning_in_shape():
    cs, cnow = _state("cleaning")
    a = _pts(render(cs, cnow, CFG))
    for t in range(EP):
        b = _pts(_story(t))
        assert len(a ^ b) > len(a & b), t


def test_opens_with_him_walking_in():
    f = _story(0)
    assert _pts(f, FUR, xs=range(20))
    assert ATTENTION not in f.pixels


def test_then_only_the_paws():
    assert not _pts(_story(4), ys=range(11))


def test_then_his_face():
    assert _face(_story(7))


def test_then_the_title():
    f = _story(10)
    assert _words(f)
    assert not _face(f)


def test_back_to_his_face_then_a_second_phrase():
    assert _face(_story(13))
    second = _words(_story(16))
    assert second and second != _words(_story(10))


def test_rests_on_the_empty_globe():
    allowed = {CFG.rgb("cream"), CFG.rgb("sand")}
    for t in (19, 20):
        f = _story(t)
        lit = _pts(f)
        assert lit and all(38 <= x <= 49 and 2 <= y <= 13 for x, y in lit), t
        assert {f.get(x, y) for x, y in lit} <= allowed, t


def test_repeats_each_episode():
    for t in range(EP):
        a, b = _story(t), _story(t + EP)
        if 16 <= t <= 18:
            assert _words(a) != _words(b), t
        else:
            assert a == b, t


def test_second_phrase_is_never_the_title():
    title = _words(_story(10))
    for episode in range(8):
        assert _words(_story(episode * EP + 16)) != title, episode


def test_follows_since():
    # Every beat lands on the same second of the story. On a phrase beat only the phrase may
    # differ: the order the phrases take turns in is shuffled per visit, seeded from since.
    for t in range(2 * EP):
        a, b = _story(t), _story(t, shift=37)
        if 16 <= t % EP <= 18:
            assert _pts(a) - _pts(a, ATTENTION) == _pts(b) - _pts(b, ATTENTION), t
            assert _words(a) and _words(b), t
        else:
            assert a == b, t


def test_beats_count_from_when_the_state_began():
    state, _ = _state()
    assert interrupted_beat(state, state.since, CFG) == (0, 0)
    assert interrupted_beat(state, state.since + timedelta(seconds=EP + 3), CFG) == (1, 3)


def test_timings_set_the_episode_length():
    cfg = replace(CFG, timings=replace(CFG.timings, interrupted_title_s=4,
                                       interrupted_phrase_s=1, interrupted_rest_s=0))
    n = EP + (4 - 2) + (1 - 3) + (0 - 2)
    assert len(interrupted_episode(cfg)) == n
    state, _ = _state()
    assert interrupted_beat(state, state.since + timedelta(seconds=n), cfg) == (1, 0)
    # Title on seconds 10-13, the look round on 14-17, the phrase on 18, then the walk again.
    assert _words(_story(13, cfg=cfg)) == _words(_story(10))
    assert _face(_story(14, cfg=cfg))
    assert _words(_story(18, cfg=cfg))
    assert _story(n, cfg=cfg) == _story(0)
