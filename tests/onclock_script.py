"""Run the clock's program (``arthur.onclock.app.ClockApp``) on sped-up time, the same way
under MicroPython and CPython, and print what it did as JSON lines. Used by
``tests/test_onclock_mpy.py`` against the fake clock and a fake Open-Meteo on 127.0.0.1;
never pointed at a real clock or service.

    micropython -X heapsize=2M tests/onclock_script.py JOB.json

The job (JSON): ``root`` (holds config.json), ``clock`` (AWTRIX's URL), ``weather`` (the
fake Open-Meteo's https URL), ``ca`` (the test CA), ``fake_t0_us`` and ``real_t0_ns`` (the
fake wall time at a real ``time.time_ns()``), ``scale`` (fake seconds per real second),
``lines`` (the robot's log), ``shift_us`` (added to its times), and ``events``: ``[[fake
seconds after fake_t0, "pause" | "resume" | "restart" | "stop"], ...]``. ``trace`` true prints
every push.

Lines: ``{"ev": "push", "us", "sha", "kind", "reason", "wx"}`` for each push (``wx`` is the
weather reading's fetch time in µs, or null), ``{"ev": "console", ...}`` for each console line,
``{"ev": "source started" | "source stopped" | "pause" | ...,"us"}``, then ``{"ev": "end",
"code", "status", "stats", "gc", "late", "heap"}``.
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
import hashlib
import json
import time
from datetime import timedelta

from onclock_source import EPOCH, ReplaySource, load_lines

from arthur.onclock.app import ClockApp
from arthur.render import Frame  # noqa: F401  (imported first: the biggest modules)

ONE_US = timedelta(microseconds=1)


def out(**kw):
    print(json.dumps(kw))


def hexdigest(data):
    return "".join("%02x" % b for b in hashlib.sha256(data).digest())  # noqa: UP031


async def main(job):
    scale = float(job["scale"])
    fake_t0 = int(job["fake_t0_us"])
    real_t0 = int(job["real_t0_ns"])

    def fake_us():
        return fake_t0 + int((time.time_ns() - real_t0) // 1000 * scale)

    def wall():
        return EPOCH + timedelta(microseconds=fake_us())

    def mono():
        return fake_us() / 1_000_000

    async def sleep(s):
        await asyncio.sleep(s / scale)

    def us_of(dt):
        return (dt - EPOCH) // ONE_US

    source = ReplaySource(load_lines(job["lines"]), int(job["shift_us"]),
                          on_event=lambda name: out(ev=name, us=fake_us()))
    app = ClockApp(source, root=job["root"], base_url=job["clock"], ca_file=job["ca"],
                   wall_fn=wall, sleep=sleep, time_fn=mono)
    app.echo = False
    app.tls.server_name = "localhost"  # the test certificate's name
    app.weather.url = job["weather"]
    console = app.console

    def console_and_print(level, msg, exc=None):
        console(level, msg, exc)
        out(ev="console", us=fake_us(), level=level, msg=msg)

    app.console = console_and_print
    app.weather._log = console_and_print

    if job.get("trace", True):
        def trace(now, frame, state, reason):
            w = state.weather
            out(ev="push", us=us_of(now), sha=hexdigest(frame.to_rgb888()), kind=state.kind,
                reason=reason, wx=None if w is None else us_of(w.fetched_at))

        app.trace = trace

    async def control():
        for when, what in sorted(job.get("events", [])):
            while fake_us() < fake_t0 + int(when * 1_000_000):
                await asyncio.sleep(0.005)
            out(ev=what, us=fake_us(), pushes=app.pusher.pushes, running=source.running)
            if what == "pause":
                app.pause()
            elif what == "resume":
                app.resume()
            elif what == "restart":
                app.request_restart()
            elif what == "stop":
                app.request_stop()

    ctl = asyncio.create_task(control())
    code = await app.run()
    ctl.cancel()
    gc.collect()
    heap = {"alloc": gc.mem_alloc(), "free": gc.mem_free()} if IS_MPY else None
    out(ev="end", code=code, status=app.status(), stats=app.stats.summary(),
        gc=app.gc_pauses.summary(), late=app.late_beats, heap=heap,
        runs=source.runs, stale_retries=app.transport.stale_retries,
        reconnect_checks=app.transport.reconnect_checks,
        connections=app.transport.conn.opened)
    return code


if __name__ == "__main__":
    with open(sys.argv[1]) as fh:
        job = json.load(fh)
    rc = asyncio.run(main(job))
    sys.exit(rc)
