"""Measure how fast and how steadily frames can be pushed to an AWTRIX NG clock.

    uv run python scripts/push_bench.py --clock http://HOST MODE [options]

Every push goes through :class:`AwtrixNgTransport` with the feeder's app name, payload and
lifetime (``ttl_s`` from config/display.toml), so what is timed is what the feeder will do.
**Latency** is the awaited duration of one ``transport.show()`` call, in milliseconds.

Modes
  rates      for each of --rates (default 4,8,12 frames a second), push changing frames for
             --seconds (default 10) on a fixed-rate grid; a push that overruns ticks skips
             them (counted as dropped) instead of queueing
  flatout    push back to back for --seconds (default 10); report the rate achieved
  idle-gap   after silences of --gaps seconds (default 5,20,60), time the next single push
  sustained  --fps (default 8) for --seconds (default 600), one row per minute
  walk       the walking cat crossing the screen, --px pixels a frame (1 to 4), at --fps
  dissolve   two real screens (--from and --to fixtures), switching over --frames steps
  wipe       the same, a vertical edge moving across
  slide      the same, the first screen pushed out upward as the second comes in from below
  counter    a frame number and a lit column that move one step per tick, for a slow-motion
             phone video: a skipped number is a dropped tick, a number shown twice is a
             frame the clock drew late

The visual modes run on the same fixed-rate grid at --fps for --seconds (default 60) and report
the same figures. Every pattern is indexed by grid tick, so a dropped tick shows as a jump.

Before the timed part, every mode makes one ``first push``. It runs the transport's settings
and rotation checks (GET /api/v1/settings, GET /api/v1/apps and maybe a PATCH or a PUT), which
the transport repeats every 300 s and after any failure. A push that ran those checks is left
out of the latency columns and counted under ``checks`` with its own time instead.

TCP connections opened are counted with an aiohttp TraceConfig on a session handed to the
transport, the same default session settings the feeder gets. aiohttp itself closes a
connection that has been idle for 15 s (``--keepalive`` changes that), so in ``idle-gap`` a new
connection after a longer silence is the client's doing, not the clock's.

The feeder pushes to the same app, so the two fight over the screen. The script refuses to run
while a process matching ``arthur.feeder`` is running, unless --force is given. Stop the feeder
first (``pkill -f arthur.feeder``) and start it again afterwards; the last bench frame stays up
and is marked stale once its lifetime runs out.

At the end (or on Ctrl-C, with what was measured so far) it prints a summary table and appends
one JSON line to ``logs/pushbench-YYYY-MM-DD.jsonl``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO

import aiohttp

from arthur.config import Config, load_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import Frame, render
from arthur.render.font import large_font
from arthur.render.icons import draw_art
from arthur.render.screens import WALK_A, WALK_B, WALK_Y
from arthur.transports import AwtrixNgTransport, TransportError

W, H = 52, 16
MODES = ("rates", "flatout", "idle-gap", "sustained",
         "walk", "dissolve", "wipe", "slide", "counter")
PATTERNS = ("walk", "dissolve", "wipe", "slide", "counter")
DEFAULT_SECONDS = {"rates": 10.0, "flatout": 10.0, "sustained": 600.0}
PATTERN_SECONDS = 60.0
DEFAULT_RATES = (4.0, 8.0, 12.0)
DEFAULT_GAPS = (5.0, 20.0, 60.0)
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
PUSH_METHOD = "PUT"
#: Fixed seed for the dissolve order, so every run switches pixels in the same order.
DISSOLVE_SEED = 52_16

EXIT_OK, EXIT_CLOCK, EXIT_USAGE, EXIT_FEEDER, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


# =========================================================================================
# Measurements
# =========================================================================================


@dataclass
class Sample:
    """One ``transport.show()`` call."""

    start: float  # time_fn() when the call started
    ms: float  # awaited duration
    requests: list[str]  # "METHOD /path" of every HTTP request it made
    new_connections: int  # TCP connections opened during the call
    conn_close: int  # responses that carried "Connection: close"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def with_checks(self) -> bool:
        """True when the call did more than the one PUT of the frame."""
        return len(self.requests) != 1 or not self.requests[0].startswith(PUSH_METHOD + " ")


def latency_stats(ms: list[float]) -> dict:
    """``{n, min, p50, p95, max}`` of ``ms``; percentiles are nearest-rank. Empty gives n=0
    and None for the rest."""
    if not ms:
        return {"n": 0, "min": None, "p50": None, "p95": None, "max": None}
    s = sorted(ms)

    def rank(p: float) -> float:
        return s[max(0, math.ceil(p / 100 * len(s)) - 1)]

    return {"n": len(s), "min": s[0], "p50": rank(50), "p95": rank(95), "max": s[-1]}


@dataclass
class Row:
    """One line of the summary: a rate, a minute, a gap or the first push."""

    label: str
    origin: float = 0.0  # seconds from the start of its run where this row begins
    samples: list[Sample] = field(default_factory=list)
    dropped: int = 0
    seconds: float = 0.0  # time covered, for the rate column; 0 = no rate shown

    @property
    def sent(self) -> int:
        return sum(s.ok for s in self.samples)

    @property
    def failures(self) -> int:
        return sum(not s.ok for s in self.samples)

    @property
    def connections(self) -> int:
        return sum(s.new_connections for s in self.samples)

    @property
    def conn_close(self) -> int:
        return sum(s.conn_close for s in self.samples)

    @property
    def frame_ms(self) -> list[float]:
        """Latency of good pushes that were one PUT only."""
        return [s.ms for s in self.samples if s.ok and not s.with_checks]

    @property
    def check_ms(self) -> list[float]:
        """Latency of good pushes that also ran the transport's checks."""
        return [s.ms for s in self.samples if s.ok and s.with_checks]

    @property
    def rate(self) -> float | None:
        return self.sent / self.seconds if self.seconds > 0 else None

    def to_json(self) -> dict:
        errors = [s.error for s in self.samples if s.error]
        checks = self.check_ms
        return {
            "label": self.label,
            "sent": self.sent,
            "dropped": self.dropped,
            "failures": self.failures,
            "seconds": round(self.seconds, 3),
            "rate_per_s": None if self.rate is None else round(self.rate, 2),
            "connections": self.connections,
            "conn_close": self.conn_close,
            "latency_ms": {k: (v if k == "n" or v is None else round(v, 2))
                           for k, v in latency_stats(self.frame_ms).items()},
            "checks": {"n": len(checks),
                       "max_ms": round(max(checks), 2) if checks else None,
                       "requests": sorted({r for s in self.samples if s.ok and s.with_checks
                                           for r in s.requests})},
            "first_error": errors[0] if errors else None,
            "latencies_ms": [round(m, 2) for m in self.frame_ms],
        }


class Tracer:
    """Counts what the transport's session does, through an aiohttp TraceConfig."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.connections = 0
        self.conn_close = 0
        self.config = aiohttp.TraceConfig()
        self.config.on_request_start.append(self._on_request_start)
        self.config.on_request_end.append(self._on_request_end)
        self.config.on_connection_create_end.append(self._on_connection)

    async def _on_request_start(self, session, ctx, params) -> None:
        self.requests.append(f"{params.method} {params.url.path}")

    async def _on_request_end(self, session, ctx, params) -> None:
        if params.response.headers.get("Connection", "").lower() == "close":
            self.conn_close += 1

    async def _on_connection(self, session, ctx, params) -> None:
        self.connections += 1


class Bench:
    """The transport, its traced session and the clock the pushes are timed with."""

    def __init__(
        self,
        base_url: str,
        *,
        app_name: str = "litter",
        ttl_s: float = 90,
        keepalive_s: float | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tracer = Tracer()
        connector = (aiohttp.TCPConnector(keepalive_timeout=keepalive_s)
                     if keepalive_s is not None else None)
        self.session = aiohttp.ClientSession(connector=connector,
                                             trace_configs=[self.tracer.config])
        self.transport = AwtrixNgTransport(base_url, app_name=app_name, session=self.session,
                                           time_fn=time_fn)
        self.ttl_s = ttl_s
        self.time_fn = time_fn

    async def push(self, frame: Frame) -> Sample:
        tr = self.tracer
        n_req, n_conn, n_close = len(tr.requests), tr.connections, tr.conn_close
        start = self.time_fn()
        error = None
        try:
            await self.transport.show(frame, self.ttl_s)
        except TransportError as exc:
            error = str(exc)
        ms = (self.time_fn() - start) * 1000
        return Sample(start, ms, tr.requests[n_req:], tr.connections - n_conn,
                      tr.conn_close - n_close, error)

    async def close(self) -> None:
        await self.transport.close()
        await self.session.close()


PushFn = Callable[[Frame], Awaitable[Sample]]


async def run_grid(
    push: PushFn,
    frame_at: Callable[[int], Frame],
    fps: float,
    seconds: float,
    row_for: Callable[[float], Row],
    *,
    time_fn: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Push ``frame_at(k)`` for tick k at ``t0 + k / fps``, for ``seconds``.

    A push that ends after the next tick's due time skips every tick already past (added to
    ``dropped`` of the row the push belongs to); nothing is queued. ``row_for(t)`` gives the
    row for a push that starts ``t`` seconds after ``t0``.
    """
    n_ticks = max(1, math.ceil(seconds * fps - 1e-9))
    t0 = time_fn()
    k = 0
    prev: Row | None = None
    while k < n_ticks:
        wait = t0 + k / fps - time_fn()
        if wait > 0:
            await sleep(wait)
        sample = await push(frame_at(k))
        row = row_for(sample.start - t0)
        if prev is not None and prev is not row:
            prev.seconds = row.origin - prev.origin  # a finished row covers its whole span
        prev = row
        row.samples.append(sample)
        now = time_fn() - t0
        nxt = max(k + 1, math.ceil(now * fps - 1e-9))
        row.dropped += min(nxt, n_ticks) - (k + 1)
        # Time covered so far (kept current so an interrupted run still has a rate).
        row.seconds = max(row.seconds, min(now, seconds) - row.origin)
        k = nxt
    if prev is not None:
        prev.seconds = seconds - prev.origin


# =========================================================================================
# Patterns: Frame for tick k
# =========================================================================================


def fixture_frame(name: str, cfg: Config) -> Frame:
    """Fixture ``name`` mapped and rendered at its own time, as the preview does."""
    fx = FIXTURES[name]
    return render(to_display_state(fx.snapshot, fx.health, fx.now, cfg), fx.now, cfg)


def counter_frame(k: int, cfg: Config) -> Frame:
    """Tick number (mod 10000) in the large font, and a lit column at ``k % 52``."""
    f = Frame(W, H)
    f.vline(k % W, 0, H, cfg.rgb("soap"))
    f.text(1, 4, str(k % 10000), cfg.rgb("text"), large_font())
    return f


def walk_frame(k: int, cfg: Config, px: int) -> Frame:
    """The walking cat entering on the left and leaving on the right, ``px`` columns a tick."""
    art_w = len(WALK_A[0])
    f = Frame(W, H)
    x = -art_w + (k * px) % (W + art_w)
    draw_art(f, WALK_A if k % 2 == 0 else WALK_B, x, WALK_Y, cfg)
    return f


# The three transitions below are stand-ins for the bench only; the real transition code comes
# in a later step and replaces them. Each gives ``a`` at i = 0 and ``b`` at i = n.


def _dissolve(a: Frame, b: Frame, i: int, n: int) -> Frame:
    order = list(range(W * H))
    random.Random(DISSOLVE_SEED).shuffle(order)
    out = a.copy()
    for p in order[: round(len(order) * i / n)]:
        out.pixels[p] = b.pixels[p]
    return out


def _wipe(a: Frame, b: Frame, i: int, n: int) -> Frame:
    edge = round(W * i / n)
    out = a.copy()
    for y in range(H):
        out.pixels[y * W: y * W + edge] = b.pixels[y * W: y * W + edge]
    return out


def _slide(a: Frame, b: Frame, i: int, n: int) -> Frame:
    off = round(H * i / n)
    out = Frame(W, H)
    for y in range(H):
        src, sy = (a, y + off) if y + off < H else (b, y + off - H)
        out.pixels[y * W: (y + 1) * W] = src.pixels[sy * W: (sy + 1) * W]
    return out


TRANSITIONS = {"dissolve": _dissolve, "wipe": _wipe, "slide": _slide}


def transition_steps(n: int, hold: int) -> list[int]:
    """Step index per tick for one back-and-forth cycle: ``hold`` ticks on a (0), steps 1..n-1,
    ``hold`` ticks on b (n), steps n-1..1."""
    return [0] * hold + list(range(1, n)) + [n] * hold + list(range(n - 1, 0, -1))


def pattern(name: str, cfg: Config, *, fps: float = 8, px: int = 1, frames: int = 6,
            a: str = "ready", b: str = "cleaning") -> Callable[[int], Frame]:
    """``frame_at(k)`` for a visual mode (or ``counter`` for the timing modes)."""
    if name == "counter":
        return lambda k: counter_frame(k, cfg)
    if name == "walk":
        return lambda k: walk_frame(k, cfg, px)
    fn = TRANSITIONS[name]
    fa, fb = fixture_frame(a, cfg), fixture_frame(b, cfg)
    cache = {i: fn(fa, fb, i, frames) for i in range(frames + 1)}
    steps = transition_steps(frames, max(1, round(fps)))
    return lambda k: cache[steps[k % len(steps)]]


# =========================================================================================
# Running a mode
# =========================================================================================


@dataclass
class Result:
    mode: str
    clock: str
    app: str
    params: dict
    rows: list[Row] = field(default_factory=list)
    interrupted: bool = False
    aborted: str | None = None
    seconds: float = 0.0
    connections: int = 0
    keepalive_s: float | None = None

    def row(self, label: str, origin: float = 0.0) -> Row:
        r = Row(label, origin)
        self.rows.append(r)
        return r


async def run_mode(
    opts: argparse.Namespace,
    cfg: Config,
    bench: Bench,
    result: Result,
    *,
    time_fn: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Fill ``result`` with rows as they are measured (so an interrupt keeps them)."""
    mode = opts.mode
    seconds = result.params.get("seconds", 0.0)
    fps = result.params.get("fps", 8.0)

    if mode in PATTERNS:
        frame_at = pattern(mode, cfg, fps=fps, px=opts.px, frames=opts.frames,
                           a=opts.from_fixture, b=opts.to_fixture)
    else:
        frame_at = pattern("counter", cfg)

    first = result.row("first push")
    sample = await bench.push(frame_at(0))
    first.samples.append(sample)
    if not sample.ok:
        result.aborted = sample.error
        return

    def grid(fn, rate, secs, row_for):
        return run_grid(bench.push, fn, rate, secs, row_for, time_fn=time_fn, sleep=sleep)

    if mode == "rates":
        for rate in result.params["rates"]:
            row = result.row(f"{rate:g}/s")
            await grid(frame_at, rate, seconds, lambda t, row=row: row)
    elif mode == "flatout":
        row = result.row("flat out")
        t0 = time_fn()
        k = 0
        while time_fn() - t0 < seconds:
            row.samples.append(await bench.push(frame_at(k)))
            row.seconds = time_fn() - t0
            k += 1
    elif mode == "idle-gap":
        for k, gap in enumerate(result.params["gaps"], start=1):
            row = result.row(f"after {gap:g}s idle")
            await sleep(gap)
            row.samples.append(await bench.push(frame_at(k)))
    elif mode == "sustained":
        minutes: dict[int, Row] = {}

        def per_minute(t: float) -> Row:
            i = int(t // 60)
            if i not in minutes:
                minutes[i] = result.row(f"min {i + 1}", origin=60.0 * i)
            return minutes[i]

        await grid(frame_at, fps, seconds, per_minute)
    else:
        label = f"{mode} {fps:g}/s" + (f" {opts.px}px" if mode == "walk" else "")
        row = result.row(label)
        await grid(frame_at, fps, seconds, lambda t: row)


def _ms(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def summary_text(result: Result) -> str:
    """The table printed at the end of a run."""
    head = (f"push_bench {result.mode} -> {result.clock} app={result.app}: "
            f"{result.seconds:.1f} s, {result.connections} TCP connection(s) opened")
    if result.interrupted:
        head += " (interrupted)"
    cols = f"{'row':<16}{'sent':>6}{'drop':>6}{'fail':>6}{'rate/s':>8}{'conn':>6}" \
           f"{'min':>8}{'p50':>8}{'p95':>8}{'max':>8}  checks"
    lines = [head, cols]
    for r in result.rows:
        st = latency_stats(r.frame_ms)
        checks = r.check_ms
        chk = f"{len(checks)} (max {max(checks):.1f} ms)" if checks else "-"
        rate = "-" if r.rate is None else f"{r.rate:.2f}"
        lines.append(f"{r.label:<16}{r.sent:>6}{r.dropped:>6}{r.failures:>6}{rate:>8}"
                     f"{r.connections:>6}{_ms(st['min']):>8}{_ms(st['p50']):>8}"
                     f"{_ms(st['p95']):>8}{_ms(st['max']):>8}  {chk}")
    lines += [
        "latency: ms per awaited transport.show(), over good pushes that were one PUT only.",
        ("checks: pushes that also ran the transport's settings and rotation checks (count and "
         "slowest); they are not in the latency columns."),
        ("conn: new TCP connections; 0 after the first push means the connection was kept "
         "alive. The client drops idle connections after "
         f"{15 if result.keepalive_s is None else result.keepalive_s:g} s."),
    ]
    close = sum(r.conn_close for r in result.rows)
    if close:
        lines.append(f"{close} response(s) said 'Connection: close'.")
    for r in result.rows:
        errs = [s.error for s in r.samples if s.error]
        if errs:
            lines.append(f"{r.label}: {len(errs)} failure(s), first: {errs[0]}")
    if result.aborted:
        lines.append(f"stopped: the first push failed: {result.aborted}")
    return "\n".join(lines)


def result_json(result: Result, now: datetime) -> dict:
    return {
        "ts": now.isoformat(timespec="seconds"),
        "mode": result.mode,
        "clock": result.clock,
        "app": result.app,
        "params": result.params,
        "interrupted": result.interrupted,
        "aborted": result.aborted,
        "seconds": round(result.seconds, 3),
        "connections_opened": result.connections,
        "keepalive_s": result.keepalive_s,
        "rows": [r.to_json() for r in result.rows],
    }


def append_log(result: Result, log_dir: Path, now: datetime) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"pushbench-{now:%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result_json(result, now), separators=(",", ":")) + "\n")
    return path


def mode_params(opts: argparse.Namespace) -> dict:
    seconds = opts.seconds
    if seconds is None:
        seconds = DEFAULT_SECONDS.get(opts.mode, PATTERN_SECONDS)
    p: dict = {"seconds": seconds}
    if opts.mode == "rates":
        p["rates"] = list(opts.rates)
    elif opts.mode == "idle-gap":
        p = {"gaps": list(opts.gaps)}
    elif opts.mode != "flatout":
        p["fps"] = opts.fps
    if opts.mode == "walk":
        p["px"] = opts.px
    if opts.mode in TRANSITIONS:
        p.update(frames=opts.frames, a=opts.from_fixture, b=opts.to_fixture)
    p["ttl_s"] = opts.ttl
    return p


async def run(
    opts: argparse.Namespace,
    cfg: Config,
    *,
    out: TextIO | None = None,
    time_fn: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    log_dir: Path | None = LOG_DIR,
    wall_fn: Callable[[], datetime] = datetime.now,
) -> int:
    """Run ``opts.mode``, print the summary and append the JSON line (``log_dir`` None: no
    log). Returns the exit status."""
    def say(msg: str) -> None:
        print(msg, file=out or sys.stdout, flush=True)

    if opts.ttl is None:
        opts.ttl = cfg.timings.ttl_s
    base_url = clock_url(opts.clock)
    result = Result(opts.mode, base_url, opts.app, mode_params(opts), keepalive_s=opts.keepalive)
    say(f"push_bench: {opts.mode} -> {base_url} app={opts.app} {json.dumps(result.params)}")
    bench = Bench(base_url, app_name=opts.app, ttl_s=opts.ttl, keepalive_s=opts.keepalive,
                  time_fn=time_fn)
    t0 = time_fn()
    try:
        await run_mode(opts, cfg, bench, result, time_fn=time_fn, sleep=sleep)
    except asyncio.CancelledError:
        result.interrupted = True
    finally:
        result.seconds = time_fn() - t0
        result.connections = bench.tracer.connections
        await bench.close()
    say(summary_text(result))
    if log_dir is not None:
        say(f"logged to {append_log(result, log_dir, wall_fn())}")
    if not result.aborted:
        say(f"the last frame stays up and is marked stale {opts.ttl:g} s after its push")
    if result.interrupted:
        return EXIT_INTERRUPTED
    return EXIT_CLOCK if result.aborted else EXIT_OK


# =========================================================================================
# Command line
# =========================================================================================


def clock_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    return raw if "://" in raw else f"http://{raw}"


def feeder_running() -> bool:
    """Whether a process matching ``arthur.feeder`` is running (``pgrep -f``), other than this
    one. False if pgrep is missing."""
    try:
        res = subprocess.run(["pgrep", "-f", "arthur.feeder"], capture_output=True, text=True,
                             check=False)
    except FileNotFoundError:
        return False
    pids = {int(p) for p in res.stdout.split() if p.isdigit()}
    return bool(pids - {os.getpid()})


def _floats(raw: str) -> list[float]:
    try:
        vals = [float(v) for v in raw.split(",") if v.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a list of numbers: {raw!r}") from exc
    if not vals or any(v <= 0 for v in vals):
        raise argparse.ArgumentTypeError(f"need numbers above 0: {raw!r}")
    return vals


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/push_bench.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("mode", choices=MODES, help="what to measure or show (see above)")
    p.add_argument("--clock", metavar="URL", required=True,
                   help="the clock's address, e.g. http://192.168.1.50")
    p.add_argument("--seconds", type=float, help="how long to run (default: rates and flatout "
                   "10, sustained 600, visual modes 60; per rate in rates)")
    p.add_argument("--fps", type=float, default=8.0,
                   help="frames a second for sustained and the visual modes (default 8)")
    p.add_argument("--rates", type=_floats, default=list(DEFAULT_RATES), metavar="FPS,...",
                   help="frame rates for rates (default 4,8,12)")
    p.add_argument("--gaps", type=_floats, default=list(DEFAULT_GAPS), metavar="S,...",
                   help="silences before each timed push for idle-gap (default 5,20,60)")
    p.add_argument("--px", type=int, default=1, choices=(1, 2, 3, 4),
                   help="walk: columns the cat moves a frame (default 1)")
    p.add_argument("--frames", type=int, default=6,
                   help="dissolve, wipe, slide: steps from one screen to the other (default 6)")
    p.add_argument("--from", dest="from_fixture", default="ready", metavar="FIXTURE",
                   help="dissolve, wipe, slide: first screen (default ready)")
    p.add_argument("--to", dest="to_fixture", default="cleaning", metavar="FIXTURE",
                   help="dissolve, wipe, slide: second screen (default cleaning)")
    p.add_argument("--keepalive", type=float, metavar="S",
                   help="close idle client connections after S seconds (default: aiohttp's "
                   "15 s, as the feeder has)")
    p.add_argument("--ttl", type=float, help="lifetime of each push in seconds (default ttl_s "
                   "from the config)")
    p.add_argument("--app", default="litter", help="pushed app name (default litter, the "
                   "feeder's)")
    p.add_argument("--config", help="path to display.toml")
    p.add_argument("--no-log", action="store_true", help="do not append to logs/pushbench-*")
    p.add_argument("--force", action="store_true",
                   help="run even though the feeder is running (the two fight over the screen)")
    return p


def check_feeder(opts: argparse.Namespace, is_running: Callable[[], bool],
                 err: TextIO | None = None) -> int | None:
    """EXIT_FEEDER with a message when the feeder runs and --force is not given; else None."""
    if opts.force or not is_running():
        return None
    print("push_bench: the feeder (arthur.feeder) is running and pushes to the same clock; the "
          "two would fight over the screen and the numbers would be wrong.\n"
          "Stop it first (pkill -f arthur.feeder) and start it again afterwards, or pass "
          "--force.", file=err or sys.stderr)
    return EXIT_FEEDER


def main(argv: list[str] | None = None, *,
         is_feeder_running: Callable[[], bool] = feeder_running, **run_kw) -> int:
    opts = build_parser().parse_args(argv)
    if opts.seconds is not None and opts.seconds <= 0:
        print("push_bench: --seconds must be more than 0", file=sys.stderr)
        return EXIT_USAGE
    if opts.frames < 1 or opts.fps <= 0 or (opts.ttl is not None and opts.ttl <= 0):
        print("push_bench: --frames must be 1 or more, --fps and --ttl more than 0",
              file=sys.stderr)
        return EXIT_USAGE
    for name in (opts.from_fixture, opts.to_fixture):
        if name not in FIXTURES:
            print(f"push_bench: unknown fixture {name!r}", file=sys.stderr)
            return EXIT_USAGE
    refused = check_feeder(opts, is_feeder_running)
    if refused is not None:
        return refused
    cfg = load_config(opts.config)
    if opts.no_log:
        run_kw.setdefault("log_dir", None)
    try:
        return asyncio.run(run(opts, cfg, **run_kw))
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
