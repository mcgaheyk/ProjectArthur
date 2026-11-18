"""The rat (placeholder). Arthur watches something off to the right, then gives chase.

A placeholder built only from art that already exists: there is no rat yet. It has the
intended shape, so the real art can drop in:

- quiet: seated, glancing mostly to the right, for whatever the slot leaves;
- burst (8 s): he trots off to the right, the stage is empty for 6 frames, he walks back in
  from the right, slower;
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
#: He leaves at 2 columns a frame, the stage stays empty this long, and he comes back at 1.
OUT_FRAMES = (OFF_RIGHT - SPOT_X) // 2
GONE_FRAMES = 6
BACK_FRAMES = BURST - OUT_FRAMES - GONE_FRAMES
BACK_TO = OFF_RIGHT - BACK_FRAMES
#: Glances while he watches: mostly to the right, where it is.
GESTURES = (("right", 4), ("left", 1), ("blink", 2), ("ear", 2), ("squint", 1))


@scene("rat")
def rat(ctx: SceneContext) -> Sequence[Beat]:
    cfg = ctx.cfg
    return [
        *quiet(ctx, ctx.frames - BURST - SETTLE, GESTURES),
        walk(cfg, SPOT_X, OFF_RIGHT, OUT_FRAMES),
        empty(GONE_FRAMES),
        walk(cfg, OFF_RIGHT, BACK_TO, BACK_FRAMES),
        *settle(cfg, BACK_TO, BACK_TO - SPOT_X, SETTLE),
    ]
