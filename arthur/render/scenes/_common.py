"""Helpers the scenes share: seated Arthur's quiet part, walking, sitting back down.

Every scene has three parts (D-0019): a quiet part that stretches to fill whatever the slot
leaves (Arthur seated, glancing about on a seeded, irregular rhythm with still stretches, at
most about one change a second), a burst of about 8 seconds, and about 6 seconds of settling
that ends with him seated at rest at his spot.

Two ways to build a scene. The placeholders join beats directly (:func:`quiet`, :func:`walk`,
:func:`settle`). A scene with two animals on the stage is easier as one :class:`Shot` per
frame (Arthur and the other animal, each a :class:`Seat` or a :class:`Sprite`), joined with
:func:`shots_to_beats`; shots also let tests measure distances and speeds (:func:`cells`,
:func:`gap`). Arthur's running art (:data:`RUN_A`, :data:`RUN_B`) is here for every scene.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from arthur.config import Config
from arthur.render.frame import Frame
from arthur.render.icons import draw_art, draw_icon
from arthur.render.idle_show import (
    REST_POSE,
    STAGE_H,
    STAGE_W,
    STAGE_X,
    STAGE_Y,
    Beat,
    SceneContext,
    draw_seated,
    mirror,
    new_stage,
)
from arthur.render.screens import PIC, TAIL_HALF, TAIL_LIFT, TAIL_X, TAIL_Y, WALK_A, WALK_B
from arthur.timebase import SHOW_FPS

#: The stage row his feet stand on: frame row 14, as when he sits.
FLOOR = STAGE_H - 1
#: Top row of the walking cat on the stage.
WALK_TOP = FLOOR - len(WALK_A) + 1
#: Width of the walking cat.
WALK_W = len(WALK_A[0])
#: Where the walking cat stands at his spot (the column of its left edge on the stage).
SPOT_X = 2
#: Just off the right edge of the stage.
OFF_RIGHT = STAGE_W
#: Walking strides, facing right and facing left.
STRIDES_RIGHT: tuple[tuple[str, ...], ...] = (WALK_A, WALK_B)
STRIDES_LEFT: tuple[tuple[str, ...], ...] = (mirror(WALK_A), mirror(WALK_B))
#: Frames each stride is held.
STRIDE_FRAMES = 2
#: The quiet part's rhythm, in frames: still stretches and gestures, each at least a second.
REST_MIN, REST_MAX = 2 * SHOW_FPS, 6 * SHOW_FPS
GESTURE_MIN, GESTURE_MAX = SHOW_FPS, 2 * SHOW_FPS
#: Shortest beat of the quiet part: at most about one change a second.
QUIET_MIN = SHOW_FPS
#: The settling blink, frames.
BLINK_FRAMES = 6


def seated(cfg: Config, frames: int, pose: str = REST_POSE) -> Beat:
    """``frames`` of Arthur seated at his spot in ``pose``."""
    return Beat(frames, lambda stage, i: draw_seated(stage, cfg, pose))


def empty(frames: int) -> Beat:
    """``frames`` of an empty stage."""
    return Beat(frames, lambda stage, i: None)


def walk(cfg: Config, x_from: int, x_to: int, frames: int, *, hop: int = 0) -> Beat:
    """Arthur walking from column ``x_from`` towards ``x_to`` over ``frames`` frames (the
    last frame is one step short of ``x_to``, so a following beat can start there), facing
    the way he goes. ``hop`` lifts him up to that many rows in an arc over the walk."""
    strides = STRIDES_RIGHT if x_to >= x_from else STRIDES_LEFT

    def draw(stage, i: int) -> None:
        x = x_from + round((x_to - x_from) * i / frames)
        lift = round(hop * math.sin(math.pi * i / frames)) if hop else 0
        art = strides[(i // STRIDE_FRAMES) % len(strides)]
        draw_art(stage, art, x, WALK_TOP - lift, cfg)

    return Beat(frames, draw)


def quiet(
    ctx: SceneContext, frames: int, gestures: Sequence[tuple[str, int]], key: str = "quiet"
) -> list[Beat]:
    """Seated Arthur for ``frames`` frames: still stretches of 2 to 6 s between gestures of 1
    to 2 s (poses from ``gestures``, ``(pose, weight)``), seeded by ``ctx`` and ``key``. Every
    beat is at least a second; the beats add up to exactly ``frames``."""
    if frames <= 0:
        return []
    rng = ctx.rng(key)
    poses = [p for p, w in gestures for _ in range(w)]
    lengths: list[tuple[str, int]] = []
    left = frames
    resting = True
    prev = None
    while left > 0:
        if resting:
            n, pose = rng.randint(REST_MIN, REST_MAX), REST_POSE
        else:
            pose = rng.choice([p for p in poses if p != prev] or poses)
            n = rng.randint(GESTURE_MIN, GESTURE_MAX)
            prev = pose
        n = min(n, left)
        if left - n < QUIET_MIN:  # too little left for a beat of its own: this one takes it
            n = left
        lengths.append((pose, n))
        left -= n
        resting = not resting
    if len(lengths) > 1 and lengths[-1][1] < QUIET_MIN:
        pose, n = lengths.pop()
        lengths[-1] = (lengths[-1][0], lengths[-1][1] + n)
    return [seated(ctx.cfg, n, pose) for pose, n in lengths]


# =========================================================================================
# Shots: a scene as one picture per frame, two actors at most
# =========================================================================================

#: Arthur running, side view, facing right: 14 wide, 7 tall, feet on the bottom row. RUN_A
#: is stretched out (front and back legs reaching), RUN_B gathered (legs crossed under him,
#: tail up). Same head, back and stripes as ``WALK_A``.
RUN_A = (
    "..........O.O.",
    "..........OOO.",
    "S....OSOSOOEO.",
    ".OOOOOOOOOOON.",
    "...OOOOOOOO...",
    "..OO......OO..",
    ".O..........O.",
)
RUN_B = (
    "..........O.O.",
    "S.........OOO.",
    "O...OSOSO.OEO.",
    ".OOOOOOOOOOON.",
    "...OOOOOOO....",
    "....OO.OO.....",
    ".....O.O......",
)
#: Running strides, facing right and facing left.
RUNS_RIGHT: tuple[tuple[str, ...], ...] = (RUN_A, RUN_B)
RUNS_LEFT: tuple[tuple[str, ...], ...] = (mirror(RUN_A), mirror(RUN_B))
#: Width and top row of the running cat on the stage.
RUN_W = len(RUN_A[0])
RUN_TOP = FLOOR - len(RUN_A) + 1
#: Frames each running stride is held.
RUN_STRIDE_FRAMES = 2

#: Seated poses beyond the home page's: watching to the right with the tail raised (the
#: hunter's twitch), and halfway there. Same icon and tails as ``screens.IDLE_POSES``.
EXTRA_SEATED: dict[str, tuple[str, tuple[str, ...]]] = {
    "right_lift": ("arthur_sit_right", TAIL_LIFT),
    "right_half": ("arthur_sit_right", TAIL_HALF),
}


class Seat(NamedTuple):
    """Arthur seated in ``pose`` (a key of ``screens.IDLE_POSES`` or :data:`EXTRA_SEATED`),
    shifted ``dx`` columns from his spot."""

    pose: str
    dx: int = 0

    def draw(self, stage: Frame, cfg: Config) -> None:
        if self.pose in EXTRA_SEATED:
            icon, tail = EXTRA_SEATED[self.pose]
            x, y = self.dx - STAGE_X, -STAGE_Y
            draw_icon(stage, icon, x, y, cfg, size=PIC)
            draw_art(stage, tail, x + TAIL_X, y + TAIL_Y, cfg)
        else:
            draw_seated(stage, cfg, self.pose, self.dx)


class Sprite(NamedTuple):
    """Text art with its top-left at stage column ``x``, row ``y`` (clipped at the edges)."""

    rows: tuple[str, ...]
    x: int
    y: int

    def draw(self, stage: Frame, cfg: Config) -> None:
        draw_art(stage, self.rows, self.x, self.y, cfg)


Actor = Seat | Sprite


@dataclass(frozen=True)
class Shot:
    """One frame of a scene: Arthur (or nobody), the other animal (or nobody), and which part
    of the scene it belongs to (for tests and review; it does not change the picture)."""

    arthur: Actor | None
    prey: Actor | None
    part: str

    def draw(self, stage: Frame, cfg: Config) -> None:
        if self.prey is not None:
            self.prey.draw(stage, cfg)
        if self.arthur is not None:
            self.arthur.draw(stage, cfg)


def shots_to_beats(cfg: Config, shots: Sequence[Shot]) -> list[Beat]:
    """One beat per run of identical pictures (``part`` is ignored)."""
    beats: list[Beat] = []
    i = 0
    while i < len(shots):
        j = i + 1
        while j < len(shots) and (shots[j].arthur, shots[j].prey) == (
                shots[i].arthur, shots[i].prey):
            j += 1
        shot = shots[i]
        beats.append(Beat(j - i, lambda stage, _k, s=shot: s.draw(stage, cfg)))
        i = j
    return beats


def cells(actor: Actor | None, cfg: Config) -> frozenset[tuple[int, int]]:
    """The stage pixels ``actor`` lights."""
    if actor is None:
        return frozenset()
    s = new_stage()
    actor.draw(s, cfg)
    off = cfg.rgb("off")
    return frozenset((i % s.width, i // s.width) for i, c in enumerate(s.pixels) if c != off)


def gap(a: frozenset[tuple[int, int]], b: frozenset[tuple[int, int]]) -> int:
    """Unlit pixels between two pixel sets, the nearest way (0 when they touch, also
    diagonally); a large number when either is empty."""
    if not a or not b:
        return 1_000
    return min(max(abs(x1 - x2), abs(y1 - y2)) for x1, y1 in a for x2, y2 in b) - 1


def seated_shots(frames: int, pose: str, part: str, prey: Actor | None = None) -> list[Shot]:
    """``frames`` shots of Arthur seated at his spot."""
    return [Shot(Seat(pose), prey, part)] * max(0, frames)


def run_shots(
    x_from: int, speed: int, *, right: bool, part: str, until_off: bool = True,
    frames: int | None = None,
) -> list[Sprite]:
    """Arthur running from column ``x_from`` at ``speed`` columns a frame, strides held
    :data:`RUN_STRIDE_FRAMES` frames, for ``frames`` frames or until he is off the stage."""
    strides = RUNS_RIGHT if right else RUNS_LEFT
    out: list[Sprite] = []
    x = x_from
    step = speed if right else -speed
    while True:
        if frames is not None and len(out) >= frames:
            break
        if frames is None and until_off and (x >= STAGE_W or x + RUN_W <= 0):
            break
        out.append(Sprite(strides[(len(out) // RUN_STRIDE_FRAMES) % 2], x, RUN_TOP))
        x += step
    return out


def walk_sprites(x_from: int, x_to: int, frames_per_px: int = 1) -> list[Sprite]:
    """Arthur walking from column ``x_from`` to ``x_to`` (both included), one column every
    ``frames_per_px`` frames, a stride held :data:`STRIDE_FRAMES` frames (or one step when
    tiptoeing), facing the way he goes."""
    strides = STRIDES_RIGHT if x_to >= x_from else STRIDES_LEFT
    step = 1 if x_to >= x_from else -1
    hold = max(STRIDE_FRAMES, frames_per_px)
    out: list[Sprite] = []
    for x in range(x_from, x_to + step, step):
        for _ in range(frames_per_px):
            out.append(Sprite(strides[(len(out) // hold) % 2], x, WALK_TOP))
    return out


def settle(cfg: Config, from_x: int, walk_frames: int, total: int) -> list[Beat]:
    """Walk from ``from_x`` back to his spot, sit, blink once, sit: ``total`` frames, ending
    seated at rest."""
    sit = total - walk_frames - BLINK_FRAMES
    if sit < 2:
        raise ValueError("settle: not enough frames to walk, sit and blink")
    first = sit // 2
    return [
        walk(cfg, from_x, SPOT_X, walk_frames),
        seated(cfg, first),
        seated(cfg, BLINK_FRAMES, "blink"),
        seated(cfg, sit - first),
    ]
