"""The bird scene (arthur/render/scenes/bird.py): what is particular to it.

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
from arthur.render.idle_show import mirror
from arthur.render.scenes import bird as B
from arthur.render.scenes._common import (
    CROUCH_A,
    CROUCH_B,
    POUNCE_CLAP,
    POUNCE_OPEN,
    Seat,
    Sprite,
    cells,
    gap,
)

CFG = default_config()
READY = FIXTURES["ready"].display_state(CFG)
SLOT_LENGTHS = (24, 36, 54)
ROUNDS = range(24)
#: Changed frames allowed in a 36 s slot of this scene, glance and wipes included. Measured
#: 176-189 over rounds 0-11: the bird beats its wings every 2 frames all the time it is in
#: the air (about 85 changes in the quiet part), the burst changes on nearly every frame
#: (about 45), the walk home about 30.
CHANGED_FRAME_BUDGET = 200
#: Pixels a frame: the bird (6 wide) 2, 3 when it flees; Arthur running (14 wide) or
#: leaping (14 to 18 wide) 5; the leap 4 rows.
BIRD_MAX, BIRD_FLEE_MAX, ARTHUR_MAX, LEAP_ROWS_MAX = 2, 3, 5, 4
FLEE_PARTS = ("chase", "top")
#: A round with each ending (round 2 is the first perched round).
PLAIN_ROUND, PERCHED_ROUND = 0, 2
FLYING = {B.BIRD_A, B.BIRD_B, mirror(B.BIRD_A), mirror(B.BIRD_B)}


def ctx(n: int, length: float = 36) -> show.SceneContext:
    frames = round(length * show.SHOW_FPS) - show.SCENE_FRAMES_AROUND
    return show.SceneContext("bird", CFG, n, frames)


def shots(n: int, length: float = 36) -> list:
    return B.shots(ctx(n, length))


def part(ss, name: str) -> list:
    return [s for s in ss if s.part == name]


def all_cases():
    return [(n, length) for n in ROUNDS for length in SLOT_LENGTHS]


def top_row(actor) -> int:
    return min(y for _, y in cells(actor, CFG))


def wing(sprite) -> str:
    return "up" if sprite.rows in (B.BIRD_A, mirror(B.BIRD_A)) else "down"


def test_the_rounds_named_in_the_report_give_each_ending():
    assert not B.perched(ctx(PLAIN_ROUND))
    assert B.perched(ctx(PERCHED_ROUND))


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_shots_fill_the_body_in_order(n, length):
    ss = shots(n, length)
    assert len(ss) == ctx(n, length).frames
    order = [k for k, _ in itertools.groupby(s.part for s in ss)]
    middle = ["settle", "perch", "settle"] if B.perched(ctx(n, length)) else ["settle"]
    assert order == ["quiet", "approach", "chase", "windup", "leap", "top", "drop", "land",
                     *middle]
    assert ss[0].arthur == Seat("right")
    # At rest at his spot, alone, for at least the last 8 frames.
    assert all(s == ss[-1] for s in ss[-8:])
    assert ss[-1].arthur == Seat("rest") and ss[-1].prey is None and ss[-1].extra is None


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_in_the_quiet_part_arthur_stays_put_and_the_bird_keeps_its_distance(n, length):
    quiet = part(shots(n, length), "quiet")
    assert len(quiet) > 5 * show.SHOW_FPS  # 24 s with the perched ending leaves about 5 s
    arthur = frozenset().union(*(cells(Seat(p), CFG) for p in
                                 ("right", "blink", "ear", "right_half", "right_lift")))
    arthur_cols = {x for x, _ in arthur}
    for s in quiet:
        assert isinstance(s.arthur, Seat) and s.arthur.dx == 0
        bird = cells(s.prey, CFG)
        assert gap(cells(s.arthur, CFG) | arthur, bird) >= 2
        assert not {x for x, _ in bird} & arthur_cols
    assert any(s.prey is not None for s in quiet)
    assert quiet[-1].prey is not None


def test_the_bird_potters_in_the_upper_right_and_sometimes_lands():
    landed = 0
    for n in ROUNDS:
        quiet = part(shots(n), "quiet")
        for s in quiet:
            if s.prey is None:
                continue
            c = cells(s.prey, CFG)
            if s.prey.rows == B.BIRD_SIT:
                assert min(x for x, _ in c) >= B.FLOOR_X0
                assert max(y for _, y in c) == show.STAGE_H - 1  # on the floor
            else:  # in the air (rows 0-6 when it hovers; lower on the way to the floor)
                assert min(x for x, _ in c) >= B.AIR_X0
        sits = [len(list(g)) for k, g in itertools.groupby(
            s.prey is not None and s.prey.rows == B.BIRD_SIT for s in quiet) if k]
        assert all(B.SIT_MIN <= k <= B.SIT_MAX for k in sits)
        landed += bool(sits)
        # It hovers: still (but for its wings) for at least 8 frames at a time.
        anchors = [(s.prey.x, s.prey.y + (wing(s.prey) == "down"))
                   for s in quiet if s.prey is not None and s.prey.rows in FLYING]
        holds = [(a, len(list(g))) for a, g in itertools.groupby(anchors)]
        assert max(k for _, k in holds) >= B.HOVER_MIN
        assert all(a[1] + 2 <= 6 for a, k in holds if k >= B.HOVER_MIN)
    assert 3 <= landed <= 20


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_wings_beat_every_two_frames(n, length):
    ss = shots(n, length)
    run: list[str] = []
    for s in [*ss, None]:
        flying = s is not None and isinstance(s.prey, Sprite) and s.prey.rows in FLYING
        if flying:
            run.append(wing(s.prey))
            continue
        # One stretch of flight: wing up, wing down, two frames each (shorter only at the ends).
        groups = [len(list(g)) for _, g in itertools.groupby(run)]
        assert all(k <= 2 for k in groups), groups
        assert all(k == 2 for k in groups[1:-1]), groups
        run = []


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_no_sprite_moves_faster_than_its_limit(n, length):
    ss = shots(n, length)
    for a, b in itertools.pairwise(ss):
        if isinstance(a.prey, Sprite) and isinstance(b.prey, Sprite):
            limit = BIRD_FLEE_MAX if b.part in FLEE_PARTS else BIRD_MAX
            assert abs(b.prey.x - a.prey.x) <= limit, (b.part, a.prey, b.prey)
            assert abs(b.prey.y - a.prey.y) <= BIRD_MAX, (b.part, a.prey, b.prey)
        if isinstance(a.arthur, Sprite) and isinstance(b.arthur, Sprite):
            assert abs(b.arthur.x - a.arthur.x) <= ARTHUR_MAX, b.part
            assert abs(top_row(b.arthur) - top_row(a.arthur)) <= LEAP_ROWS_MAX, b.part
        if isinstance(a.arthur, Seat) and isinstance(b.arthur, Seat):
            assert a.arthur.dx == b.arthur.dx  # seated, he never slides


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_bird_and_arthur_never_touch_except_on_the_perch(n, length):
    for s in shots(n, length):
        if s.part != "perch":
            assert gap(cells(s.arthur, CFG), cells(s.prey, CFG)) >= 1, s.part


@pytest.mark.parametrize("n", ROUNDS)
def test_the_chase_and_the_wind_up(n):
    ss = shots(n)
    approach = part(ss, "approach")
    assert all(s.arthur == Seat("right") for s in approach)  # dead still
    assert len(set(approach[-B.MID_HOVER:])) <= 2  # hovering: only its wings move
    chase = part(ss, "chase")
    sprung = next(i for i, s in enumerate(chase) if not isinstance(s.arthur, Seat))
    assert sprung in B.SPRING_DELAYS  # the bird goes first
    windup = part(ss, "windup")
    assert len(windup) >= 8
    arts = [s.arthur.rows for s in windup]
    assert set(arts) == {CROUCH_A, CROUCH_B}
    assert [len(list(g)) for _, g in itertools.groupby(arts)] == [2] * (len(windup) // 2)
    # He stops well short of the bird, which hovers ahead of him and high up.
    crouch, bird = cells(windup[0].arthur, CFG), cells(windup[0].prey, CFG)
    assert min(x for x, _ in bird) - max(x for x, _ in crouch) >= 10
    assert max(y for _, y in bird) < min(y for _, y in crouch)


def centre_x(actor) -> float:
    c = cells(actor, CFG)
    return sum(x for x, _ in c) / len(c)


@pytest.mark.parametrize("n", ROUNDS)
def test_the_leap_is_side_on_and_carries_him_right(n):
    ss = shots(n)
    windup, leap, top = part(ss, "windup"), part(ss, "leap"), part(ss, "top")
    drop, land = part(ss, "drop"), part(ss, "land")
    airborne = [s.arthur for s in leap + top + drop]
    assert all(isinstance(a, Sprite) for a in airborne)
    # Side-on, facing right: his eye is in the right third of every frame (a face-on cat
    # has two eyes, either side of the middle).
    eye = CFG.palette.chars["eye"]
    for a in airborne:
        eyes = [(i, r.index(eye)) for i, r in enumerate(a.rows) if eye in r]
        assert len(eyes) == 1 and eyes[0][1] >= 2 * len(a.rows[0]) // 3, a.rows
    # Rising towards the top right, then falling towards the bottom right.
    xs = [centre_x(s.arthur) for s in [windup[-1], *leap, top[0], *drop]]
    assert all(b > a for a, b in itertools.pairwise(xs)), xs
    assert 10 <= centre_x(top[0].arthur) - centre_x(windup[-1].arthur) <= 14
    rows = [top_row(s.arthur) for s in leap + top[:1]]
    assert all(b < a for a, b in itertools.pairwise(rows)), rows
    # He lands front paws first: the first landed frame touches the floor in front only.
    touch = cells(land[0].arthur, CFG)
    floor = [x for x, y in touch if y == show.STAGE_H - 1]
    assert floor and min(floor) > centre_x(land[0].arthur)
    assert isinstance(land[1].arthur, Sprite) and land[1].arthur.rows == CROUCH_A


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_open_and_the_clap_are_held_at_the_top_of_the_leap(n, length):
    ss = shots(n, length)
    top = part(ss, "top")
    assert len(top) >= 5
    arts = [s.arthur.rows for s in top]
    assert arts == [POUNCE_OPEN] * B.OPEN_HOLD + [POUNCE_CLAP] * B.CLAP_HOLD
    assert {(s.arthur.x, s.arthur.y) for s in top} == {(B.TOP_X, B.TOP_Y)}  # hanging still
    # The clap is the longest-held moving pose: no other airborne picture of him lasts as long.
    runs = [(k, len(list(g))) for k, g in itertools.groupby(
        s.arthur for s in ss if isinstance(s.arthur, Sprite))]
    clap_run = max(r for k, r in runs if k.rows == POUNCE_CLAP)
    assert all(r < clap_run for k, r in runs if k.rows != POUNCE_CLAP)
    # The highest he gets: no frame of him outside the top is higher.
    airborne = [s for s in ss if isinstance(s.arthur, Sprite)]
    high = top_row(top[0].arthur)
    assert all(top_row(s.arthur) >= high for s in airborne)
    assert all(top_row(s.arthur) > high for s in airborne
               if s.part not in ("top", "drop"))


@pytest.mark.parametrize("n", ROUNDS)
def test_the_bird_escapes_up_and_away_as_he_reaches_the_top(n):
    ss = shots(n)
    leap, top = part(ss, "leap"), part(ss, "top")
    before, first = leap[-1].prey, top[0]
    cat, bird = cells(first.arthur, CFG), cells(first.prey, CFG)
    assert first.prey.x > before.x and top_row(first.prey) < top_row(before)  # up and away
    assert gap(cat, bird) >= 1
    # It was just in front of his open paws: one unlit pixel short of the V, level with it.
    assert gap(cat, cells(before, CFG)) == 1
    paws = [y for x, y in cat if x >= max(x for x, _ in cat) - 1]
    assert min(paws) <= min(y for _, y in cells(before, CFG))
    assert max(paws) >= max(y for _, y in cells(before, CFG))
    # His paws clap shut on empty air, and it is gone before he starts to fall.
    claps = [s for s in top if s.arthur.rows == POUNCE_CLAP]
    assert claps[0].prey is None or min(x for x, _ in cells(claps[0].prey, CFG)) >= \
        show.STAGE_W - 2
    assert all(s.prey is None for s in claps[1:] + part(ss, "drop") + part(ss, "land"))


@pytest.mark.parametrize(("n", "length"), all_cases())
def test_the_feather_zig_zags_down_onto_his_head_and_drifts_off(n, length):
    ss = shots(n, length)
    path = [s.extra for s in ss if s.extra is not None]
    first = next(i for i, s in enumerate(ss) if s.extra is not None)
    assert ss[first].part == "drop" and path[0].y == 0
    assert all(f.rows == B.FEATHER for f in path)
    # It moves every 2 frames, one column sideways and one row down (until it rests).
    land = part(ss, "land")
    falling = [s.extra for s in ss[first:] if s.part in ("drop", "land")]
    for a, b in itertools.pairwise([k for k, _ in itertools.groupby(falling)]):
        assert abs(b.x - a.x) == 1 and b.y - a.y == 1
    assert [len(list(g)) for _, g in itertools.groupby(falling)][:-1] == [2] * 2
    # It rests on top of his head, between his ears, while he sits.
    rest = land[-1].extra
    head = cells(land[-1].arthur, CFG)
    assert (rest.x, rest.y + 1) in head and (rest.x, rest.y) not in head
    sat = [s for s in ss if s.part == "settle" and isinstance(s.arthur, Seat)
           and s.arthur.dx == B.LAND_DX]
    assert sat and all(s.extra == rest for s in sat)
    assert len(sat) >= show.SHOW_FPS
    # Gone within a few frames of his first step, and long before the end.
    walking = next(i for i, s in enumerate(ss) if s.part == "settle"
                   and isinstance(s.arthur, Sprite))
    assert all(s.extra is None for s in ss[walking + 8:])


@pytest.mark.parametrize("n", ROUNDS)
def test_he_lands_where_he_came_down_and_walks_home(n):
    ss = shots(n)
    land = part(ss, "land")
    crouch = next(s.arthur for s in land if isinstance(s.arthur, Sprite)
                  and s.arthur.rows == CROUCH_A)
    seat = next(s.arthur for s in land if isinstance(s.arthur, Seat))
    assert seat == Seat("rest", B.LAND_DX)
    # Seated, his body (tail aside) is where the landing crouch's was.
    body = [x for x, y in cells(seat, CFG) if x <= seat.dx + 12]
    assert abs(centre_x(crouch) - sum(body) / len(body)) <= 1
    # His tail still fits on the stage.
    assert max(x for x, _ in cells(seat, CFG)) <= show.STAGE_W - 1
    walk = [s.arthur for s in part(ss, "settle") if isinstance(s.arthur, Sprite)]
    xs = [a.x for a in walk]
    assert all(b - a == -1 for a, b in itertools.pairwise(xs))  # a column a frame, leftwards
    assert xs[-1] == 2


@pytest.mark.parametrize("n", [n for n in range(40) if B.perched(ctx(n))][:6])
def test_perched_ending(n):
    ss = shots(n)
    perch = part(ss, "perch")
    sat = [s for s in perch if s.prey is not None and s.prey.rows == B.BIRD_PERCH]
    assert B.PERCH_HOLD <= len(sat) <= B.PERCH_HOLD + 2  # about a second
    assert len(set(sat)) == 1
    # It sits on his head between his ears, touching nothing but his head.
    head, bird = cells(sat[0].arthur, CFG), cells(sat[0].prey, CFG)
    assert not head & bird
    assert any((x, y + 1) in head for x, y in bird)
    # He sits frozen throughout, and it comes and goes by the top right.
    assert {s.arthur for s in perch} == {Seat("rest", B.LAND_DX)}
    assert perch[0].prey.x >= show.STAGE_W - 2 and perch[-1].prey is None
    # Then he blinks and walks home.
    end = max(i for i, s in enumerate(ss) if s.part == "perch")
    assert ss[end + 1].arthur == Seat("blink", B.LAND_DX)


def test_the_bird_perches_on_about_a_quarter_of_rounds():
    p = [B.perched(ctx(n)) for n in range(400)]
    assert 0.18 <= sum(p) / len(p) <= 0.32


def test_rounds_vary_the_quiet_part_and_repeat_exactly():
    quiets = [tuple(part(shots(n), "quiet")) for n in range(12)]
    assert len(set(quiets)) == len(quiets)
    birds = [tuple(s.prey for s in q[-100:]) for q in quiets]
    assert len(set(birds)) == len(birds)
    for n in (PLAIN_ROUND, PERCHED_ROUND, 123):
        assert shots(n) == shots(n)


def test_changed_frames_stay_under_budget():
    for n in range(12):
        cue = Cue(kind="scene", at=NOW, length_s=36.0, n=n, name="bird")
        frames = [render(READY.__class__(**{**READY.__dict__, "cue": cue}),
                         NOW + timedelta(microseconds=k * 125_000), CFG, variant="arthur")
                  for k in range(show.slot_frames(cue) + 1)]
        changed = sum(a != b for a, b in itertools.pairwise(frames))
        assert changed <= CHANGED_FRAME_BUDGET, (n, changed)
