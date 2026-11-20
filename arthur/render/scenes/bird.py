"""The bird. A small bird potters about above Arthur; he chases it and leaps across at it.

Three parts (D-0019; motion from ``docs/research/2026-11-18-led-animation-and-idle-cadence.md``:
travelling motion is the most distracting kind, small motion in one place the least):

1. **Quiet**, whatever the burst and the settle leave (about 22 s of a 36 s slot). Arthur sits
   at his spot and watches, mostly glancing right, with blinks, an ear twitch and a raised
   tail. After a second or two a bird flutters in from the top right and potters about the
   upper right of the stage: it hovers in one place for 8 to 20 frames, drifts to another
   over 4 to 8, and now and then lands on the floor at the far right for a second or two,
   wings folded, and flies back up. It flaps all the time it is in the air: wing up and wing
   down every 2 frames, its body a row higher on the downstroke (the bob). It ends hovering
   at :data:`QUIET_END`.
2. **Burst**, fixed: the bird drifts closer and hovers above the middle of the stage while
   Arthur is dead still; it flutters off to the right and he runs after it on all fours; it
   stops high at the right and he stops well short of it; the wind-up (crouched, rear
   wiggling); the leap, seen from the side: he springs off, rises in two frames tilted up
   towards it, and at the top flings his forelegs open just in front of it; it hops up and
   away, his paws clap shut on empty air, and he hangs there while it leaves by the top
   right corner. Two frames falling forward, front paws leading; he lands front paws first,
   squashes into a crouch and sits, facing us, where he came down, 11 columns on from where
   he jumped. A feather zig-zags down onto his head.
3. **Settle**, fixed: he sits a second with the feather on his head, blinks, and walks home
   (the feather drifts off behind him), sits at his spot and rests.

**Perched**, one round in four (seeded pick ``"ending"``): after the feather settles the bird
flutters back down and perches on his head between his ears for about a second while he sits
frozen, then flies off out of the top right; he blinks and walks home. This ending is longer;
the quiet part gives up the difference.

The scene is built as one :class:`Shot` per frame (:func:`shots`), then joined into beats.
Everything in the quiet part varies with the round's seeded helpers, and it stretches or
shrinks to the slot (24, 36 and 54 s all work).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arthur.compat import tuple_record, zip_strict
from arthur.render.idle_show import STAGE_W, Beat, SceneContext, mirror, scene
from arthur.render.scenes._common import (
    CROUCH_A,
    CROUCH_B,
    FLOOR,
    LAUNCH,
    LEAP_DOWN_A,
    LEAP_DOWN_B,
    LEAP_UP_A,
    LEAP_UP_B,
    POUNCE_CLAP,
    POUNCE_OPEN,
    SPOT_X,
    Seat,
    Shot,
    Sprite,
    run_shots,
    seated_shots,
    shots_to_beats,
    walk_sprites,
)
from arthur.timebase import SHOW_FPS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arthur.compat import Random

# =========================================================================================
# Art: facing left; mirrored to face right.
# =========================================================================================

#: Wing up. Drawn at the bird's anchor row.
BIRD_A = (
    "...vv.",
    "Uvvvv.",
    ".vvvvv",
    "......",
)
#: Wing down. Drawn a row higher than the anchor (the downstroke lifts the body), so its lit
#: rows are the same three as BIRD_A's.
BIRD_B = (
    "......",
    "Uvvvvv",
    ".vvvv.",
    "..vv..",
)
#: On the floor, wings folded: three rows, the bottom one on the floor.
BIRD_SIT = (
    ".vv...",
    "Uvvvv.",
    ".vvvvv",
)
#: Perched on Arthur's head, between his ears: five wide so it fits there.
BIRD_PERCH = (
    ".vv..",
    "Uvvvv",
    ".vvvv",
)
BIRD_W = len(BIRD_A[0])
#: The feather: one pixel in the bird's colour.
FEATHER = ("v",)


@tuple_record("x y how left")
class Bird:
    """Where the bird is on one frame: its anchor (the top-left of its three lit rows), whether
    it is flying, sitting on the floor or perched, and which way it faces."""

    x: int
    y: int
    how: str = "fly"  # "fly", "sit" or "perch"
    left: bool = True


def bird_sprite(b: Bird | None, t: int) -> Sprite | None:
    """The bird on body frame ``t``: in flight the wing beats every 2 frames of the scene."""
    if b is None:
        return None
    if b.how == "fly":
        down = (t // 2) % 2 == 1
        art, y = (BIRD_B, b.y - 1) if down else (BIRD_A, b.y)
    elif b.how == "sit":
        art, y = BIRD_SIT, b.y
    else:
        art, y = BIRD_PERCH, b.y
    return Sprite(art if b.left else mirror(art), b.x, y)


def feather_at(x: int, y: int) -> Sprite:
    return Sprite(FEATHER, x, y)


# =========================================================================================
# Geometry and timing (frames at SHOW_FPS)
# =========================================================================================

#: Where the bird potters in the quiet part: anchors (its lit rows are y .. y + 2). Its
#: leftmost lit column is at least 24, six columns clear of seated Arthur's tail (17).
AIR_X0, AIR_X1 = 24, STAGE_W - BIRD_W
AIR_Y0, AIR_Y1 = 0, 4
#: Its floor visits: the far right, three lit rows ending on the floor.
FLOOR_X0, FLOOR_X1 = 40, STAGE_W - BIRD_W
FLOOR_Y = FLOOR - len(BIRD_SIT) + 1
#: It flutters in from just off the top right.
ENTER_X, ENTER_Y = STAGE_W, 0
#: Columns and rows a frame in the quiet part. A row a frame is the most the anchor may move
#: up or down: with the bob on top the art moves at most 2.
DRIFT_DX, DRIFT_DY = 2, 1
#: The quiet part's holds and moves, in frames.
HOVER_MIN, HOVER_MAX = 8, 20
DRIFT_MIN, DRIFT_MAX = 4, 8
SIT_MIN, SIT_MAX = SHOW_FPS, 2 * SHOW_FPS
#: Chance of a floor visit instead of a drift (never twice in a row).
FLOOR_CHANCE = 0.45
#: Arthur alone before the bird shows, frames (even).
LEAD_MIN, LEAD_MAX = 8, 16
#: Where the quiet part leaves the bird, hovering.
QUIET_END = (34, 1)

#: The burst. (a) It drifts closer, a column a frame, and hovers above the middle of the stage.
MID = (29, 2)
APPROACH_SPEED = 1
MID_HOVER = 6
#: (b) It flutters off to the right; Arthur runs after it a frame or two later, and both stop:
#: the bird hovering at HOVER, high at the right, Arthur crouched at CROUCH_X, well short of
#: it, so the leap has somewhere to go. The bird's five steps take no longer than his run.
FLEE_STEPS = (3, 3, 3, 3, 3)
SPRING_DELAYS = (1, 2)
RUN_SPEED = 3
CROUCH_X = SPOT_X + 4 * RUN_SPEED  # 14: four strides of 3 from his spot
CROUCH_Y = FLOOR - len(CROUCH_A) + 1
#: (d) The leap, side-on, facing right: the launch, two frames rising, then the top, where he
#: hangs. Each is (art, column, row) of the art's top-left; he travels 11 columns right.
RISE: tuple[tuple[tuple[str, ...], int, int], ...] = (
    (LAUNCH, CROUCH_X, FLOOR - len(LAUNCH) + 1),  # top row 6, hind feet still down
    (LEAP_UP_A, CROUCH_X + 4, 3),
    (LEAP_UP_B, CROUCH_X + 8, 1),
)
TOP_X, TOP_Y = CROUCH_X + 11, 0
#: The bird hovers just in front of his open paws: one unlit column beyond the upper paw
#: (POUNCE_OPEN's last column), level with the opening of the V.
HOVER = (TOP_X + len(POUNCE_OPEN[0]) + 1, 1)
#: Frames it hovers there before it turns to face him.
TURN_AFTER = 2
#: (c) The wind-up: crouched, the rear wiggling, each crouch frame held 2 frames.
WINDUP = 8
WIGGLE_HOLD = 2
#: At the top: paws flung open (the bird hops away on the first of these frames), then
#: clapped shut on empty air and held, a cartoon hang.
OPEN_HOLD = 2
CLAP_HOLD = 5
#: The bird hops up and away, 2 columns and a row a frame, and leaves by the top right.
ESCAPE_STEP = (2, -1)
#: (e) Two frames falling, front paws leading; then, landed: the front paws touch the floor
#: first, the rear comes down into a crouch, and he sits facing us where he came down.
DROP: tuple[tuple[tuple[str, ...], int, int], ...] = (
    (LEAP_DOWN_A, TOP_X + 1, 1),
    (LEAP_DOWN_B, TOP_X + 5, 4),
)
LAND_X = TOP_X + 5  # 30: the landing crouch's column
TOUCH = (LEAP_DOWN_B, LAND_X, FLOOR - len(LEAP_DOWN_B) + 1)
SQUASH = 2
#: Seated, his body (stage columns dx .. dx + 12) is where the crouch's was; his tail reaches
#: dx + 17 = 48, inside the stage.
LAND_DX = LAND_X + 1
#: (f) The feather: one column left or right and one row down every 2 frames, from the top,
#: starting as he starts to fall; it rests on his head between his ears.
FEATHER_HOLD = 2
HEAD_X = 6 + LAND_DX
FEATHER_PATH = ((HEAD_X, 0), (HEAD_X + 1, 1), (HEAD_X, 2))
#: The settle: a second with the feather on his head, a blink, a beat, the walk home.
FEATHER_SIT = SHOW_FPS
BLINK_FRAMES = 2
BEFORE_WALK = 2
WALK_FROM = LAND_DX + 1
#: The feather drifts off behind him as he starts walking, then is gone.
FEATHER_OFF = ((HEAD_X + 1, 3), (HEAD_X, 4), (HEAD_X + 1, 5))
#: At rest at his spot at the end.
REST_END = 12

#: Perched ending: the bird flies back in from the top right and sits on his head, between
#: his ears, for about a second; then it flies off the way it came.
PERCH = (LAND_DX + 4, 0)
PERCH_FROM = (STAGE_W, 0)
PERCH_HOLD = SHOW_FPS + 2
BLINK_AFTER_PERCH = 4
#: How often the bird perches: one round in this many.
PERCH_ONE_IN = 4

# Arthur's gestures while he watches: (poses with frames, weight). Every gesture and every gap
# is an even number of frames, so his changes land on the bird's wing beats.
WATCH_GESTURES: tuple[tuple[tuple[tuple[str, int], ...], int], ...] = (
    ((("blink", 2),), 3),
    ((("ear", 4),), 2),
    ((("right_half", 2), ("right_lift", 4), ("right_half", 2)), 2),
)
GAP_MIN, GAP_MAX = SHOW_FPS, 3 * SHOW_FPS


def perched(ctx: SceneContext) -> bool:
    """Whether this round plays the perched ending."""
    return ctx.pick("ending", PERCH_ONE_IN) == 0


# =========================================================================================
# The quiet part
# =========================================================================================


def _line(a: tuple[int, int], b: tuple[int, int], n: int) -> list[tuple[int, int]]:
    """``n`` points from just after ``a`` to ``b`` (the last is ``b``), evenly spaced."""
    return [(a[0] + round((b[0] - a[0]) * i / n), a[1] + round((b[1] - a[1]) * i / n))
            for i in range(1, n + 1)]


def _frames_to(a: tuple[int, int], b: tuple[int, int], dx: int = DRIFT_DX,
               dy: int = DRIFT_DY) -> int:
    """Fewest frames to fly from ``a`` to ``b`` at ``dx`` columns and ``dy`` rows a frame."""
    return max(-(-abs(b[0] - a[0]) // dx), -(-abs(b[1] - a[1]) // dy), 1)


def _fly(out: list[Bird | None], a: tuple[int, int], b: tuple[int, int], n: int) -> None:
    left = b[0] < a[0] or (b[0] == a[0] and (out[-1].left if out and out[-1] else True))
    out.extend(Bird(x, y, "fly", left) for x, y in _line(a, b, n))


def _plan_bird(rng: Random, n: int) -> list[Bird | None]:
    """The bird for each of the quiet part's ``n`` frames (None while it is not there yet):
    it flutters in, potters, and ends hovering at QUIET_END."""
    out: list[Bird | None] = []
    first = (rng.randint(AIR_X0 + 8, AIR_X1 - 2), rng.randint(AIR_Y0 + 1, AIR_Y1))
    enter_n = _frames_to((ENTER_X, ENTER_Y), first)
    final_n = _frames_to(first, QUIET_END) + HOVER_MIN
    lead = rng.randrange(LEAD_MIN, LEAD_MAX + 1, 2)
    lead = max(0, min(lead, n - enter_n - final_n))
    out.extend([None] * lead)
    if n - lead < enter_n + _frames_to(first, QUIET_END) + 2:
        # A very short quiet part: straight in to the end spot.
        enter_n = min(n - lead, _frames_to((ENTER_X, ENTER_Y), QUIET_END))
        _fly(out, (ENTER_X, ENTER_Y), QUIET_END, enter_n)
        out.extend([Bird(*QUIET_END)] * (n - len(out)))
        return out[:n]
    _fly(out, (ENTER_X, ENTER_Y), first, enter_n)
    here = first
    on_floor_last = False

    def room_after(extra: int, at: tuple[int, int]) -> bool:
        return len(out) + extra + _frames_to(at, QUIET_END) + HOVER_MIN <= n

    while True:
        hover = rng.randint(HOVER_MIN, HOVER_MAX)
        if not room_after(hover, here):
            break
        out.extend([Bird(*here)] * hover)
        if not on_floor_last and rng.random() < FLOOR_CHANCE:
            spot = (rng.randint(FLOOR_X0, FLOOR_X1), FLOOR_Y)
            down = max(DRIFT_MIN, _frames_to(here, spot))
            sit = rng.randint(SIT_MIN, SIT_MAX)
            up_to = (rng.randint(AIR_X0 + 10, AIR_X1), rng.randint(AIR_Y0, AIR_Y1))
            up = max(DRIFT_MIN, _frames_to(spot, up_to))
            if room_after(down + sit + up, up_to):
                _fly(out, here, spot, down)
                out.extend([Bird(*spot, "sit", out[-1].left)] * sit)
                _fly(out, spot, up_to, up)
                here, on_floor_last = up_to, True
                continue
        on_floor_last = False
        for _ in range(20):
            frames = rng.randint(DRIFT_MIN, DRIFT_MAX)
            to = (rng.randint(max(AIR_X0, here[0] - DRIFT_DX * frames),
                              min(AIR_X1, here[0] + DRIFT_DX * frames)),
                  rng.randint(max(AIR_Y0, here[1] - DRIFT_DY * frames),
                              min(AIR_Y1, here[1] + DRIFT_DY * frames)))
            if abs(to[0] - here[0]) >= 4:
                break
        if not room_after(frames, to):
            break
        _fly(out, here, to, frames)
        here = to
    # The way to the end spot, then hover there for whatever is left.
    _fly(out, here, QUIET_END, max(DRIFT_MIN // 2, _frames_to(here, QUIET_END)))
    out.extend([Bird(*QUIET_END)] * (n - len(out)))
    return out[:n]


def _pick_gesture(rng: Random, table) -> tuple[tuple[str, int], ...]:
    return rng.choices([g for g, _ in table], [w for _, w in table])[0]


def _arthur_poses(rng: Random, n: int, lead: int) -> list[str]:
    """Arthur's pose for each frame of the quiet part: glancing right, with gestures that start
    on even frames and last an even number of frames, none in the last second."""
    poses = ["right"] * n
    p = lead + rng.randrange(GAP_MIN, GAP_MAX + 1, 2)
    while True:
        g = _pick_gesture(rng, WATCH_GESTURES)
        length = sum(f for _, f in g)
        if p + length > n - SHOW_FPS:
            break
        for pose, f in g:
            poses[p:p + f] = [pose] * f
            p += f
        p += rng.randrange(GAP_MIN, GAP_MAX + 1, 2)
    return poses


def quiet_shots(ctx: SceneContext, frames: int) -> list[Shot]:
    """The quiet part, exactly ``frames`` frames."""
    if frames <= 0:
        return []
    birds = _plan_bird(ctx.rng("bird"), frames)
    lead = next((i for i, b in enumerate(birds) if b is not None), frames)
    poses = _arthur_poses(ctx.rng("arthur"), frames, lead)
    return [Shot(Seat(pose), bird_sprite(b, t), "quiet")
            for t, (pose, b) in enumerate(zip_strict(poses, birds))]


# =========================================================================================
# The burst and the settle
# =========================================================================================


def burst_shots(ctx: SceneContext, t0: int) -> list[Shot]:
    """From the end of the quiet part (body frame ``t0``) to his landing with the feather
    resting on his head."""
    shots: list[Shot] = []

    def add(arthur, bird: Bird | None, part: str, extra=None) -> None:
        shots.append(Shot(arthur, bird_sprite(bird, t0 + len(shots)), part, extra))

    # (a) It drifts closer and hovers above the middle; he is dead still.
    pts = _line(QUIET_END, MID, _frames_to(QUIET_END, MID, APPROACH_SPEED, DRIFT_DY))
    for x, y in pts:
        add(Seat("right"), Bird(x, y), "approach")
    for _ in range(MID_HOVER):
        add(Seat("right"), Bird(*MID), "approach")
    # (b) It flutters off to the right; a frame or two later he runs after it.
    delay = SPRING_DELAYS[ctx.pick("spring", SPRING_DELAYS)]
    runs = run_shots(SPOT_X, RUN_SPEED, right=True, part="chase",
                     frames=(CROUCH_X - SPOT_X) // RUN_SPEED)
    xs, ys = [MID[0]], [MID[1]]
    for i, s in enumerate(FLEE_STEPS):
        xs.append(xs[-1] + s)
        ys.append(ys[-1] + (-1 if ys[-1] > HOVER[1] else 0))
    assert (xs[-1], ys[-1]) == HOVER
    flee = [Bird(x, y, "fly", False) for x, y in zip_strict(xs[1:], ys[1:])]
    n = delay + len(runs)
    for j in range(n):
        if j < len(flee):
            b = flee[j]
        else:  # stopped; after a moment it turns round to face him
            b = Bird(*HOVER, "fly", left=j - len(flee) >= TURN_AFTER)
        cat = Seat("right") if j < delay else runs[j - delay]
        add(cat, b, "chase")
    hover = Bird(*HOVER)
    # (c) The wind-up: crouched well short of it, the rear wiggling.
    for i in range(WINDUP):
        art = CROUCH_A if (i // WIGGLE_HOLD) % 2 == 0 else CROUCH_B
        add(Sprite(art, CROUCH_X, CROUCH_Y), hover, "windup")
    # (d) The leap, side-on: the launch and two frames rising towards it.
    for art, x, y in RISE:
        add(Sprite(art, x, y), hover, "leap")
    # At the top his paws fly open in front of it; it hops up and away, and they clap shut
    # on empty air. He hangs there while it leaves by the top right.
    bx, by = HOVER
    for i in range(OPEN_HOLD + CLAP_HOLD):
        bx, by = bx + ESCAPE_STEP[0], by + ESCAPE_STEP[1]
        b = Bird(bx, by, "fly", False) if bx < STAGE_W else None
        art = POUNCE_OPEN if i < OPEN_HOLD else POUNCE_CLAP
        add(Sprite(art, TOP_X, TOP_Y), b, "top")
    # (e) Falling forward, front paws leading; the feather starts down from the top.
    feather = [feather_at(x, y) for x, y in FEATHER_PATH for _ in range(FEATHER_HOLD)]
    for art, x, y in DROP:
        add(Sprite(art, x, y), None, "drop", feather.pop(0))
    # (f) Landed: front paws first, the squash, then sitting where he came down; the feather
    # settles on his head.
    add(Sprite(*TOUCH), None, "land", feather.pop(0))
    for _ in range(SQUASH):
        add(Sprite(CROUCH_A, LAND_X, CROUCH_Y), None, "land", feather.pop(0) if feather else None)
    while feather:
        add(Seat("rest", LAND_DX), None, "land", feather.pop(0))
    return shots


def _perch_shots(t0: int) -> list[Shot]:
    """The bird flies back in, perches on his head (over the feather) and flies off."""
    shots: list[Shot] = []
    rest = feather_at(*FEATHER_PATH[-1])

    def add(arthur, bird: Bird | None, extra=rest) -> None:
        shots.append(Shot(arthur, bird_sprite(bird, t0 + len(shots)), "perch", extra))

    cat = Seat("rest", LAND_DX)
    n_in = _frames_to(PERCH_FROM, PERCH)
    for x, y in _line(PERCH_FROM, PERCH, n_in):
        add(cat, Bird(x, y, "fly", True))
    for _ in range(PERCH_HOLD):
        add(cat, Bird(*PERCH, "perch", True))
    for x, y in _line(PERCH, PERCH_FROM, n_in):
        b = Bird(x, y, "fly", False) if x < STAGE_W else None
        add(cat, b)
    for _ in range(BLINK_AFTER_PERCH):
        add(cat, None)
    return shots


def settle_shots(ctx: SceneContext, t0: int) -> list[Shot]:
    """With the feather on his head: a second's sit (or the perched bird), a blink, the walk
    home with the feather drifting off, and rest at his spot."""
    rest = feather_at(*FEATHER_PATH[-1])
    shots = [Shot(Seat("rest", LAND_DX), None, "settle", rest)] * FEATHER_SIT
    if perched(ctx):
        shots += _perch_shots(t0 + len(shots))
    shots += [Shot(Seat("blink", LAND_DX), None, "settle", rest)] * BLINK_FRAMES
    shots += [Shot(Seat("rest", LAND_DX), None, "settle", rest)] * BEFORE_WALK
    off = [feather_at(x, y) for x, y in FEATHER_OFF for _ in range(FEATHER_HOLD)]
    for i, s in enumerate(walk_sprites(WALK_FROM, SPOT_X)):
        shots.append(Shot(s, None, "settle", off[i] if i < len(off) else None))
    shots += seated_shots(REST_END, "rest", "settle")
    return shots


def tail_length(ctx: SceneContext) -> int:
    """Frames of the burst and the settle (they do not depend on where they start)."""
    return len(burst_shots(ctx, 0)) + len(settle_shots(ctx, 0))


def shots(ctx: SceneContext) -> list[Shot]:
    """The whole scene, one shot per body frame."""
    q = ctx.frames - tail_length(ctx)
    quiet = quiet_shots(ctx, q)
    burst = burst_shots(ctx, q)
    return quiet + burst + settle_shots(ctx, q + len(burst))


@scene("bird")
def bird(ctx: SceneContext) -> Sequence[Beat]:
    return shots_to_beats(ctx.cfg, shots(ctx))


