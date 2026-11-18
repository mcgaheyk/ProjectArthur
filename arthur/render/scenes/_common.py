"""Helpers the scenes share: seated Arthur's quiet part, walking, sitting back down.

Every scene has three parts (D-0019): a quiet part that stretches to fill whatever the slot
leaves (Arthur seated, glancing about on a seeded, irregular rhythm with still stretches, at
most about one change a second), a burst of about 8 seconds, and about 6 seconds of settling
that ends with him seated at rest at his spot.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from arthur.config import Config
from arthur.render.icons import draw_art
from arthur.render.idle_show import (
    REST_POSE,
    STAGE_H,
    STAGE_W,
    Beat,
    SceneContext,
    draw_seated,
    mirror,
)
from arthur.render.screens import WALK_A, WALK_B
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
