"""Push rendered screens to a real AWTRIX NG clock: a first hardware test, not the feeder.

    uv run python scripts/push_test.py --clock http://HOST [NAME] [--seconds N] [--readback] [--clear]
    uv run python scripts/push_test.py --list

NAME is a display kind or fixture name, as for ``python -m arthur.render.preview NAME``
(default ``ready``). The fixture is mapped once, at its own time, and rendered at 52x16 as
time moves on from there, the way the studio plays a fixture. A frame is pushed when it
changes, and at least every ``heartbeat_s`` of config/display.toml, for ``--seconds``
(default 60; 0 pushes one frame and exits).

Every push carries ``lifetimeMs`` = ``ttl_s`` (default from config, 90 s) with the stale mark,
so if this script dies the clock marks the frame stale instead of showing it as current. On a
normal exit (or Ctrl-C) the last frame stays up and is marked stale once its lifetime runs
out; ``--clear`` removes the pushed app instead.

``--readback`` reads ``GET /api/v1/display/screen`` after every push and compares it with the
frame sent. The order and colour handling of that read-back are unconfirmed guesses
(docs/reference/awtrix-ng.md), so differences are reported, not judged.

The transport turns the clock's ``enlargeApps`` off before the first push (a TC002 has it on).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TextIO

from arthur.config import Config, load_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.pusher import Pusher
from arthur.render import Frame, list_variants, render
from arthur.render.preview import names_text
from arthur.transports import AwtrixNgTransport, TransportError

DEFAULT_NAME = "ready"
#: How often the screen is re-rendered. Screens change on whole seconds.
TICK_S = 0.25
#: Wait between a push and its read-back, so the clock has drawn at least one frame.
READBACK_SETTLE_S = 0.2
#: How many differing pixels the read-back lists.
SHOW_DIFFS = 8


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/push_test.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("name", nargs="?", default=DEFAULT_NAME,
                   help=f"display kind or fixture name (default {DEFAULT_NAME}); see --list")
    p.add_argument("--clock", metavar="URL", help="the clock's address, e.g. http://192.168.1.50")
    p.add_argument("--seconds", type=float, default=60,
                   help="how long to keep animating (default 60; 0 = push one frame and exit)")
    p.add_argument("--variant", help="variant of the screen (default: its first)")
    p.add_argument("--ttl", type=float, help="lifetime of each push in seconds (default ttl_s "
                   "from the config)")
    p.add_argument("--readback", action="store_true",
                   help="after each push, read the clock's screen back and compare")
    p.add_argument("--clear", action="store_true", help="remove the pushed app on exit")
    p.add_argument("--app", default="litter", help="pushed app name (default litter)")
    p.add_argument("--config", help="path to display.toml")
    p.add_argument("--list", action="store_true", help="list kinds, variants and fixtures")
    return p


def clock_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    return raw if "://" in raw else f"http://{raw}"


def _hex(n: int) -> str:
    return f"#{n & 0xFFFFFF:06X}"


def compare(frame: Frame, screen: dict) -> list[str]:
    """Lines describing how the read-back ``screen`` differs from ``frame``.

    Pixels are taken as row-major from the top left, which is a guess for a real clock.
    """
    w, h, got = screen["width"], screen["height"], screen["pixels"]
    sent_size = f"{frame.width}x{frame.height} ({frame.width * frame.height} px)"
    lines = [f"  readback: sent {sent_size}, got {w}x{h} ({len(got)} px)"]
    if (w, h) != frame.size or len(got) != w * h:
        lines.append("  readback: sizes differ; pixels not compared")
        return lines
    sent = frame.to_ints()
    diffs = [(i % w, i // w, s, g) for i, (s, g) in enumerate(zip(sent, got, strict=True))
             if s != g]
    lines.append(f"  readback: {len(diffs)} of {len(sent)} pixels differ")
    if diffs:
        shown = ", ".join(f"({x}, {y}, {_hex(s)}, {_hex(g)})" for x, y, s, g in diffs[:SHOW_DIFFS])
        more = f" and {len(diffs) - SHOW_DIFFS} more" if len(diffs) > SHOW_DIFFS else ""
        lines.append(f"  readback: first (x, y, sent, got): {shown}{more}")
    return lines


async def run(
    opts: argparse.Namespace,
    cfg: Config,
    *,
    out: TextIO | None = None,
    time_fn: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Push ``opts.name`` to ``opts.clock``. Raises TransportError on any clock failure."""
    fx = FIXTURES[opts.name]
    state = to_display_state(fx.snapshot, fx.health, fx.now, cfg)
    variants = list_variants(state.kind, cfg.size)
    variant = opts.variant if opts.variant in variants else variants[0]
    ttl_s = opts.ttl if opts.ttl is not None else cfg.timings.ttl_s
    heartbeat_s = min(cfg.timings.heartbeat_s, ttl_s / 3)
    base_url = clock_url(opts.clock)

    def say(msg: str) -> None:
        print(msg, file=out or sys.stdout, flush=True)

    say(f"push_test: {opts.name} [{state.kind}] variant={variant} -> {base_url} "
        f"app={opts.app} ttl={ttl_s:g}s for {opts.seconds:g}s")
    transport = AwtrixNgTransport(base_url, app_name=opts.app)
    pusher = Pusher(transport, time_fn=time_fn)
    t0 = time_fn()
    try:
        while True:
            elapsed = time_fn() - t0
            frame = render(state, fx.now + timedelta(seconds=elapsed), cfg, variant=variant)
            result = await pusher.offer(frame, ttl_s, heartbeat_s)
            if result is not None:
                if result.error is not None:
                    raise result.error
                say(f"push {pusher.pushes} at +{elapsed:.1f}s ({result.reason})")
                if opts.readback:
                    await sleep(READBACK_SETTLE_S)
                    for line in compare(frame, await transport.read_screen()):
                        say(line)
            if time_fn() - t0 >= opts.seconds:
                break
            await sleep(TICK_S)
        if opts.clear:
            await transport.clear()
            say(f"cleared app {opts.app}")
        else:
            say(f"done after {pusher.pushes} pushes; the last frame stays up and is marked stale "
                f"{ttl_s:g}s after its push")
    except asyncio.CancelledError:
        # Ctrl-C: leave the last frame (it goes stale on its own), or clear if asked.
        if opts.clear:
            await transport.clear()
            say(f"interrupted; cleared app {opts.app}")
        else:
            say(f"interrupted after {pusher.pushes} pushes; the last frame stays up and goes stale")
    finally:
        await transport.close()
    return 0


async def guarded(opts: argparse.Namespace, cfg: Config, *, out: TextIO | None = None,
                  err: TextIO | None = None, **kw) -> int:
    """:func:`run`, with any clock failure printed as one line on ``err`` (exit status 1)."""
    try:
        return await run(opts, cfg, out=out, **kw)
    except TransportError as exc:
        print(f"push_test: error: {exc}", file=err or sys.stderr, flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    opts = build_parser().parse_args(argv)
    cfg = load_config(opts.config)
    if opts.list:
        print(names_text(cfg))
        return 0
    if opts.name not in FIXTURES:
        print(f"push_test: unknown kind or fixture {opts.name!r}; try --list", file=sys.stderr)
        return 2
    if not opts.clock:
        print("push_test: --clock URL is required (e.g. --clock http://192.168.1.50)",
              file=sys.stderr)
        return 2
    if opts.seconds < 0 or (opts.ttl is not None and opts.ttl <= 0):
        print("push_test: --seconds must be 0 or more and --ttl more than 0", file=sys.stderr)
        return 2

    try:
        return asyncio.run(guarded(opts, cfg))
    except KeyboardInterrupt:
        return 130
