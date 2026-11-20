"""A scene's plan is built once per play and kept, and a kept plan is never stale (D-0029).

``idle_show.planned_beats`` keeps the last plan, keyed by the scene's registry entry, its
name, the round, the body length and the config object. These tests play scenes the way the
show does (``render`` at each frame's time), interleaved so that a plan kept from one play
would be served to the next if any part of the key were missing, and compare every frame
with the same frame drawn from a plan built fresh for it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from arthur.config import default_config, load_config
from arthur.fixtures import FIXTURES, NOW
from arthur.model import Cue
from arthur.render import idle_show as show
from arthur.render import render
from arthur.render.scenes import bird, rat, stretch

CFG = default_config()
READY = FIXTURES["ready"].display_state(CFG)
FRAME_US = 1_000_000 // 8
BODY0 = show.GLANCE_FRAMES + show.WIPE_FRAMES


def _cue(name: str | None, n: int, length: float = 36.0) -> Cue:
    return Cue(kind="scene", at=NOW, length_s=length, n=n, name=name)


def _frame(cue: Cue, k: int, cfg=CFG):
    return render(replace(READY, cue=cue), cue.at + timedelta(microseconds=k * FRAME_US), cfg,
                  variant="arthur")


def _fresh(cue: Cue, k: int, cfg=CFG):
    """Frame ``k`` (a body frame) drawn from a plan built for it alone."""
    name = show.scene_name(cue)
    assert name is not None
    body = show.slot_frames(cue) - show.SCENE_FRAMES_AROUND
    beats = show.scene_beats(name, cfg, cue.n, body)
    return show.stage_frame(show.body_stage(beats, k - BODY0))


def _round(pred, ctx_name: str, want: bool) -> int:
    for n in range(200):
        ctx = show.SceneContext(ctx_name, CFG, n, 270)
        if pred(ctx) == want:
            return n
    raise AssertionError("no such round")


#: (scene, round) pairs that differ in every way the key must tell apart: each scene, each
#: of its endings, and rounds of the same ending.
PLAYS = [
    ("bird", _round(bird.perched, "bird", False)),
    ("bird", _round(bird.perched, "bird", True)),
    ("rat", _round(rat.turned, "rat", False)),
    ("rat", _round(rat.turned, "rat", True)),
    ("stretch", _round(stretch.kneads, "stretch", False)),
    ("stretch", _round(stretch.kneads, "stretch", True)),
    ("bird", _round(bird.perched, "bird", False) + 4),
]


@pytest.fixture(autouse=True)
def _no_kept_plan():
    show.forget_plan()
    yield
    show.forget_plan()


def test_interleaved_plays_never_get_another_plays_plan():
    """Frames of seven plays drawn in turn, a few at a time: every frame is its own play's."""
    cues = [_cue(name, n) for name, n in PLAYS]
    body = range(BODY0, show.slot_frames(cues[0]) - show.WIPE_FRAMES)
    for start in range(body.start, body.stop, 23):
        for cue in cues:
            for k in range(start, min(start + 3, body.stop)):
                assert _frame(cue, k) == _fresh(cue, k), (cue.name, cue.n, k)


def test_the_bag_pick_is_not_kept_across_rounds():
    """Unnamed cues (the bag picks the scene) one after another: each round its own scene."""
    for n in range(12):
        cue = _cue(None, n)
        assert show.scene_name(cue) == show.pick_scene(n)
        for k in (BODY0, BODY0 + 100, BODY0 + 200):
            assert _frame(cue, k) == _fresh(cue, k), (n, k)


def test_a_different_slot_length_builds_its_own_plan():
    for length in (36.0, 24.0, 54.0, 36.0):
        cue = _cue("rat", 3, length)
        for k in (BODY0, BODY0 + 50):
            assert _frame(cue, k) == _fresh(cue, k), (length, k)


def test_a_reloaded_config_builds_its_own_plan():
    """The same round under another config object: an equal reload, then one whose bird and
    fur are other colours. A plan kept from the first would draw with the first's colours."""
    cue = _cue("bird", PLAYS[0][1])
    k = BODY0 + 200
    _frame(cue, k)
    reloaded = load_config()
    assert reloaded == CFG and reloaded is not CFG
    assert _frame(cue, k, reloaded) == _fresh(cue, k, reloaded)
    colors = dict(CFG.palette.colors)
    colors["bird"], colors["fur"] = (1, 2, 3), (4, 5, 6)
    recoloured = replace(CFG, palette=replace(CFG.palette, colors=colors))
    for k in range(BODY0, BODY0 + 120, 7):
        drawn = _frame(cue, k, recoloured)
        assert drawn == _fresh(cue, k, recoloured)
        assert drawn != _frame(cue, k, CFG) or not ({(1, 2, 3), (4, 5, 6)} & set(drawn.pixels))
    assert {(4, 5, 6)} <= set(_frame(cue, BODY0, recoloured).pixels)


def test_a_re_registered_scene_builds_its_own_plan(monkeypatch):
    """Replacing a scene's registry entry (as a live reload or a test does) drops its plan."""
    cue = _cue("rat", 2)
    k = BODY0 + 10
    _frame(cue, k)
    calls = []

    def empty_scene(ctx):
        calls.append(ctx.n)
        return [show.Beat(ctx.frames, lambda stage, i: None)]

    monkeypatch.setitem(show.SCENES, "rat", show.SceneEntry(empty_scene))
    assert _frame(cue, k) == show.stage_frame(show.new_stage())
    assert calls == [2]


def test_one_play_builds_its_plan_once(monkeypatch):
    calls = []
    real = show.SCENES["stretch"]

    def counted(ctx):
        calls.append(ctx.n)
        return real.fn(ctx)

    monkeypatch.setitem(show.SCENES, "stretch", show.SceneEntry(counted))
    cue = _cue("stretch", 5)
    for k in range(show.slot_frames(cue)):
        _frame(cue, k)
    assert calls == [5]


def test_the_plan_depends_only_on_the_context():
    """Two plans built for the same context draw the same frames: nothing else (the time,
    the state, a previous play) goes into one."""
    for name, n in PLAYS:
        a = show.scene_beats(name, CFG, n, 270)
        show.scene_beats("bird", CFG, n + 1, 270)  # another plan in between
        b = show.scene_beats(name, CFG, n, 270)
        for j in range(0, 270, 9):
            assert show.body_stage(a, j) == show.body_stage(b, j), (name, n, j)
        # Drawing out of order gives the same stage as drawing in order.
        for j in (269, 0, 135, 1, 268):
            assert show.body_stage(a, j) == show.body_stage(b, j)


def test_pick_scene_answers_each_weighting():
    a = {"bird": 1, "rat": 1, "stretch": 1}
    b = {"bird": 3, "rat": 1}
    want = {}
    for n in range(20):
        for w in (a, b):
            show.forget_plan()
            want[n, id(w)] = show.pick_scene(n, w)
    assert any(want[n, id(a)] != want[n, id(b)] for n in range(20))
    for n in range(20):
        want_a, want_b = want[n, id(a)], want[n, id(b)]
        assert show.pick_scene(n, a) == want_a
        assert show.pick_scene(n, b) == want_b
        assert show.pick_scene(n, a) == want_a
