"""The stretch. Arthur yawns, strolls to the middle of the stage and does the cat stretch.

The calm one of the scenes (D-0019): no second animal, nothing moves fast, and the only
travelling motion is his slow walk out and back (a column every 2 frames). A real cat's
stretch comes in two halves, and so does his:

1. **Quiet**, whatever the rest leaves (about 12 s of a 36 s slot, never under 3 s). Arthur
   sits at his spot with the home page's small gestures on a seeded rhythm, and ends with a
   yawn of about a second: the yawn announces the stretch.
2. **The walk out**: he gets up and strolls right to the middle, where the bow will sit
   centred on the stage, and stands for a beat.
3. **The bow**: down through two in-betweens (the front paws walk forward, the chest sinks,
   the rump stays up and the tail starts to curl over) into the full bow: forelegs flat out
   along the floor, chest down, head low above the paws, the back sloping steeply up to the
   rump, hind legs straight, the tail hooked over with its tip drooping. Held about 3 s
   (seeded): eyes open for half a second, then squeezed shut; the tail tip uncurls and curls
   (once, or twice in a long hold) and once he pushes his rump a pixel higher for half a
   second. Every held picture lasts at least 4 frames, so at most two changes a second. His
   hind feet stay in the same two columns from the stand through the whole bow.
4. **The second half**: he rocks forward through one in-between into the hind-leg stretch,
   body level, head up, one hind leg straight out behind him along the floor, about a
   second; draws it in, stretches the other one higher for a little less, and stands, four
   columns further on than he started.
5. **Home**: a beat standing, he turns and strolls back facing left, sits at his spot,
   blinks and rests.

**Knead**, one round in four (seeded pick ``"ending"``): at the end of the held bow he kneads
the floor, the front paws taking turns 4 to 6 times, 2 or 3 frames each, before he rocks
forward. Also seeded: the quiet part, the length of the hold and its small movements, and on
some rounds (``"sit"``) he sits down in the middle facing us for 1 to 3 seconds, with a
blink, before walking home. The quiet part gives up whatever these take.

In a short slot (24 s) the extras give way so the quiet part keeps its 3 s: first the sit in
the middle, then the hold shrinks to 2 s, then the kneading to its shortest. The scene is
built as one :class:`Shot` per frame (:func:`shots`), then joined into beats.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arthur.compat import Random, zip_strict
from arthur.render.idle_show import STAGE_W, Beat, SceneContext, mirror, scene
from arthur.render.scenes._common import (
    FLOOR,
    SPOT_X,
    WALK_TOP,
    Seat,
    Shot,
    Sprite,
    seated_shots,
    shots_to_beats,
    walk_sprites,
)
from arthur.render.screens import WALK_A
from arthur.timebase import SHOW_FPS

if TYPE_CHECKING:
    from collections.abc import Sequence

# =========================================================================================
# Art: side view, facing right, feet on the bottom row. Same head, stripes and body as the
# walking cat (``WALK_A``): STAND is him with his legs straight, one column in from the left
# edge of the art, so it lines up with ``WALK_A`` drawn one column to the right. Every pose
# up to the hind-leg stretch keeps his hind feet in columns 2 and 3 of its art.
# =========================================================================================

#: Standing still, facing right (the walking cat with straight legs), 12 wide.
WALK_STAND = WALK_A[:5] + (".OO.....OO..", ".OO.....OO..")
#: The same in the stretch's columns (hind feet in columns 2-3).
STAND = tuple("." + r for r in WALK_STAND)
#: Into the bow, first: the front paws step forward, head and chest dip, rump up a row,
#: the tail leans back.
DOWN_A = (
    ".S............",
    "..O...........",
    "..O........O.O",
    "..O.OSOS...OOO",
    "..OOOOOOOSOOEO",
    "..OOOOOOOOOOON",
    "..OO......OO..",
    "..OO.......OOC",
)
#: Second: forelegs nearly flat, chest low, rump high, the tail starting to curl over.
DOWN_B = (
    ".SO..............",
    "...O.............",
    "..OOOS...........",
    "..OOOOOSO....O.O.",
    "..OO.OOOOSO..OOO.",
    "..OO...OOOOOOOEO.",
    "..OO.....OOOOOON.",
    "..OO.......OOOOOC",
)
#: The bow: rump high, chest down, forelegs flat out in front, head low above them, the
#: tail hooked over with the tip drooping. Eyes open.
BOW = (
    ".OO.................",
    "S..O................",
    "...O................",
    "..OOOO..............",
    "..OOOSOO............",
    "..OOOOOSOO..........",
    "..OO.OOOOSO.O.O.....",
    "..OO...OOOOOOOO.....",
    "..OO.....OOOOEON....",
    "..OO.......OOOOOOOCC",
)
#: The bow with his eyes squeezed shut (the held pose).
BOW_SHUT = tuple(r.replace("OEON", "OSON") for r in BOW)
#: Held, the tail tip uncurled: the hook opens and droops a row lower.
BOW_TAIL = ("..O.................", ".O.O................",
            "S..O................") + BOW_SHUT[3:]
#: Held, the deeper push: the rump a row higher on straighter hind legs, the head and paws
#: where they were.
BOW_DEEP = (
    ".OO.................",
    "S..O................",
    "...O................",
    "..OOOO..............",
    "..OOOSO.............",
    "..OOOOOO............",
    "..OO.OOSOO..........",
    "..OO...OOSO.O.O.....",
    "..OO...OOOOOOOO.....",
    "..OO.....OOOOSON....",
    "..OO.......OOOOOOOCC",
)
#: Kneading: the near paw lifted and pressed down beside his nose, then the far one pushed
#: out along the floor.
KNEAD_A = BOW_SHUT[:8] + ("..OO.....OOOOSON.OC.", "..OO.......OOOOOOC..")
KNEAD_B = BOW_SHUT[:8] + ("..OO.....OOOOSON....", "..OO.......OOOOOOOOC.")
#: Rocking forward out of the bow: body low and long, head coming up, front paws drawn
#: back under him, hind feet still where they were.
ROCK = (
    "...S..............",
    "...O..........O.O.",
    "...O.OSOSOS...OOO.",
    "...OOOOOOOOOOOOEO.",
    "..OOOOOOOOOOOOOON.",
    "..OO.........OOO..",
    "..OO..........OOC.",
)
#: The hind-leg stretch: weight forward, body level and a row higher, head up, one hind
#: leg straight out behind him along the floor, toes pointed.
HIND_A = (
    ".....S.......O.O.",
    ".....O.......OOO.",
    ".....O..OSOSOOEO.",
    "......OOOOOOOOON.",
    "......OOOOOOOOO..",
    "....OOOO.....OO..",
    "..OOO.OO.....OO..",
    "CO....OO.....OO..",
)
#: That leg drawn back in: standing tall on four straight legs.
HIND_IN = (
    ".....S.......O.O.",
    ".....O.......OOO.",
    ".....O..OSOSOOEO.",
    "......OOOOOOOOON.",
    "......OOOOOOOOO..",
    "......OO.....OO..",
    "......OO.....OO..",
    "......OO.....OO..",
)
#: The other leg, stretched out straight and level, off the floor.
HIND_B = (
    ".....S.......O.O.",
    ".....O.......OOO.",
    ".....O..OSOSOOEO.",
    "......OOOOOOOOON.",
    "...OOOOOOOOOOOO..",
    "COO...OO.....OO..",
    "......OO.....OO..",
    "......OO.....OO..",
)
#: Standing again, FORWARD columns on from where he started (the same picture as STAND).
FORWARD = 4
STAND_FWD = tuple("." * FORWARD + r for r in STAND)

# =========================================================================================
# Geometry and timing (frames at SHOW_FPS)
# =========================================================================================

#: Stage column of the stretch art's left edge: the bow (20 wide) centred on the stage.
STRETCH_X = (STAGE_W - len(BOW[0])) // 2
#: Where the walking cat stops so STAND lines up with him.
WALK_TO = STRETCH_X + 1
#: Where he walks home from (the walking cat over STAND_FWD), and the seat there.
HOME_FROM = WALK_TO + FORWARD
MID_DX = HOME_FROM - SPOT_X
#: Columns are walked one every this many frames, out and back.
WALK_FRAMES_PER_PX = 2

#: The quiet part: at least this long; it opens still glancing right (from the engine's
#: glance) and ends with the yawn and a short rest before he gets up.
QUIET_MIN = 3 * SHOW_FPS
GLANCE_MIN, GLANCE_MAX = 6, 12
REST_MIN, REST_MAX = 2 * SHOW_FPS, 5 * SHOW_FPS
YAWN_FRAMES = SHOW_FPS
AFTER_YAWN = 4
#: The home page's gestures, (poses with frames, weight).
GESTURES: tuple[tuple[tuple[tuple[str, int], ...], int], ...] = (
    ((("blink", 2),), 3),
    ((("ear", 3),), 2),
    ((("left", 8),), 2),
    ((("squint", 8),), 1),
    ((("half", 1), ("lift", 4), ("half", 1)), 1),
)

#: The beat standing before the bow, and each in-between on the way down.
STAND_BEAT = 4
DOWN_FRAMES = 2
#: The held bow: its length (seeded), and the shortest it is squeezed to in a short slot.
HOLD_MIN, HOLD_MAX = 24, 30
HOLD_SQUEEZED = 2 * SHOW_FPS
#: Inside the hold: eyes open first, every held picture at least SEG_MIN frames, the push
#: about half a second, a tail flick at most this long.
OPEN_MIN, OPEN_MAX = 4, 6
SEG_MIN = 4
DEEP_FRAMES = 4
TAIL_MAX = 8
#: Kneading: presses (paws taking turns) and frames each.
KNEAD_PRESSES = (4, 5, 6)
KNEAD_HOLDS = (2, 3)
#: The second half.
ROCK_FRAMES = 2
HIND_A_FRAMES = SHOW_FPS
HIND_IN_FRAMES = 3
HIND_B_FRAMES = 6
UP_BEAT_MIN, UP_BEAT_MAX = 4, 6
TURN_FRAMES = 2
#: Sitting in the middle facing us: how often (rounds in SIT_OF), how long, the blink.
SIT_IN, SIT_OF = 2, 5
SIT_MIN, SIT_MAX = SHOW_FPS, 3 * SHOW_FPS
#: Home: sat, blink, rest (at least 1.5 s after the blink).
SAT_FRAMES = 6
BLINK_FRAMES = 2
REST_END = 12

#: How often he kneads: one round in this many.
KNEAD_ONE_IN = 4


def kneads(ctx: SceneContext) -> bool:
    """Whether this round plays the knead ending."""
    return ctx.pick("ending", KNEAD_ONE_IN) == 0


def sits(ctx: SceneContext) -> bool:
    """Whether he sits in the middle facing us before walking home (if the slot allows)."""
    return ctx.pick("sit", SIT_OF) < SIT_IN


def at(art: tuple[str, ...], x: int = STRETCH_X) -> Sprite:
    """``art`` with its left edge at column ``x``, standing on the floor."""
    return Sprite(art, x, FLOOR - len(art) + 1)


# =========================================================================================
# The quiet part
# =========================================================================================


def _pick_gesture(rng: Random) -> tuple[tuple[str, int], ...]:
    return rng.choices([g for g, _ in GESTURES], [w for _, w in GESTURES])[0]


def quiet_poses(rng: Random, frames: int) -> list[str]:
    """Seated poses for ``frames`` frames: still glancing right, then rests and gestures,
    and the yawn with a short rest after it at the end."""
    end = ["yawn"] * YAWN_FRAMES + ["rest"] * AFTER_YAWN
    if frames <= len(end):  # never on the slots the show uses
        return end[-frames:] if frames > 0 else []
    body = frames - len(end)
    poses = ["rest"] * body
    # The glance leaves at least half a second of rest before the yawn.
    first = max(0, min(body - SHOW_FPS // 2, rng.randint(GLANCE_MIN, GLANCE_MAX)))
    poses[:first] = ["right"] * first
    p = first
    prev = None
    while True:
        p += rng.randint(REST_MIN, REST_MAX)
        g = _pick_gesture(rng)
        if g == prev:
            g = _pick_gesture(rng)
        length = sum(f for _, f in g)
        if p + length > body - SHOW_FPS:  # keep a still second before the yawn
            break
        for pose, f in g:
            poses[p:p + f] = [pose] * f
            p += f
        prev = g
    return poses + end


# =========================================================================================
# The stretch
# =========================================================================================


def hold_pictures(rng: Random, frames: int) -> list[tuple[str, ...]]:
    """The held bow, ``frames`` frames: eyes open, then shut, with a tail flick or two and
    one deeper push; every picture at least :data:`SEG_MIN` frames."""
    templates = [
        ("open", "shut", "tail", "shut", "deep", "shut"),
        ("open", "shut", "deep", "shut", "tail", "shut"),
        ("open", "tail", "shut", "deep", "shut", "tail", "shut"),
        ("open", "shut", "deep", "shut"),
    ]
    fits = [t for t in templates[:3] if len(t) * SEG_MIN <= frames] or [templates[3]]
    names = rng.choice(fits)
    lengths = [rng.randint(OPEN_MIN, OPEN_MAX) if n == "open" else
               DEEP_FRAMES if n == "deep" else SEG_MIN for n in names]
    while sum(lengths) > frames:  # a squeezed hold: the open eyes give way first
        i = max(range(len(lengths)), key=lambda k: lengths[k])
        lengths[i] -= 1
    grow = [i for i, n in enumerate(names) if n in ("shut", "tail")]
    while sum(lengths) < frames:
        i = rng.choice(grow)
        if names[i] == "tail" and lengths[i] >= TAIL_MAX:
            continue
        lengths[i] += 1
    art = {"open": BOW, "shut": BOW_SHUT, "tail": BOW_TAIL, "deep": BOW_DEEP}
    return [art[n] for n, k in zip_strict(names, lengths) for _ in range(k)]


class Plan:
    """What this round does, apart from the quiet part's gestures: decided from the seed,
    then trimmed until the quiet part keeps :data:`QUIET_MIN` frames."""

    def __init__(self, ctx: SceneContext) -> None:
        rng = ctx.rng("plan")
        self.hold = rng.randint(HOLD_MIN, HOLD_MAX)
        self.knead = kneads(ctx)
        self.presses = rng.choice(KNEAD_PRESSES)
        self.press_hold = rng.choice(KNEAD_HOLDS)
        self.up_beat = rng.randint(UP_BEAT_MIN, UP_BEAT_MAX)
        self.sit = rng.randint(SIT_MIN, SIT_MAX) if sits(ctx) else 0
        self.sit_blink_at = rng.randint(SHOW_FPS // 2, max(SHOW_FPS // 2, self.sit - 6))
        self.hold_seed = ctx.seed("hold")
        # In a short slot: no sit in the middle, a shorter hold (down to 2 s), the shortest
        # kneading, each only if the one before was not enough.
        for trim in (self._no_sit, self._short_hold, self._short_knead):
            short = QUIET_MIN - (ctx.frames - len(self.tail_shots()))
            if short <= 0:
                break
            trim(short)

    def _no_sit(self, short: int) -> None:
        self.sit = 0

    def _short_hold(self, short: int) -> None:
        self.hold = max(HOLD_SQUEEZED, self.hold - short)

    def _short_knead(self, short: int) -> None:
        self.presses, self.press_hold = min(KNEAD_PRESSES), min(KNEAD_HOLDS)

    def tail_shots(self) -> list[Shot]:
        """Everything after the quiet part."""
        out: list[Shot] = []

        def add(actor, part: str, n: int = 1) -> None:
            out.extend([Shot(actor, None, part)] * n)

        for s in walk_sprites(SPOT_X, WALK_TO, WALK_FRAMES_PER_PX):
            add(s, "walk")
        add(at(STAND), "stand", STAND_BEAT)
        add(at(DOWN_A), "down", DOWN_FRAMES)
        add(at(DOWN_B), "down", DOWN_FRAMES)
        for art in hold_pictures(Random(self.hold_seed), self.hold):
            add(at(art), "bow")
        if self.knead:
            for i in range(self.presses):
                add(at(KNEAD_A if i % 2 == 0 else KNEAD_B), "knead", self.press_hold)
        add(at(ROCK), "rock", ROCK_FRAMES)
        add(at(HIND_A), "hind", HIND_A_FRAMES)
        add(at(HIND_IN), "hind", HIND_IN_FRAMES)
        add(at(HIND_B), "hind", HIND_B_FRAMES)
        add(at(STAND_FWD), "up", self.up_beat)
        if self.sit:
            first = self.sit_blink_at
            add(Seat("rest", MID_DX), "sit", first)
            add(Seat("blink", MID_DX), "sit", BLINK_FRAMES)
            add(Seat("rest", MID_DX), "sit", max(1, self.sit - first - BLINK_FRAMES))
        add(Sprite(mirror(WALK_STAND), HOME_FROM, WALK_TOP), "home", TURN_FRAMES)
        for s in walk_sprites(HOME_FROM, SPOT_X, WALK_FRAMES_PER_PX):
            add(s, "home")
        out += seated_shots(SAT_FRAMES, "rest", "settle")
        out += seated_shots(BLINK_FRAMES, "blink", "settle")
        out += seated_shots(REST_END, "rest", "settle")
        return out


def shots(ctx: SceneContext) -> list[Shot]:
    """The whole scene, one shot per body frame."""
    tail = Plan(ctx).tail_shots()
    q = ctx.frames - len(tail)
    if q < 0:  # never on the slots the show uses: keep the end, which is seated at rest
        return tail[-ctx.frames:] if ctx.frames > 0 else []
    poses = quiet_poses(ctx.rng("quiet"), q)
    return [Shot(Seat(p), None, "quiet") for p in poses] + tail


@scene("stretch")
def stretch(ctx: SceneContext) -> Sequence[Beat]:
    return shots_to_beats(ctx.cfg, shots(ctx))
