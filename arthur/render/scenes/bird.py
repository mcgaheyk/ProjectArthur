"""The bird (placeholder). Arthur watches something above him, then leaps after it.

A placeholder built only from art that already exists: there is no bird yet. It has the
intended shape, so the real art can drop in:

- quiet: seated, ears and tail twitching, glancing mostly to the left, for whatever the slot
  leaves;
- burst (8 s): he bounds off to the right in a high arc, fast, the stage is empty for 6
  frames, he walks back in from the right;
- settle (6 s): he walks to his spot, sits, blinks.
"""

from __future__ import annotations

from collections.abc import Sequence

from arthur.render.idle_show import Beat, SceneContext, scene
from arthur.render.scenes._common import OFF_RIGHT, SPOT_X, empty, quiet, settle, walk
from arthur.timebase import SHOW_FPS

#: Frames of the burst and of the settling.
BURST = 8 * SHOW_FPS
SETTLE = 6 * SHOW_FPS
#: He bounds off at 3 columns a frame in an arc this many rows high, the stage stays empty,
#: and he walks back at 1 column a frame.
OUT_FRAMES = (OFF_RIGHT - SPOT_X) // 3
HOP_ROWS = 4
GONE_FRAMES = 6
BACK_FRAMES = BURST - OUT_FRAMES - GONE_FRAMES
BACK_TO = OFF_RIGHT - BACK_FRAMES
#: Twitches while he watches: ears and tail, glances mostly to the left.
GESTURES = (("ear", 3), ("left", 3), ("lift", 2), ("squint", 2), ("blink", 2), ("right", 1))


@scene("bird")
def bird(ctx: SceneContext) -> Sequence[Beat]:
    cfg = ctx.cfg
    return [
        *quiet(ctx, ctx.frames - BURST - SETTLE, GESTURES),
        walk(cfg, SPOT_X, OFF_RIGHT, OUT_FRAMES, hop=HOP_ROWS),
        empty(GONE_FRAMES),
        walk(cfg, OFF_RIGHT, BACK_TO, BACK_FRAMES),
        *settle(cfg, BACK_TO, BACK_TO - SPOT_X, SETTLE),
    ]
