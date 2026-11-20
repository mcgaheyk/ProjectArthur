"""The clock's robot source (``arthur/onclock/source.py``) under MicroPython, against the fake
Whisker on 127.0.0.1 (tests only; run by ``tests/test_onclock_source_mpy.py``).

    micropython onclock_source_script.py PORT ROOT

Runs the source until it is connected, stops it (as a pause does), waits for the step the
harness sets at ``/_test/step``, runs it again and stops it again. Prints one JSON line per
event.
"""

import sys

IS_MPY = sys.implementation.name == "micropython"
_here = __file__[: __file__.rfind("/")] if "/" in __file__ else "."
_root = (_here[: _here.rfind("/")] or "/") if "/" in _here else (".." if _here == "." else ".")
if IS_MPY:
    sys.path.insert(0, _root + "/mpy/lib")
sys.path.insert(0, _root)

import asyncio
import json


def out(**kw):
    print(json.dumps(kw))


async def step(port):
    from arthur.wire import http
    from arthur.wire.net import Tls

    resp = await http.request("GET", "http://127.0.0.1:" + port + "/_test/step",
                              Tls(allow_plain=True), timeout=5)
    return json.loads(resp.body)["step"]


async def main(port, root):
    from arthur.config import load_config_json
    from arthur.onclock.clocklog import ClockLog
    from arthur.onclock.source import make_source
    from arthur.wire.net import Tls

    cfg = load_config_json(root + "/config.json")
    log = ClockLog()
    base = "http://127.0.0.1:" + port
    src = make_source(cfg=cfg, log=log, tls=Tls(allow_plain=True), root=root, echo=False,
                      wire_kw={"client_kw": {"lr4_url": base + "/graphql",
                                             "cognito_url": base + "/",
                                             "ws_backoff": (0.05, 0.2)}})
    for run in (1, 2):
        stop = asyncio.Event()
        task = asyncio.create_task(src.run(stop))
        if run == 2:
            await asyncio.sleep(0)
            # Nothing old is shown while the second run connects.
            out(ev="second run begins", snapshot=src.view(src.inner._wall())[0] is not None)
        while src.inner is None or src.inner.last_contact_at is None:
            await asyncio.sleep(0.05)
        while not src.inner.monitor.connected:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5 if run == 1 else 2.0)
        out(ev="connected", run=run)
        while await step(port) < run:
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 10)
        _snap, _health = src.view(src.inner._wall())
        out(ev="stopped", run=run, connected=src.inner.monitor.connected)
        while await step(port) < run + 1:
            await asyncio.sleep(0.05)
    msgs = [ln["msg"] for _, ln in log.read(0, 10**7)[0] if ln.get("kind") == "console"]
    out(ev="end", console=msgs, tokens_writes=src.tokens.writes, runs=src.runs)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
