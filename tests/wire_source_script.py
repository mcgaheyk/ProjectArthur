"""Run ``WireSource`` through one scripted scenario, the same way under MicroPython and
CPython, and print what it did as JSON lines. Used by
``tests/test_wire_source_micropython.py`` against the fake Whisker on 127.0.0.1; never
pointed at the real service.

    micropython -X heapsize=1M tests/wire_source_script.py JOB.json

The job (written by the test) holds the exported config, the scenario, the fake's URL, the
token file and the credentials. When the made-up clock reaches an event the fake must carry
out, the script prints ``{"ev": "need", "i": N}`` and waits until the fake's
``/_test/step`` says it is done (and, for a push, until the record has arrived). At the end
it prints every view sampled, every log line as written (``text``), the console lines, the
notes, and on MicroPython the heap.
"""

import sys

IS_MPY = sys.implementation.name == "micropython"


def _dir(path):
    i = path.rfind("/")
    return path[:i] if i > 0 else "."


HERE = _dir(sys.argv[0])
ROOT = HERE + "/.."
if IS_MPY:
    sys.path.insert(0, ROOT + "/mpy/lib")
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import gc

gc.collect()
HEAP_START = gc.mem_alloc() if IS_MPY else None

import asyncio
import json
import os

from wire_source_driver import Clock, Console, Counter

from arthur.config import config_from_json
from arthur.sources.spikeformat import MemorySink, dumps
from arthur.sources.wire_source import WireSource
from arthur.wire import http
from arthur.wire.net import Tls

gc.collect()
HEAP_IMPORTS = gc.mem_alloc() if IS_MPY else None


def out(**kw):
    print(json.dumps(kw))
    try:
        sys.stdout.flush()
    except AttributeError:
        pass


class PrintSink:
    """Each line printed as it is written (as text, the way a sink on the clock writes it),
    so the run keeps nothing and its heap is the source's own."""

    def state(self, line):
        out(ev="state", text=dumps(line))

    def activity(self, entry):
        out(ev="activity", text=dumps(entry))

    def close(self):
        pass


def token_store(path):
    def load():
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get("access_token") and data.get("id_token"):
            return data
        return None

    def save(tokens):
        with open(path, "w") as f:
            f.write(json.dumps(tokens))

    def delete():
        try:
            os.remove(path)
        except OSError:
            pass

    return load, save, delete


async def main(job):
    cfg = config_from_json(job["config"])
    sc = job["scenario"]
    url = job["url"]
    tls = Tls(allow_plain=True)
    asked = [0]

    async def ask(i, ev):
        out(ev="need", i=i)
        asked[0] += 1
        while True:
            resp = await http.request("GET", url + "/_test/step", tls, timeout=5.0)
            if resp.json()["step"] >= asked[0]:
                return
            await asyncio.sleep(0.005)

    counter = Counter()
    console = Console()
    holder = []
    clock = Clock(sc["start_us"], sc["events"], sc["stop_at"], ask, counter,
                  lambda now: holder[0].view(now), on_probe=lambda p: out(ev="probe", p=p))
    load, save, delete = token_store(job["token"])
    sink = PrintSink() if job.get("ring") is None else MemorySink(limit=job["ring"])
    src = WireSource(
        cfg, credentials=tuple(job["credentials"]), tls=tls, sink=sink, load_token=load,
        save_token=save, delete_token=delete, serial=job.get("serial"), wall_fn=clock.wall,
        mono_fn=clock.mono, sleep=clock.sleep, say=console, local=cfg.local,
        client_kw={"lr4_url": url + "/graphql", "cognito_url": url + "/", "stale_s": 0.5,
                   "ws_backoff": (0.05, 0.2), "timeout": 30.0})
    holder.append(src)
    src.monitor.on_record = counter.wrap(src.monitor.on_record)
    gc.collect()
    heap_ready = gc.mem_alloc() if IS_MPY else None
    await src.run(asyncio.Event())
    clock._sample()
    if isinstance(sink, MemorySink):
        for text in sink.states:
            out(ev="state", text=text)
        for text in sink.activities:
            out(ev="activity", text=text)
    for line in console.lines:
        out(ev="console", line=line)
    gc.collect()
    out(ev="done", notes=clock.notes, counts=src.client.counts,
        heap={"start": HEAP_START, "imports": HEAP_IMPORTS, "ready": heap_ready,
              "peak": clock.peak, "end": gc.mem_alloc() if IS_MPY else None,
              "free": gc.mem_free() if IS_MPY else None})


with open(sys.argv[1]) as f:
    JOB = json.load(f)
asyncio.run(main(JOB))
