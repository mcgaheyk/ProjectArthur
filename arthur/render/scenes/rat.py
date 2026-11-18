"""The rat. A small rat creeps up on Arthur, bolts, and the chase crosses the empty stage.

Three parts (D-0019; motion from ``docs/research/2026-11-18-led-animation-and-idle-cadence.md``:
travelling motion is the most distracting kind, small motion in one place the least):

1. **Quiet**, whatever the burst and the settle leave (about 22 s of a 36 s slot). Arthur sits
   at his spot and watches. A rat's nose peeks in at the right edge, withdraws, peeks again,
   then creeps along the floor towards him in short darts (3 or 4 frames at 2 columns a
   frame) with freezes between (6 to 14 frames), sniffing, looking back over its shoulder or
   stepping back now and then, and ends frozen 6 columns clear of his tail. Arthur mostly
   stares right, with blinks, an ear twitch and a raised tail, never on a frame the rat moves.
2. **Burst**, fixed: a 4-frame standoff; the rat turns and bolts right, Arthur springs after
   it a frame or two later; the stage is empty; the rat scurries back across the whole stage
   right to left with Arthur galloping after it; empty again.
3. **Settle**, fixed: Arthur strolls back in from the left, sits, squints, pleased with
   himself, blinks, and rests.

**Tables turned**, one round in four (seeded pick ``"ending"``): on the way back Arthur
comes first, flat out, with the rat trotting after him; it stops mid-stage, looks both ways
and strolls off left. After the empty beat seated Arthur peeks in from the left edge, then
tiptoes back to his spot. This ending is longer; the quiet part gives up the difference.

The scene is built as one :class:`Shot` per frame (:func:`shots`), then joined into beats.
Everything varies with the round's seeded helpers, and the quiet part stretches or shrinks to
the slot (24, 36 and 54 s all work).
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from arthur.render.idle_show import STAGE_W, Beat, SceneContext, mirror, scene
from arthur.render.scenes._common import (
    FLOOR,
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

# =========================================================================================
# Art: side view, facing right; mirrored to face left. Feet on the stage's floor row.
# =========================================================================================

#: Legs apart (the standing and frozen pose).
RAT_A = (
    "....rN....",
    ".rrrrrrr..",
    "NNrrrrr.rN",
    "..r....r..",
)
#: Legs together, tail up (the other stride).
RAT_B = (
    "....rN....",
    "Nrrrrrrr..",
    ".Nrrrrr.rN",
    "...r..r...",
)
#: Frozen, nose lifted (the sniff).
RAT_SNIFF = (
    "....rN...N",
    ".rrrrrrrr.",
    "NNrrrrr...",
    "..r....r..",
)
RAT_W = len(RAT_A[0])
RAT_TOP = FLOOR - len(RAT_A) + 1


def rat_at(art: tuple[str, ...], x: int, *, left: bool = True) -> Sprite:
    """The rat in ``art`` with its left edge at column ``x``, facing left (or right)."""
    return Sprite(mirror(art) if left else art, x, RAT_TOP)


# =========================================================================================
# Geometry and timing (frames at SHOW_FPS)
# =========================================================================================

#: Rightmost stage column seated Arthur lights (the tip of his tail, at rest or lifted).
ARTHUR_R = 17
#: Where the creep ends: the rat's nose 6 unlit columns clear of his tail.
END_X = ARTHUR_R + 7
#: Where the peeks stop: the first only the nose shows, later ones the ear too; the last
#: peek is where the creep starts (an even 20 columns from END_X, so darts of 2 land on it).
FIRST_PEEK_X, PEEK_X, CREEP_X = 47, 45, END_X + 20
#: Columns a frame: sliding in and out to peek, and darting.
PEEK_SPEED = DART_SPEED = 2
#: Frames of a dart (at DART_SPEED), and of the holds between (scaled down in a short slot).
DART_MIN, DART_MAX = 3, 4
FREEZE_MIN, FREEZE_MAX = 6, 14
PEEK_MIN, PEEK_MAX = 6, 14
GONE_MIN, GONE_MAX = 6, 16
#: Frames of the rat's gestures while frozen.
SNIFF_FRAMES = 2
LOOK_BACK_FRAMES = 4
#: Shortest any hold may be squeezed to when the slot is short.
SQUEEZE_MIN = 4
#: Arthur alone before the rat shows: at least this, at most about 40 % of the quiet part.
LEAD_MIN = 8
LEAD_SHARE = 0.4
#: In a long slot the rat gets more peeks and steps back, then longer holds (up to this
#: factor), before Arthur's lead grows past LEAD_SHARE.
MAX_PEEKS, MAX_BACKS, MAX_STRETCH = 4, 3, 1.5

#: The burst.
STANDOFF = 4
BOLT_SPEED = 5
SPRING_SPEED = 4
SPRING_DELAYS = (1, 2)
GONE_FRAMES = 6
SCURRY_SPEED = 4
GALLOP_SPEED = 5
CHASE_LAG = 6
#: Tables turned: Arthur flees, the rat trots after him, stops mid-stage, looks both ways.
FLEE_SPEED = 5
TROT_SPEED = 3
TROT_LAG = 8
MID_X = 20
LOOK_HOLD = 4
STROLL_SPEED = 2

#: The settle: walk in from the left edge, sit, squint, blink, rest.
WALK_FROM = -10  # only his ear tip on the stage (WALK_A's last lit column is 10)
SAT_FRAMES = 6
SQUINT_FRAMES = SHOW_FPS
BLINK_GAP = 4
BLINK_FRAMES = 2
REST_END = 8
SETTLE = 6 * SHOW_FPS
#: Tables turned: the peek from the left edge, and the tiptoe (1 column every 2 frames).
PEEK_DX = -9
PEEK_FRAMES = SHOW_FPS
TIPTOE_FROM = -5
TIPTOE_FRAMES_PER_PX = 2

#: How often the tables turn: one round in this many.
TURNED_ONE_IN = 4

# Arthur's gestures while he watches: (poses with frames, weight). The lead (before the rat
# shows) uses the home page's gestures.
WATCH_GESTURES: tuple[tuple[tuple[tuple[str, int], ...], int], ...] = (
    ((("blink", 2),), 3),
    ((("ear", 3),), 2),
    ((("right_half", 1), ("right_lift", 4), ("right_half", 1)), 2),
)
LEAD_GESTURES: tuple[tuple[tuple[tuple[str, int], ...], int], ...] = (
    ((("blink", 2),), 3),
    ((("ear", 3),), 2),
    ((("left", 8),), 2),
    ((("squint", 8),), 1),
    ((("half", 1), ("lift", 4), ("half", 1)), 1),
)


def turned(ctx: SceneContext) -> bool:
    """Whether this round plays the tables-turned ending."""
    return ctx.pick("ending", TURNED_ONE_IN) == 0


# =========================================================================================
# The quiet part
# =========================================================================================


def _plan_rat(
    rng: random.Random, squeeze: float, peeks: int, backs: int
) -> list[Sprite | None]:
    """The rat for each frame of its part of the quiet: peeks, then the creep to END_X."""
    out: list[Sprite | None] = []

    def hold(lo: int, hi: int) -> int:
        return max(SQUEEZE_MIN, round(rng.randint(lo, hi) * squeeze))

    def frozen(x: int, n: int, *, actions: bool, tail: int = 2) -> None:
        """``n`` frames frozen at ``x``; maybe a sniff or a look back inside, never in the
        first two or the last ``tail`` frames."""
        roll = rng.random()
        act = None
        if actions and roll < 0.35:
            act = ("sniff", SNIFF_FRAMES)
        elif actions and roll < 0.6:
            act = ("look", LOOK_BACK_FRAMES)
        start = -1
        if act and n - act[1] - tail >= 2:
            start = rng.randint(2, n - act[1] - tail)
        for i in range(n):
            if start >= 0 and start <= i < start + act[1]:
                out.append(rat_at(RAT_SNIFF, x) if act[0] == "sniff"
                           else rat_at(RAT_A, x, left=False))
            else:
                out.append(rat_at(RAT_A, x))

    # Peeks: slide in, hold, slide back out and stay gone; the last one stays and creeps.
    xs = [CREEP_X] if peeks == 1 else [FIRST_PEEK_X, *[PEEK_X] * (peeks - 2), CREEP_X]
    for k, px in enumerate(xs):
        for x in [*range(STAGE_W - PEEK_SPEED, px, -PEEK_SPEED), px]:
            out.append(rat_at(RAT_A, x))
        frozen(px, hold(PEEK_MIN, PEEK_MAX), actions=px != FIRST_PEEK_X)
        if k < len(xs) - 1:
            for x in range(px + PEEK_SPEED, STAGE_W, PEEK_SPEED):
                out.append(rat_at(RAT_B, x))
            out.extend([None] * hold(GONE_MIN, GONE_MAX))

    # The creep: darts that add up to 20 columns plus 2 for each step back.
    total = (CREEP_X - END_X) // DART_SPEED + backs
    counts = [m for m in range(1, total + 1)
              if DART_MIN * m <= total <= DART_MAX * m and m > backs]
    m = rng.choice(counts)
    darts = [DART_MIN] * m
    for i in rng.sample(range(m), total - DART_MIN * m):
        darts[i] += 1
    back_before = set(rng.sample(range(1, m), backs)) if backs else set()
    x = CREEP_X
    for d, n in enumerate(darts):
        for i in range(n):  # strides alternate, landing on the frozen pose
            x -= DART_SPEED
            out.append(rat_at(RAT_A if (n - 1 - i) % 2 == 0 else RAT_B, x))
        freeze = hold(FREEZE_MIN, FREEZE_MAX)
        if d + 1 in back_before:
            settle_n = rng.randint(2, 3)
            frozen(x, max(SQUEEZE_MIN, freeze - 1 - settle_n), actions=True)
            x += DART_SPEED
            out.append(rat_at(RAT_B, x))
            out.extend([rat_at(RAT_A, x)] * settle_n)
        else:
            frozen(x, freeze, actions=True, tail=3 if d == m - 1 else 2)
    assert x == END_X
    return out


def _changes(seq: Sequence[object]) -> set[int]:
    return {i for i in range(1, len(seq)) if seq[i] != seq[i - 1]}


def _pick_gesture(rng: random.Random, table) -> tuple[tuple[str, int], ...]:
    return rng.choices([g for g, _ in table], [w for _, w in table])[0]


def _arthur_poses(
    rng: random.Random, rat: Sequence[Sprite | None], lead: int
) -> list[str]:
    """Arthur's pose for each frame of the quiet part (the rat's frames start at ``lead``)."""
    n = len(rat)
    poses = ["rest"] * n
    # The lead: still glancing right from the opening, then resting, with a gesture or two.
    first = min(lead, rng.randint(SHOW_FPS, 2 * SHOW_FPS))
    poses[:first] = ["right"] * first
    p = first + rng.randint(2 * SHOW_FPS, 4 * SHOW_FPS)
    while True:
        g = _pick_gesture(rng, LEAD_GESTURES)
        length = sum(f for _, f in g)
        if p + length > lead - SHOW_FPS:
            break
        for pose, f in g:
            poses[p:p + f] = [pose] * f
            p += f
        p += rng.randint(2 * SHOW_FPS, 5 * SHOW_FPS)
    if lead >= n:
        return poses
    # He notices the rat a moment after it first shows, then watches it.
    moves = _changes(rat) | {lead}
    react = lead + rng.randint(2, 4)
    while react in moves:
        react += 1
    poses[react:] = ["right"] * (n - react)
    p = react + rng.randint(SHOW_FPS, 2 * SHOW_FPS)
    while p < n:
        g = _pick_gesture(rng, WATCH_GESTURES)
        length = sum(f for _, f in g)
        s = p
        # The whole gesture inside a stretch where the rat keeps still.
        while s + length < n - 2 and any(f in moves for f in range(s, s + length + 1)):
            s += 1
        if s + length >= n - 2:
            break
        for pose, f in g:
            poses[s:s + f] = [pose] * f
            s += f
        p = s + rng.randint(SHOW_FPS, 3 * SHOW_FPS)
    return poses


def quiet_shots(ctx: SceneContext, frames: int) -> list[Shot]:
    """The quiet part, exactly ``frames`` frames."""
    if frames <= 0:
        return []
    shape = ctx.rng("shape")
    peeks = shape.choice((2, 2, 3))
    backs = shape.choices((0, 1, 2), (4, 5, 2))[0]
    squeeze = 1.0
    cap = max(LEAD_MIN * 3, round(frames * LEAD_SHARE))
    rat: list[Sprite | None] = []
    for _ in range(40):
        rat = _plan_rat(ctx.rng("rat"), squeeze, peeks, backs)
        lead = frames - len(rat)
        if lead < LEAD_MIN:  # too long for the slot: fewer peeks, fewer steps back, shorter
            if backs:
                backs -= 1
            elif peeks > 1:
                peeks -= 1
            elif squeeze > 0.3:
                squeeze -= 0.1
            else:
                break
        elif lead > cap and (peeks < MAX_PEEKS or backs < MAX_BACKS
                             or squeeze < MAX_STRETCH):  # a long slot: more of the rat
            if peeks < MAX_PEEKS and peeks <= backs:
                peeks += 1
            elif backs < MAX_BACKS:
                backs += 1
            else:
                squeeze = round(squeeze + 0.1, 1)
        else:
            break
    lead = frames - len(rat)
    if lead < 0:  # never on the slots the show uses; keep the end, where the rat is close
        rat, lead = rat[-frames:], 0
    full = [None] * lead + rat
    poses = _arthur_poses(ctx.rng("arthur"), full, lead)
    return [Shot(Seat(pose), r, "quiet") for pose, r in zip(poses, full, strict=True)]


# =========================================================================================
# The burst and the settle
# =========================================================================================


def burst_shots(ctx: SceneContext) -> list[Shot]:
    """Standoff, bolt and spring, empty, the way back (either ending), empty."""
    shots = seated_shots(STANDOFF, "right", "standoff", rat_at(RAT_A, END_X))
    # The rat turns and bolts; Arthur springs after it a frame or two later.
    shots.append(Shot(Seat("right"), rat_at(RAT_A, END_X, left=False), "bolt"))
    delay = SPRING_DELAYS[ctx.pick("spring", SPRING_DELAYS)]
    runs = run_shots(SPOT_X, SPRING_SPEED, right=True, part="bolt")
    j = 0
    while True:
        x = END_X + BOLT_SPEED * (j + 1)
        rat = rat_at(RAT_B if j % 2 == 0 else RAT_A, x, left=False) if x < STAGE_W else None
        cat = Seat("right") if j < delay else (runs[j - delay] if j - delay < len(runs)
                                                else None)
        if rat is None and cat is None:
            break
        shots.append(Shot(cat, rat, "bolt"))
        j += 1
    shots += [Shot(None, None, "gone")] * GONE_FRAMES
    shots += _back_turned() if turned(ctx) else _back_chase()
    shots += [Shot(None, None, "gone")] * GONE_FRAMES
    return shots


def _back_chase() -> list[Shot]:
    """The rat scurries right to left across the empty stage, Arthur galloping after it."""
    rats = []
    x = STAGE_W - SCURRY_SPEED
    while x + RAT_W > 0:
        rats.append(rat_at(RAT_A if len(rats) % 2 == 0 else RAT_B, x))
        x -= SCURRY_SPEED
    cats = run_shots(STAGE_W - GALLOP_SPEED, GALLOP_SPEED, right=False, part="back")
    n = max(len(rats), CHASE_LAG + len(cats))
    return [Shot(cats[t - CHASE_LAG] if 0 <= t - CHASE_LAG < len(cats) else None,
                 rats[t] if t < len(rats) else None, "back") for t in range(n)]


def _back_turned() -> list[Shot]:
    """Tables turned: Arthur flees right to left, the rat trots after him, stops mid-stage,
    looks one way and the other, and strolls off left."""
    cats = run_shots(STAGE_W - FLEE_SPEED, FLEE_SPEED, right=False, part="back")
    rats: list[Sprite] = []
    x = STAGE_W - TROT_SPEED
    while x >= MID_X:
        rats.append(rat_at(RAT_A if (len(rats) // 2) % 2 == 0 else RAT_B, x))
        x -= TROT_SPEED
    x = rats[-1].x
    rats += [rat_at(RAT_A, x)] * LOOK_HOLD
    rats += [rat_at(RAT_A, x, left=False)] * LOOK_HOLD
    rats += [rat_at(RAT_A, x)] * LOOK_HOLD
    i = 0
    x -= STROLL_SPEED
    while x + RAT_W > 0:
        rats.append(rat_at(RAT_B if (i // 2) % 2 == 0 else RAT_A, x))
        x -= STROLL_SPEED
        i += 1
    n = max(len(cats), TROT_LAG + len(rats))
    return [Shot(cats[t] if t < len(cats) else None,
                 rats[t - TROT_LAG] if 0 <= t - TROT_LAG < len(rats) else None, "back")
            for t in range(n)]


def settle_shots(ctx: SceneContext) -> list[Shot]:
    """Back to his spot and sitting, ending at rest."""
    if turned(ctx):
        shots = [Shot(Seat("right", PEEK_DX), None, "settle")] * PEEK_FRAMES
        shots += [Shot(s, None, "settle")
                  for s in walk_sprites(TIPTOE_FROM, SPOT_X, TIPTOE_FRAMES_PER_PX)]
        shots += seated_shots(SAT_FRAMES, "rest", "settle")
        shots += seated_shots(BLINK_FRAMES, "blink", "settle")
        shots += seated_shots(REST_END + 2, "rest", "settle")
        return shots
    shots = [Shot(s, None, "settle") for s in walk_sprites(WALK_FROM, SPOT_X)]
    shots += seated_shots(SAT_FRAMES, "rest", "settle")
    shots += seated_shots(SQUINT_FRAMES, "squint", "settle")
    shots += seated_shots(BLINK_GAP, "rest", "settle")
    shots += seated_shots(BLINK_FRAMES, "blink", "settle")
    shots += seated_shots(SETTLE - len(shots), "rest", "settle")
    return shots


def shots(ctx: SceneContext) -> list[Shot]:
    """The whole scene, one shot per body frame."""
    tail = burst_shots(ctx) + settle_shots(ctx)
    return quiet_shots(ctx, ctx.frames - len(tail)) + tail


@scene("rat")
def rat(ctx: SceneContext) -> Sequence[Beat]:
    return shots_to_beats(ctx.cfg, shots(ctx))
