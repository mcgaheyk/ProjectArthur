"""The rat scene (arthur/render/scenes/rat.py): what is particular to it.

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
from arthur.render.scenes import rat as R
from arthur.render.scenes._common import Seat, Sprite, cells, gap

CFG = default_config()
READY = FIXTURES["ready"].display_state(CFG)
SLOT_LENGTHS = (24, 36, 54)
ROUNDS = range(24)
#: Changed frames allowed in a 36 s slot of this scene, glance and wipes included. Measured
#: 105-122 over rounds 0-19.
CHANGED_FRAME_BUDGET = 130
#: Columns a frame: the rat (10 wide) and Arthur running (14 wide), about a third of their
#: length; the rat bolts at 5.
RAT_MAX, RAT_BOLT_MAX, ARTHUR_MAX = 4, 5, 5
#: A round with each ending (round 2 is the first tables-turned round).
CHASE_ROUND, TURNED_ROUND = 0, 2


def ctx(n: int, length: float = 36) -> show.SceneContext:
    frames = round(length * show.SHOW_FPS) - show.SCENE_FRAMES_AROUND
    return show.SceneContext("rat", CFG, n, frames)


def shots(n: int, length: float = 36) -> list:
    return R.shots(ctx(n, length))


def part(ss, name: str) -> list:
    return [s for s in ss if s.part == name]


def all_cases():
    return [(n, length) for n in ROUNDS for length in SLOT_LENGTHS]


def test_the_rounds_named_in_the_report_give_each_ending():
    assert not R.turned(ctx(CHASE_ROUND))
    assert R.turned(ctx(TURNED_ROUND))


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_shots_fill_the_body_in_order(n, length):
    ss = shots(n, length)
    assert len(ss) == ctx(n, length).frames
    order = [k for k, _ in itertools.groupby(s.part for s in ss)]
    assert order == ["quiet", "standoff", "bolt", "gone", "back", "gone", "settle"]
    assert ss[0].arthur == Seat("right") or ss[0].arthur == Seat("rest")
    assert all(s == ss[-1] for s in ss[-R.REST_END:])
    assert ss[-1].arthur == Seat("rest") and ss[-1].prey is None


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_in_the_quiet_part_arthur_stays_put_and_the_rat_keeps_its_distance(n, length):
    quiet = part(shots(n, length), "quiet")
    assert len(quiet) > 4 * show.SHOW_FPS
    arthur = cells(Seat("rest"), CFG) | cells(Seat("right_lift"), CFG)
    for s in quiet:
        assert isinstance(s.arthur, Seat) and s.arthur.dx == 0
        assert gap(cells(s.arthur, CFG) | arthur, cells(s.prey, CFG)) >= 2
    # It shows up, and ends frozen about 6 columns clear of his tail, facing him.
    assert any(s.prey is not None for s in quiet)
    assert quiet[-1].prey == R.rat_at(R.RAT_A, R.END_X)
    assert gap(arthur, cells(quiet[-1].prey, CFG)) == 6


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_rat_creeps_in_darts_of_two_columns(n, length):
    quiet = part(shots(n, length), "quiet")
    rats = [s.prey for s in quiet]
    for a, b in itertools.pairwise(rats):
        if a is not None and b is not None:
            assert abs(b.x - a.x) <= 2
    # From the last peek on, it only darts: 3 or 4 frames of 2 columns towards him.
    start = max(i for i, r in enumerate(rats) if r is not None and r.x == R.CREEP_X)
    xs = [r.x for r in rats[start:]]
    steps = [b - a for a, b in itertools.pairwise(xs)]
    assert set(steps) <= {0, -2, 2}
    runs = [len(list(g)) for k, g in itertools.groupby(steps) if k == -2]
    assert len(runs) >= 3 and all(r in (3, 4) for r in runs), runs
    assert all(len(list(g)) == 1 for k, g in itertools.groupby(steps) if k == 2)


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_arthur_rarely_moves_on_a_frame_the_rat_moves(n, length):
    quiet = part(shots(n, length), "quiet")
    both = [i for i in range(1, len(quiet))
            if quiet[i].arthur != quiet[i - 1].arthur and quiet[i].prey != quiet[i - 1].prey]
    assert len(both) <= 1


def test_he_watches_it_mostly_glancing_right():
    for n in ROUNDS:
        quiet = part(shots(n), "quiet")
        first = next(i for i, s in enumerate(quiet) if s.prey is not None)
        watching = [s.arthur.pose for s in quiet[first + 6:]]
        assert watching.count("right") >= 0.7 * len(watching)


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_burst_has_two_empty_beats(n, length):
    ss = shots(n, length)
    burst = [s for s in ss if s.part not in ("quiet", "settle")]
    empty_runs = [len(list(g)) for k, g in itertools.groupby(
        s.arthur is None and s.prey is None for s in burst) if k]
    assert sum(r >= 4 for r in empty_runs) >= 2


def _xs(actor):
    c = cells(actor, CFG)
    return min(x for x, _ in c), max(x for x, _ in c)


@pytest.mark.parametrize("n", ROUNDS)
def test_on_the_way_back_the_right_one_leads(n):
    back = part(shots(n), "back")
    both = [s for s in back if s.arthur is not None and s.prey is not None
            and cells(s.arthur, CFG) and cells(s.prey, CFG)]
    assert both
    for s in both:
        (rat_l, rat_r), (cat_l, cat_r) = _xs(s.prey), _xs(s.arthur)
        if R.turned(ctx(n)):
            assert cat_r < rat_l  # Arthur flees first, the rat after him
        else:
            assert rat_r < cat_l  # the rat runs, Arthur after it
    # Everyone goes off to the left.
    assert all(isinstance(s.arthur, Sprite) or s.arthur is None for s in back)


def test_the_standoff_holds_still_and_the_rat_bolts_first():
    for n in ROUNDS:
        ss = shots(n)
        stand = part(ss, "standoff")
        assert len(stand) == R.STANDOFF and len(set(stand)) == 1
        bolt = part(ss, "bolt")
        assert bolt[0].arthur == Seat("right")  # the rat turns; he has not moved yet
        sprung = next(i for i, s in enumerate(bolt) if not isinstance(s.arthur, Seat))
        assert sprung - 1 in R.SPRING_DELAYS


def test_tables_turn_on_about_a_quarter_of_rounds_and_repeat_for_a_round():
    turned = [R.turned(ctx(n)) for n in range(400)]
    assert 0.18 <= sum(turned) / len(turned) <= 0.32
    assert [R.turned(ctx(n)) for n in range(400)] == turned
    for n in (TURNED_ROUND, 7, 123):
        assert shots(n) == shots(n)


def test_rounds_vary_the_quiet_part():
    quiets = [tuple(part(shots(n), "quiet")) for n in range(12)]
    assert len(set(quiets)) == len(quiets)
    # Not just the length: where the rat is differs too.
    prey = [tuple(s.prey for s in q[-120:]) for q in quiets]
    assert len(set(prey)) == len(prey)


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_no_sprite_moves_faster_than_its_limit(n, length):
    ss = shots(n, length)
    for a, b in itertools.pairwise(ss):
        if isinstance(a.prey, Sprite) and isinstance(b.prey, Sprite):
            limit = RAT_BOLT_MAX if b.part == "bolt" else RAT_MAX
            assert abs(b.prey.x - a.prey.x) <= limit, (b.part, a.prey.x, b.prey.x)
        if isinstance(a.arthur, Sprite) and isinstance(b.arthur, Sprite):
            assert abs(b.arthur.x - a.arthur.x) <= ARTHUR_MAX, b.part
        if isinstance(a.arthur, Seat) and isinstance(b.arthur, Seat):
            assert a.arthur.dx == b.arthur.dx  # seated, he never slides


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_two_never_touch(n, length):
    for s in shots(n, length):
        assert gap(cells(s.arthur, CFG), cells(s.prey, CFG)) >= 1


def test_changed_frames_stay_under_budget():
    for n in range(20):
        cue = Cue(kind="scene", at=NOW, length_s=36.0, n=n, name="rat")
        frames = [render(READY.__class__(**{**READY.__dict__, "cue": cue}),
                         NOW + timedelta(microseconds=k * 125_000), CFG, variant="arthur")
                  for k in range(show.slot_frames(cue) + 1)]
        changed = sum(a != b for a, b in itertools.pairwise(frames))
        assert changed <= CHANGED_FRAME_BUDGET, (n, changed)
