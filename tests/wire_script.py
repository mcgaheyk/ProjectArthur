"""Drive the wire client once, the same way under MicroPython and CPython, and print what it
saw as JSON lines. Used by ``tests/test_wire_micropython.py`` against the fake Whisker; never
pointed at the real service.

    micropython -X heapsize=2M tests/wire_script.py URL TOKENS_JSON UPDATES [options]

Options: ``--plain`` (allow http/ws), ``--ca FILE``, ``--server-name NAME``,
``--login USER:PASS`` (no tokens: SRP login), ``--since EPOCH`` (history start),
``--hold SECONDS`` (keep the subscription this long after the updates), ``--measure``
(per-phase allocation with the collector paused, MicroPython), ``--threshold BYTES``
(collect after every BYTES allocated, so sampled peaks are close to real use), ``--monitor``
(instead of the phases: run :class:`arthur.wire.monitor.Monitor` until UPDATES pushes).

Phases: ``token`` (renew, or log in), ``load`` (the robot), ``poll`` (one refresh), ``live``
(subscribe, print ``subscribed``, then each update), ``history``. Lines: ``{"ev": ...}``.
"""

import asyncio
import gc
import json
import sys

sys.path.insert(0, (sys.argv[0].rsplit("/", 1)[0] if "/" in sys.argv[0] else ".") + "/..")

from arthur.wire import errors, util
from arthur.wire.client import WhiskerClient
from arthur.wire.net import Tls

MPY = util.IS_MPY


def out(**kw):
    print(json.dumps(kw))
    try:
        sys.stdout.flush()
    except AttributeError:
        pass


def parse_args(argv):
    opts = {"url": argv[1], "tokens": argv[2], "updates": int(argv[3]), "plain": False,
            "ca": None, "server_name": None, "login": None, "since": None, "hold": 0.0,
            "measure": False, "threshold": None, "monitor": False}
    i = 4
    while i < len(argv):
        a = argv[i]
        if a in ("--plain", "--measure", "--monitor"):
            opts[a[2:]] = True
            i += 1
            continue
        value = argv[i + 1]
        i += 2
        if a == "--ca":
            opts["ca"] = value
        elif a == "--server-name":
            opts["server_name"] = value
        elif a == "--login":
            opts["login"] = value.split(":", 1)
        elif a == "--since":
            opts["since"] = float(value)
        elif a == "--hold":
            opts["hold"] = float(value)
        elif a == "--threshold":
            opts["threshold"] = int(value)
        else:
            raise ValueError("unknown option " + a)
    return opts


class Meter:
    """Per phase on MicroPython: the most heap in use seen (sampled every 2 ms and at each
    update, with the collector running normally), and what the phase allocated in total
    with the collector paused (``--measure``), an upper bound on its peak."""

    def __init__(self, measure):
        self.measure = measure and MPY
        self.peak = 0
        self.task = None

    async def _sample(self):
        while True:
            self.poke()
            await asyncio.sleep(0.002)

    def poke(self):
        if MPY:
            m = gc.mem_alloc()
            self.peak = max(self.peak, m)

    def start(self):
        gc.collect()
        self.base = gc.mem_alloc() if MPY else 0
        self.peak = self.base
        if self.measure:
            gc.disable()
        self.task = asyncio.create_task(self._sample())

    def stop(self, phase, **extra):
        self.task.cancel()
        self.poke()
        allocated = (gc.mem_alloc() - self.base) if self.measure else None
        if self.measure:
            gc.enable()
        gc.collect()
        after = gc.mem_alloc() if MPY else None
        out(ev="mem", phase=phase, peak=self.peak if MPY else None, base=self.base if MPY
            else None, after=after, allocated=allocated, **extra)


def read_tokens(path):
    if path == "-":
        return None
    with open(path) as f:
        return json.load(f)


async def main(o, tokens):
    if o["threshold"] and MPY:
        gc.threshold(o["threshold"])  # collect after this many bytes: peaks become real use
    tls = Tls(ca_file=o["ca"], allow_plain=o["plain"], server_name=o["server_name"])
    client = WhiskerClient(tls, tokens=tokens, credentials=o["login"],
                           lr4_url=o["url"] + "/graphql", cognito_url=o["url"] + "/",
                           ws_backoff=(0.05, 0.2), timeout=60.0)
    meter = Meter(o["measure"])
    try:
        meter.start()
        t0 = util.mono()
        await client.ensure_token(force=True)
        meter.stop("token", seconds=round(util.mono() - t0, 3), login=client.counts["login"],
                   refresh=client.counts["refresh"])

        meter.start()
        record = await client.connect()
        meter.stop("load")
        out(ev="record", src="start", record=record)

        meter.start()
        record = await client.refresh_robot()
        meter.stop("poll")
        out(ev="record", src="poll", record=record)

        got = []
        done = asyncio.Event()

        def on_update():
            meter.poke()
            r = client.record()
            got.append(r)
            out(ev="record", src="ws", record=r)
            if len(got) >= o["updates"]:
                done.set()

        def on_event(kind, text):
            if kind == "ws_start_ack":
                out(ev="subscribed")
            elif kind == "ws_closed":
                out(ev="ws_closed", text=text)

        meter.start()
        t0 = util.mono()
        task = asyncio.create_task(client.live(on_update, on_event))
        await done.wait()
        if o["hold"]:
            await asyncio.sleep(o["hold"])
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        meter.stop("live", updates=len(got), seconds=round(util.mono() - t0, 1))

        meter.start()
        acts = await client.history(start=o["since"], limit=100)
        meter.stop("history", entries=len(acts))
        out(ev="history", acts=[[a.iso, a.action.text, a.action.value] for a in acts])
        out(ev="done", counts=client.counts)
    except errors.WireError as exc:
        out(ev="error", kind=exc.kind, message=str(exc))
        sys.exit(3)


async def monitor_main(o, tokens):
    """The connection policy itself: connect, the start record, the history read at start,
    the pushes, then stop."""
    from arthur.wire.monitor import Monitor

    tls = Tls(ca_file=o["ca"], allow_plain=o["plain"], server_name=o["server_name"])
    client = WhiskerClient(tls, tokens=tokens, credentials=o["login"],
                           lr4_url=o["url"] + "/graphql", cognito_url=o["url"] + "/",
                           ws_backoff=(0.05, 0.2), timeout=60.0)
    stop = asyncio.Event()
    pushed = []

    def on_record(record, source):
        out(ev="record", src=source, record=record)
        if source == "ws":
            pushed.append(1)
            if len(pushed) >= o["updates"]:
                stop.set()

    def on_info(kind, text):
        if kind == "ws_start_ack":
            out(ev="subscribed")

    mon = Monitor(client, on_record,
                  on_error=lambda k, w, m, r: out(ev="monitor_error", kind=k, where=w,
                                                  retry_in=r),
                  on_info=on_info,
                  on_activity=lambda acts: out(ev="history", acts=[
                      [a.iso, a.action.text, a.action.value] for a in acts]))
    await asyncio.wait_for(mon.run(stop), 60)
    out(ev="done", counts=client.counts, error=mon.error)


opts = parse_args(sys.argv)
asyncio.run((monitor_main if opts["monitor"] else main)(opts, read_tokens(opts["tokens"])))
