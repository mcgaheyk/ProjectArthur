"""How long each of the program's long jobs blocks the event loop (tests only; both Pythons).

    micropython onclock_blockbench.py JOB.json

The display is drawn on the same event loop as everything else, so a job that holds the loop
for longer than a beat (125 ms) makes beats late. Each job runs here beside a ticker task that
wakes every 2 ms and records the longest gap it saw; that gap, less the 2 ms, is the longest
stretch the job kept the loop. Jobs: loading the CA bundle, a password login (SRP, sliced), a
renewal, loading the robot, reading 100 history entries, opening the live connection, one
weather fetch, reloading the config, and the door's work for a bundle chunk, a log page and a
request's tag, done in place as the door does them.

Prints one JSON line: ``{job: {"total_ms": ..., "blocked_ms": ...}}``. The clock is
estimated at 25 to 30 times slower than this Mac (the spike's measurement).
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
import time


def ticks_us():
    if IS_MPY:
        return time.ticks_us()
    return time.perf_counter_ns() // 1000


def diff_us(a, b):
    if IS_MPY:
        return time.ticks_diff(a, b)
    return a - b


class Ticker:
    def __init__(self):
        self.worst = 0
        self.running = True

    async def run(self):
        last = ticks_us()
        while self.running:
            await asyncio.sleep(0.002)
            now = ticks_us()
            gap = diff_us(now, last) - 2000
            self.worst = max(self.worst, gap)
            last = now


async def measure(out, ticker, name, coro):
    await asyncio.sleep(0.01)
    ticker.worst = 0
    t0 = ticks_us()
    result = await coro
    total = diff_us(ticks_us(), t0)
    await asyncio.sleep(0.005)
    out[name] = {"total_ms": round(total / 1000, 1), "blocked_ms": round(ticker.worst / 1000, 1)}
    return result


async def measure_sync(out, ticker, name, fn):
    async def run():
        return fn()
    return await measure(out, ticker, name, run())


async def main(job):
    import gc

    from arthur.config import load_config_json
    from arthur.onclock.clock import Clock
    from arthur.onclock.clocklog import ClockLog
    from arthur.onclock.weather import WeatherService
    from arthur.wire import lr4, ws
    from arthur.wire.client import WhiskerClient
    from arthur.wire.net import Tls

    out = {}
    ticker = Ticker()
    tick_task = asyncio.create_task(ticker.run())
    tls = Tls(ca_file=job["ca"], server_name="localhost")
    await measure_sync(out, ticker, "load the CA bundle", tls.context)
    client = WhiskerClient(tls, credentials=(job["user"], job["password"]),
                           lr4_url=job["whisker"] + "/graphql", cognito_url=job["whisker"] + "/")
    await measure(out, ticker, "password login (SRP, sliced)", client.ensure_token())
    await measure(out, ticker, "load the robot", client.load_robot())
    await measure(out, ticker, "renew the token", client.ensure_token(force=True))
    await measure(out, ticker, "read 100 history entries",
                  client.history(start=job["since"], limit=100))
    bearer = await client.bearer()

    async def ws_open():
        sock = await ws.WebSocket.connect(lr4.ws_url(bearer, client.lr4_url), tls,
                                          subprotocol="graphql-ws")
        await sock.close()

    await measure(out, ticker, "open the live connection (TLS + upgrade)", ws_open())
    cfg = load_config_json(job["root"] + "/config.json")
    await measure_sync(out, ticker, "reload the config",
                       lambda: load_config_json(job["root"] + "/config.json"))
    weather = WeatherService(cfg, tls, Clock(), url=job["weather"])
    await measure(out, ticker, "one weather fetch (TLS)", weather.fetch(cfg.weather))

    # The door's own work, as it does it for one authenticated request.
    from arthur.onclock import door as d

    class App:
        def __init__(self):
            self.log = ClockLog()
            for i in range(600):
                self.log.append({"kind": "state", "ts": "2026-11-20T12:00:00.000Z", "t": i,
                                 "record": {"status": "READY", "litter_level": 82.0,
                                            "pet_weight": 5.14, "n": i}})

    app = App()
    door = d.Door(app, job["root"], bytes(range(32)), validate_config=lambda x: None,
                  reserve=0)
    mv = door._mv
    for i in range(d.MAX_HEAD + d.MAX_BODY):
        door._buf[i] = i & 0xFF

    def tag():
        import hashlib

        inner = hashlib.sha256(door._ipad)
        inner.update(mv[:d.MAX_HEAD + d.MAX_BODY])
        outer = hashlib.sha256(door._opad)
        outer.update(inner.digest())
        return outer.digest()

    await measure_sync(out, ticker, "door: the tag of a full request (34 KB)", tag)
    await measure_sync(out, ticker, "door: a log page (24 KB of lines)",
                       lambda: json.dumps(door._logs({"after": 0})))
    chunk = bytes(24576)
    import hashlib

    manifest = json.dumps({"v": 1, "files": {"x.bin": [
        hashlib.sha256(chunk).hexdigest() if not IS_MPY else
        "".join(f"{b:02x}" for b in hashlib.sha256(chunk).digest()), len(chunk)]}})
    door.running = "a"
    begin = door._bundle_begin(manifest.encode())
    await measure_sync(out, ticker, "door: write and hash a 24 KB bundle chunk",
                       lambda: door._bundle_file({"session": begin["session"], "path": "x.bin",
                                                  "offset": 0}, chunk))
    ticker.running = False
    await tick_task
    gc.collect()
    print(json.dumps(out))


if __name__ == "__main__":
    with open(sys.argv[1]) as fh:
        asyncio.run(main(json.load(fh)))
