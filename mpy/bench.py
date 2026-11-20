"""Time the shared drawing code, the same on CPython and MicroPython (D-0029).

    uv run python -m arthur.tools.export_config /tmp/display.json
    micropython -X heapsize=1M mpy/bench.py /tmp/display.json
    uv run python mpy/bench.py /tmp/display.json

Draws the home page for a minute and each scene for one 36-second slot at 8 frames a second
(the fonts and art already loaded, each scene's plan dropped first, so its first body frame
builds it, as a real play does) and prints, in ms a frame: the mean and the worst over the
sequence, the frame that builds the plan, and the mean and worst of the rest. Under
MicroPython it also prints the heap in use. Nothing is compared: ``tests/test_micropython.py``
checks the pixels.
"""

import gc
import sys

IS_MPY = sys.implementation.name == "micropython"
_here = __file__[: __file__.rfind("/")] if "/" in __file__ else "."
_root = (_here[: _here.rfind("/")] or "/") if "/" in _here else (".." if _here == "." else ".")
if IS_MPY:
    sys.path.insert(0, _here + "/lib")
sys.path.insert(0, _root)

import time

if IS_MPY:
    def _now_us():
        return time.ticks_us()

    def _since_us(t0):
        return time.ticks_diff(time.ticks_us(), t0)
else:
    def _now_us():
        return time.perf_counter_ns() // 1000

    def _since_us(t0):
        return time.perf_counter_ns() // 1000 - t0


def main(cfg_path):
    from datetime import UTC, datetime, timedelta

    from arthur.config import load_config_json
    from arthur.model import Cue, DisplayState
    from arthur.render import idle_show, render

    cfg = load_config_json(cfg_path)
    at = datetime(2026, 11, 18, 15, 0, 0, tzinfo=UTC)
    base = DisplayState("ready", drawer_pct=42.0, litter_pct=78.0, litter_state="OPTIMAL",
                        since=at - timedelta(hours=3), last_visit_at=at - timedelta(hours=2))
    render(base, at, cfg)  # fonts and art loaded
    seqs = [("home", base, 480, None)]
    for name in ("bird", "rat", "stretch"):
        cue = Cue("scene", at, 36.0, 0, name)
        seqs.append((name, DisplayState("ready", drawer_pct=42.0, litter_pct=78.0,
                                        litter_state="OPTIMAL", since=base.since,
                                        last_visit_at=base.last_visit_at, cue=cue), 288,
                     idle_show.GLANCE_FRAMES + idle_show.WIPE_FRAMES))
    print("python:", sys.implementation.name, ".".join(str(x) for x in
                                                       sys.implementation.version[:3]))
    peak = 0
    for name, state, n, plan_k in seqs:
        idle_show.forget_plan()
        gc.collect()
        us = []
        for k in range(n):
            t0 = _now_us()
            render(state, at + timedelta(microseconds=125_000 * k), cfg)
            us.append(_since_us(t0))
            if IS_MPY:
                peak = max(peak, gc.mem_alloc())
        worst = max(range(n), key=lambda i: us[i])
        line = (f"{name}: {n} frames, mean {sum(us) / n / 1000:.3f}, "
                f"worst {us[worst] / 1000:.3f} (frame {worst})")
        if plan_k is not None:
            rest = us[:plan_k] + us[plan_k + 1:]
            line += (f"; plan built on frame {plan_k}: {us[plan_k] / 1000:.3f}; the rest "
                     f"mean {sum(rest) / len(rest) / 1000:.3f}, worst {max(rest) / 1000:.3f}")
        print(line)
    if IS_MPY:
        gc.collect()
        print("heap:", gc.mem_alloc() + gc.mem_free(), "bytes; in use after a collection",
              gc.mem_alloc(), "; highest seen after a frame (garbage included)", peak)


if __name__ == "__main__":
    main(sys.argv[1])
