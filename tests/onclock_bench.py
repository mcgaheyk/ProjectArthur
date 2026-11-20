"""An hour (or any length) of the clock's program on fast-forwarded time, for its timings and
its memory, the same under MicroPython and CPython (tests and measurement only).

    micropython -X heapsize=2M tests/onclock_bench.py ROOT [--minutes 60] [--ca FILE]
    uv run python tests/onclock_bench.py ROOT [--minutes 60]

``ROOT`` holds ``config.json`` (``python -m arthur.tools.export_config ROOT/config.json``).
:class:`arthur.onclock.app.ClockApp` runs its real loop with the time injected: every sleep
returns at once with the clock moved on, so an hour takes as long as its 28,800 beats of work.
The robot is the real robot's three logged cycles (``tests/data/real-cycles.jsonl``) placed at
10, 30 and 50 minutes, idle in between, so the hour has visits, pages and scenes. AWTRIX is
left out: the push builds its request (the encoding is measured) and sends nothing. The
weather is a fixed reading, fetched every 15 minutes. ``--ca`` loads a CA bundle at start as
the clock does (its 216 KB Mozilla store is the biggest single thing in the heap).

Prints one JSON line: the work per beat by what it drew (ms, mean and worst), the beat that
built a scene's plan ahead, the collections (count, mean and worst pause), the heap in use
after the last collection and the most seen after any scheduled one, and the late beats.
"""

import sys

IS_MPY = sys.implementation.name == "micropython"
_here = __file__[: __file__.rfind("/")] if "/" in __file__ else "."
_root = (_here[: _here.rfind("/")] or "/") if "/" in _here else (".." if _here == "." else ".")
if IS_MPY:
    sys.path.insert(0, _root + "/mpy/lib")
sys.path.insert(0, _here)
sys.path.insert(0, _root)

import asyncio
import gc
import json
from datetime import UTC, datetime, timedelta

from onclock_source import EPOCH, ReplaySource, load_lines

from arthur.model import Weather
from arthur.onclock import app as appmod
from arthur.onclock.app import ClockApp
from arthur.onclock.awtrix import ClockTransport

R0 = datetime(2026, 11, 18, 15, 0, 0, tzinfo=UTC)  # 10:00 in Boston: scenes play


class NoAwtrix(ClockTransport):
    """Builds every push's request, sends nothing."""

    async def show(self, frame, ttl_s):
        self.push_request(frame, ttl_s)

    async def check(self):
        pass

    async def close(self):
        pass


class FixedWeather:
    error = None
    fetches = 0

    def __init__(self):
        self.reading = None
        self.slot = None

    def view(self, now):
        slot = ((now - EPOCH) // timedelta(seconds=900))
        if slot != self.slot:  # a fetch every 15 minutes
            self.slot = slot
            self.reading = Weather(temp_f=52.5, code=2, is_day=True, high_f=61.9, low_f=38.9,
                                   fetched_at=EPOCH + timedelta(seconds=900 * slot))
        return self.reading

    async def run(self, stop):
        await stop.wait()

    def update_config(self, cfg):
        pass


def hour_of_lines(path):
    """The three logged cycles, each moved so its detection is at 10, 30 and 50 minutes,
    after one READY line a minute before the start."""
    lines = load_lines(path)
    us = 1_000_000
    r0 = (R0 - EPOCH) // timedelta(microseconds=1)
    out = [(r0 - 60 * us, lines[0][1])]
    for first, last, minute in ((1, 12, 10), (13, 24, 30), (25, 34, 50)):
        shift = r0 + minute * 60 * us - lines[first][0]
        for t, rec in lines[first:last]:
            out.append((t + shift, rec))
    return out


async def bench(root, minutes, ca):
    state = {"t": 0.0}

    def wall():
        return R0 + timedelta(microseconds=round(state["t"] * 1_000_000))

    async def sleep(s):
        state["t"] += s
        if state["t"] >= minutes * 60:
            app.request_stop()
        await asyncio.sleep(0)

    source = ReplaySource(hour_of_lines(_here + "/data/real-cycles.jsonl"), 0)
    app = ClockApp(source, root=root, ca_file=ca, wall_fn=wall, sleep=sleep,
                   time_fn=lambda: state["t"])
    app.echo = False
    app.transport = NoAwtrix(time_fn=app.clock.mono)
    app.pusher.transport = app.transport
    app.weather = FixedWeather()
    highest = [0]
    collect = app._collect

    def watching_collect(force=False):
        before = app.gc_pauses.parts.get("collect", [0])[0]
        collect(force)
        if IS_MPY and app.gc_pauses.parts.get("collect", [0])[0] != before:
            highest[0] = max(highest[0], gc.mem_alloc())

    app._collect = watching_collect
    code = await app.run()
    gc.collect()
    errors = [ln["msg"] for _, ln in app.log.read(0, 10**9)[0] if ln["level"] == "error"]
    print(json.dumps({
        "python": sys.implementation.name, "minutes": minutes, "code": code,
        "beats": app.stats.summary(), "gc": app.gc_pauses.summary(),
        "gc_every_bytes": appmod.GC_EVERY_BYTES, "late": app.late_beats,
        "heap_in_use": gc.mem_alloc() if IS_MPY else None,
        "heap_size": gc.mem_alloc() + gc.mem_free() if IS_MPY else None,
        "heap_highest_after_collect": highest[0] if IS_MPY else None,
        "pushes": app.pusher.pushes, "errors": errors,
    }))
    return code


def _print_slow(ms):
    add = appmod._Stats.add

    def noisy(self, part, us):
        if us > ms * 1000:
            heap = (gc.mem_alloc(), gc.mem_free()) if IS_MPY else None
            print(json.dumps({"slow": part, "ms": us / 1000, "heap": heap}))
        add(self, part, us)

    appmod._Stats.add = noisy


def main(argv):
    root = argv[1]
    minutes = 60.0
    ca = None
    i = 2
    while i < len(argv):
        if argv[i] == "--minutes":
            minutes = float(argv[i + 1])
        elif argv[i] == "--ca":
            ca = argv[i + 1]
        elif argv[i] == "--gc-every":
            appmod.GC_EVERY_BYTES = int(argv[i + 1])
        elif argv[i] == "--slow":  # print every beat slower than this many ms
            _print_slow(float(argv[i + 1]))
        i += 2
    return asyncio.run(bench(root, minutes, ca))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
