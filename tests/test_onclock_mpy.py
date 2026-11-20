"""The clock's program end to end under the ``micropython`` binary (1.29, as on the clock).

``tests/onclock_script.py`` runs :class:`arthur.onclock.app.ClockApp` under MicroPython, on
time sped up :data:`SCALE` times, against the repository's fake clock (``arthur.sim``, which
rejects unknown keys with 422 and starts with ``enlargeApps`` on, like a new TC002) and a fake
Open-Meteo over verified TLS, both on 127.0.0.1 in this process. The robot is a scripted
source replaying a real visit from ``tests/data/real-cycles.jsonl``. One run covers:

- AWTRIX's API down at start (the program waits, then carries on);
- an idle round with the scene's plan built ahead, the scene, and the weather page;
- the fake clock restarted mid-run (our app, the setting and the rotation come back);
- AWTRIX answering 500 for a while (logged once, recovered, checked again);
- the whole real visit: in use, the countdown, cleaning, just cleaned;
- pause and resume (no pushes, the source stopped and started again);
- a restart requested, giving exit code 42.

Every frame the program pushed is drawn again here by CPython's pipeline for the same state
and moment and must match; every frame the fake clock received must be one of them, with the
lifetime from ``ttl_s`` and ``lifetimeExpiry: "mark"``.

Skips, saying the on-clock test did not run, when ``micropython`` is not installed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
import ssl
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import web
from onclock_source import ReplaySource, at, load_lines, ts_us

from arthur.config import config_from_json
from arthur.mapping import to_display_state
from arthur.model import FeederHealth
from arthur.render import render
from arthur.sim.server import CLOCKS, create_app
from arthur.sources.openmeteo import parse_open_meteo
from arthur.tools.export_config import export

pytestmark = pytest.mark.usefixtures("loopback_only")

MPY = shutil.which("micropython")
needs_mpy = pytest.mark.skipif(
    MPY is None, reason="micropython is not on PATH: the on-clock program's end-to-end test "
    "did NOT run, so nothing checks that it works under MicroPython (brew install "
    "micropython)")

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "onclock_script.py"
LINES = HERE / "data" / "real-cycles.jsonl"
SAMPLE = json.loads((HERE / "data" / "open-meteo-forecast.json").read_text())
#: Fake seconds per real second.
SCALE = 20.0
#: A round boundary in the daytime in Boston: the idle show plays scenes.
R0 = datetime(2026, 11, 18, 15, 0, 0, tzinfo=UTC)
#: The program starts 100 s into the round; offsets below are from there.
START = R0 + timedelta(seconds=100)
VISIT_AT = 190.0  # the real visit's detection (R0 + 290)
RESTART_CLOCK_AT = 95.0  # after the scene (R0 + 144 to 180)
ERRORS = (100.0, 112.0)  # AWTRIX answers 500
PAUSE, RESUME = 400.0, 420.0  # during the visit's countdown
RESTART_AT = 905.0  # after the visit (back to ready at about R0 + 987)
HEARTBEAT_S, TTL_S = 1.5, 4.5


def _us(dt: datetime) -> int:
    return (dt - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Fakes:
    """The fake clock (restartable on one port) and the fake Open-Meteo, on fake time."""

    def __init__(self, real_t0_ns: int, tls_files) -> None:
        self.real_t0 = real_t0_ns
        self.port = free_port()
        self.received: list[tuple[float, str, int, str]] = []  # (fake s, sha, lifetime, exp)
        self.requests: list[tuple[float, str]] = []
        self.failing = False
        self.clocks: list = []
        self.runner = None
        self.weather_runner = None
        self.tls_files = tls_files
        self.weather_hits = 0

    def fake_s(self) -> float:
        """Fake seconds since START."""
        return (time.time_ns() - self.real_t0) / 1e9 * SCALE

    def mono(self) -> float:
        return _us(START) / 1e6 + self.fake_s()

    @web.middleware
    async def middleware(self, request, handler):
        self.requests.append((self.fake_s(), f"{request.method} {request.path}"))
        if self.failing:
            return web.json_response({"error": {"code": "internal", "message": "boom"}},
                                     status=500)
        resp = await handler(request)
        if request.method == "PUT" and request.path.startswith("/api/v1/apps/pushed/") \
                and resp.status == 200:
            body = json.loads(await request.read())
            rgb = base64.b64decode(body["draw"][0][5])
            self.received.append((self.fake_s(), hashlib.sha256(rgb).hexdigest(),
                                  body["lifetimeMs"], body["lifetimeExpiry"]))
        return resp

    async def start_clock(self) -> None:
        app = create_app(start_tasks=False, time_fn=self.mono)
        app.middlewares.append(self.middleware)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.clocks.append(app[CLOCKS]["52x16"])

    async def stop_clock(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    async def start_weather(self) -> str:
        async def forecast(request):
            self.weather_hits += 1
            return web.json_response(SAMPLE)

        app = web.Application()
        app.router.add_get("/v1/forecast", forecast)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.tls_files["cert"], self.tls_files["key"])
        self.weather_runner = web.AppRunner(app)
        await self.weather_runner.setup()
        port = free_port()
        await web.TCPSite(self.weather_runner, "127.0.0.1", port, ssl_context=ctx).start()
        return f"https://127.0.0.1:{port}/v1/forecast"

    async def close(self) -> None:
        await self.stop_clock()
        if self.weather_runner is not None:
            await self.weather_runner.cleanup()


def write_job(tmp_path: Path, fakes: Fakes, weather_url: str, tls_files, *,
              end_at: float = RESTART_AT, events=None) -> tuple[Path, dict]:
    data = export()
    data["timings"] = dict(data["timings"], heartbeat_s=HEARTBEAT_S, ttl_s=TTL_S)
    (tmp_path / "config.json").write_text(json.dumps(data))
    lines = load_lines(str(LINES))
    shift = _us(START + timedelta(seconds=VISIT_AT)) - lines[1][0]
    job = {
        "root": str(tmp_path), "clock": f"http://127.0.0.1:{fakes.port}",
        "weather": weather_url, "ca": str(tls_files["ca"]),
        "fake_t0_us": _us(START), "real_t0_ns": fakes.real_t0, "scale": SCALE,
        "lines": str(LINES), "shift_us": shift,
        "events": events if events is not None else [
            [PAUSE, "pause"], [RESUME, "resume"], [end_at, "restart"]],
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job))
    return path, data


async def drive(argv: list[str], fakes: Fakes, *, timeout: float) -> tuple[list, int, str]:
    """Run the program; bring the clock up once it says it is waiting, restart it and make it
    fail at the planned moments. Returns (lines, exit code, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(HERE.parent)})
    lines: list[dict] = []

    async def director():
        while not any(x.get("ev") == "console" and "waiting for AWTRIX" in x.get("msg", "")
                      for x in lines):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        await fakes.start_clock()
        while fakes.fake_s() < RESTART_CLOCK_AT:
            await asyncio.sleep(0.01)
        await fakes.stop_clock()  # AWTRIX restarts: our app and connection are gone
        await fakes.start_clock()
        while fakes.fake_s() < ERRORS[0]:
            await asyncio.sleep(0.01)
        fakes.failing = True
        while fakes.fake_s() < ERRORS[1]:
            await asyncio.sleep(0.01)
        fakes.failing = False

    task = asyncio.create_task(director())
    end = time.monotonic() + timeout
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), max(0.1, end - time.monotonic()))
            if not raw:
                break
            try:
                lines.append(json.loads(raw))
            except ValueError:  # a traceback: MicroPython prints them on stdout
                lines.append({"ev": "text", "text": raw.decode(errors="replace")})
        await proc.wait()
    finally:
        task.cancel()
        if proc.returncode is None:
            proc.kill()
    err = (await proc.stderr.read()).decode()
    return lines, proc.returncode, err


def recompute(pushes: list[dict], cfg) -> list[str]:
    """CPython's pipeline for the same moments: ``[problem, ...]``."""
    source = ReplaySource(load_lines(str(LINES)),
                          _us(START + timedelta(seconds=VISIT_AT)) - load_lines(str(LINES))[1][0])
    problems = []
    for p in sorted(pushes, key=lambda p: p["us"]):
        now = at(p["us"])
        snap, health = source.view(now)
        weather = parse_open_meteo(SAMPLE, at(p["wx"])) if p["wx"] is not None else None
        state = to_display_state(snap, health, now, cfg, weather=weather)
        sha = hashlib.sha256(render(state, now, cfg).to_rgb888()).hexdigest()
        if sha != p["sha"] or state.kind != p["kind"]:
            problems.append((p["us"], p["kind"], state.kind))
    return problems


@pytest.fixture
async def e2e(tmp_path, tls_files):
    """One whole run under MicroPython (the planned events), shared by the checks below."""
    fakes = Fakes(time.time_ns() + int(0.3e9), tls_files)
    weather_url = await fakes.start_weather()
    job, data = write_job(tmp_path, fakes, weather_url, tls_files)
    try:
        lines, code, err = await drive([MPY, "-X", "heapsize=2M", str(SCRIPT), str(job)],
                                       fakes, timeout=(RESTART_AT + 60) / SCALE + 60)
    finally:
        await fakes.close()
    return {"lines": lines, "code": code, "err": err, "fakes": fakes,
            "cfg": config_from_json(data)}


def _console(lines) -> list[str]:
    return [x["msg"] for x in lines if x.get("ev") == "console"]


@needs_mpy
async def test_the_program_end_to_end_under_micropython(e2e):
    lines, code, fakes, cfg = e2e["lines"], e2e["code"], e2e["fakes"], e2e["cfg"]
    texts = [x["text"] for x in lines if x.get("ev") == "text"]
    end = next((x for x in lines if x.get("ev") == "end"), None)
    assert end is not None and not texts, (texts[-20:], e2e["err"][-3000:])
    print("end:", json.dumps({k: end[k] for k in ("stats", "gc", "late", "heap", "runs",
                                                  "stale_retries", "reconnect_checks",
                                                  "connections")}))
    console = _console(lines)

    # A restart requested: exit code 42, and the source stopped with the program.
    assert code == 42 and end["code"] == 42

    # The API down at start: waited, without exiting, then carried on.
    assert console.count(next(m for m in console if m.startswith("waiting for AWTRIX"))) == 1
    assert any(m.startswith("AWTRIX answered after") for m in console)
    assert "first frame pushed to AWTRIX" in console

    # Every frame pushed is the frame CPython draws for the same state and moment.
    pushes = [x for x in lines if x.get("ev") == "push"]
    assert len(pushes) > 1000
    assert recompute(pushes, cfg) == []
    kinds = {p["kind"] for p in pushes}
    assert {"ready", "in_use", "waiting", "cleaning", "just_cleaned"} <= kinds, kinds

    # The fake clock received those frames, each with the lifetime and the stale mark.
    sent = {p["sha"] for p in pushes}
    got = fakes.received
    assert got and {sha for _, sha, _, _ in got} <= sent
    assert len(got) - len(pushes) in (0, 1, 2)  # a push whose answer was lost in a restart
    assert {(lt, exp) for _, _, lt, exp in got} == {(round(TTL_S * 1000), "mark")}

    # The heartbeat: a still frame is pushed again every heartbeat_s.
    by_time = sorted(pushes, key=lambda p: p["us"])
    beats = [(a, b) for a, b in zip(by_time, by_time[1:], strict=False)
             if b["reason"] == "heartbeat"]
    assert len(beats) >= 5
    for a, b in beats:
        assert a["sha"] == b["sha"]
        # Times are each beat's start; the pusher counts from the end of the last push, so
        # a beat's work (sped up 20 times here) can put the stamps a little under.
        assert HEARTBEAT_S - 0.25 <= (b["us"] - a["us"]) / 1e6 <= HEARTBEAT_S + 1

    # The idle show: the weather page and a scene were played, the scene's plan built ahead.
    assert end["stats"].get("plan built ahead", {}).get("beats", 0) >= 1
    assert any(p.startswith("scene ") and p != "scene wipe" for p in end["stats"])
    assert "page" in end["stats"]
    assert any(p["wx"] is not None for p in pushes)  # the weather reached the page

    # enlargeApps off and ours the only app, on the first clock and on the restarted one.
    assert len(fakes.clocks) == 2
    for c in fakes.clocks:
        assert not c.enlarge_apps and c.in_rotation() == ["litter"] and "litter" in c.apps

    # AWTRIX failing: logged once per outage, recovered, and both checks ran again after.
    lost = [m for m in console if m.startswith("clock unreachable")]
    back = [m for m in console if m.startswith("clock reachable again")]
    assert 1 <= len(lost) == len(back) <= 2
    after = [r for t, r in fakes.requests if t > ERRORS[1]]
    assert after[:1] == ["GET /api/v1/settings"]
    assert "GET /api/v1/apps" in after[:4]
    in_errors = [p for p in pushes if ERRORS[0] + 0.5 < p["us"] / 1e6 - _us(START) / 1e6
                 < ERRORS[1]]
    assert in_errors == []

    # Pause and resume: no pushes between, the source stopped, then started again.
    ev = {x["ev"]: x for x in lines if x.get("ev") in ("pause", "resume")}
    p_us, r_us = ev["pause"]["us"], ev["resume"]["us"]
    assert not [p for p in pushes if p_us + 250_000 < p["us"] < r_us]
    starts = [x["us"] for x in lines if x.get("ev") == "source started"]
    stops = [x["us"] for x in lines if x.get("ev") == "source stopped"]
    assert len(starts) == 2 and starts[1] >= r_us
    assert any(p_us <= s < r_us for s in stops) and ev["resume"]["running"] == 0
    assert end["runs"] == 2 and end["status"]["paused"] is False
    assert "paused: no pushes, the source stopped" in console and "resumed" in console

    # The status at the end is small and JSON-able, with a heap.
    st = end["status"]
    assert st["heap_free"] > 0 and st["heap_alloc"] > 0 and st["log_last_seq"] > 10
    assert sys.getsizeof(json.dumps(st)) < 2000
