"""The clock's own program (``arthur.onclock.app.ClockApp``) against the fake clock, with a
fake source and fake time: the expectations ``tests/test_feeder.py`` sets for the Mac's
feeder, case for case where they apply, then what is new on the clock (waiting for AWTRIX,
pause and resume, stop and restart, the plan built ahead, the status, the log ring).

The program runs here under CPython; ``tests/test_onclock_mpy.py`` runs it under MicroPython.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arthur.config import default_config
from arthur.mapping import to_display_state
from arthur.model import Cue, FeederHealth, RobotSnapshot, Weather
from arthur.onclock import app as appmod
from arthur.onclock.app import ClockApp
from arthur.onclock.awtrix import TransportError
from arthur.onclock.clocklog import ClockLog
from arthur.render import Frame, overlay_stale, render
from arthur.render import idle_show as show
from arthur.sim.server import CLOCKS, create_app
from arthur.tools.export_config import export

pytestmark = pytest.mark.usefixtures("loopback_only")

CFG = default_config()
START = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)


class FakeTime:
    """Monotonic and wall time that move only when the program sleeps; ``stop_at`` asks the
    app to stop."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.app: ClockApp | None = None
        self.stop_at: float | None = None
        self.on_tick = None

    def __call__(self) -> float:
        return self.t

    def wall(self) -> datetime:
        return START + timedelta(seconds=self.t - 1000.0)

    async def sleep(self, s: float) -> None:
        self.t += s
        if self.on_tick is not None:
            self.on_tick(self)
        if self.stop_at is not None and self.t >= self.stop_at and self.app is not None:
            self.app.request_stop()
        await asyncio.sleep(0)


class FakeSource:
    def __init__(self, snapshot: RobotSnapshot | None, error=None) -> None:
        self.snapshot = snapshot
        self.error = error
        self.configs = []
        self.raise_in_view = False
        self.runs = 0
        self.stopped = 0
        self.running = 0
        self.most_running = 0

    def view(self, now):
        if self.raise_in_view:
            raise RuntimeError("a bug in the source")
        return self.snapshot, FeederHealth(last_contact_at=now, error=self.error)

    async def run(self, stop):
        self.runs += 1
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            await stop.wait()
        finally:
            self.running -= 1
            self.stopped += 1

    def update_config(self, cfg):
        self.configs.append(cfg)


class FakeWeather:
    def __init__(self, reading=None) -> None:
        self.reading = reading
        self.raise_in_view = False
        self.configs = []
        self.runs = 0
        self.stopped = False
        self.error = None

    def view(self, now):
        if self.raise_in_view:
            raise RuntimeError("a bug in the weather")
        return self.reading

    async def run(self, stop):
        self.runs += 1
        await stop.wait()
        self.stopped = True

    def update_config(self, cfg):
        self.configs.append(cfg)


ROBOT_OFF = RobotSnapshot(status="OFF", is_on=False, status_since=START - timedelta(hours=1))
READY = RobotSnapshot(status="READY", status_since=START - timedelta(hours=1))
WEATHER = Weather(temp_f=52.5, code=0, is_day=True, high_f=61.9, low_f=38.9,
                  fetched_at=START - timedelta(minutes=5))


@pytest.fixture
def clock():
    return FakeTime()


@pytest.fixture
async def client(aiohttp_client, clock):
    return await aiohttp_client(create_app(start_tasks=False, time_fn=clock))


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps(export()))
    return str(tmp_path)


def base(client) -> str:
    return str(client.make_url("")).rstrip("/")


def the_clock(client):
    (c,) = client.app[CLOCKS].values()
    return c


def make_app(client, clock, source, root, *, weather=None) -> ClockApp:
    app = ClockApp(source, root=root, base_url=base(client), ca_file=None,
                   wall_fn=clock.wall, sleep=clock.sleep, time_fn=clock)
    app.echo = False
    app.weather = weather if weather is not None else FakeWeather()
    clock.app = app
    return app


async def run_for(app, clock, seconds: float) -> int:
    clock.stop_at = clock.t + seconds
    return await asyncio.wait_for(app.run(), timeout=20)


def logged(app) -> str:
    lines, _ = app.log.read(0, 10**9)
    return "\n".join(f"{ln['level']} {ln['msg']}" for _, ln in lines
                     if ln.get("kind") == "console")


def expected(snapshot, health, now, weather=None) -> Frame:
    return render(to_display_state(snapshot, health, now, CFG, weather=weather), now, CFG)


# ---- the feeder's expectations --------------------------------------------------------------


async def test_mapped_frame_lands_and_heartbeats(client, clock, root):
    source = FakeSource(ROBOT_OFF)
    app = make_app(client, clock, source, root)
    assert await run_for(app, clock, 65) == 0
    c = the_clock(client)
    assert not c.enlarge_apps
    assert c.in_rotation() == ["litter"]  # Time and Status were taken out
    want = expected(ROBOT_OFF, FeederHealth(last_contact_at=clock.wall()), clock.wall())
    assert c.screen() == want
    assert app.pusher.pushes == 3  # the first frame, then heartbeats at 30 s and 60 s
    assert c.apps["litter"].lifetime_ms == round(CFG.timings.ttl_s * 1000)
    assert c.apps["litter"].expiry == "mark"
    assert source.stopped == 1  # the source task was stopped with the app
    text = logged(app)
    assert "display: robot_off" in text and "first frame pushed" in text


async def test_source_error_pushes_no_data(client, clock, root):
    source = FakeSource(READY)
    clock.on_tick = lambda c: setattr(source, "error", "api") if c.t >= 1005 else None
    app = make_app(client, clock, source, root)
    await run_for(app, clock, 10)
    assert app.shown == ("no_data", "api")
    now = clock.wall()
    want = expected(READY, FeederHealth(last_contact_at=now, error="api"), now)
    assert the_clock(client).screen() == want
    text = logged(app)
    assert "display: ready -> no_data (api)" in text
    assert text.count("Whisker contact lost (api)") == 1


async def test_whisker_contact_regained_is_logged(client, clock, root):
    source = FakeSource(READY, error="auth")
    clock.on_tick = lambda c: setattr(source, "error", None) if c.t >= 1003 else None
    app = make_app(client, clock, source, root)
    await run_for(app, clock, 5)
    text = logged(app)
    assert text.count("Whisker contact lost (auth)") == 1
    assert text.count("Whisker contact regained") == 1


class FlakyTransport:
    """The real transport, with a switch that makes the clock unreachable."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.down = False

    async def show(self, frame, ttl_s):
        if self.down:
            raise TransportError("GET /api/v1/settings: cannot connect")
        await self.inner.show(frame, ttl_s)


async def test_clock_outage_is_logged_once_and_recovers(client, clock, root):
    source = FakeSource(ROBOT_OFF)
    app = make_app(client, clock, source, root)
    flaky = FlakyTransport(app.transport)
    app.pusher.transport = flaky

    def outage(c):
        flaky.down = 1002 <= c.t < 1010
        if 1002 <= c.t < 1010:
            source.snapshot = READY  # a new frame to push while the clock is away

    clock.on_tick = outage
    await run_for(app, clock, 15)
    text = logged(app)
    assert text.count("clock unreachable") == 1
    assert text.count("clock reachable again") == 1
    assert 6 <= app.pusher.failures <= 9  # about once a second, not every beat
    now = clock.wall()
    assert the_clock(client).screen() == expected(READY, FeederHealth(last_contact_at=now), now)
    assert not app.transport.conn.is_open  # closed when the app stopped


async def test_stopped_app_goes_stale_after_ttl(client, clock, root):
    app = make_app(client, clock, FakeSource(ROBOT_OFF), root)
    await run_for(app, clock, 5)
    c = the_clock(client)
    last = c.screen()
    clock.t = c.last_push_at + CFG.timings.ttl_s - 0.5
    assert c.screen() == last
    clock.t += 1
    assert c.screen() == overlay_stale(last, CFG.rgb("stale"))
    assert "litter" in c.apps  # not cleared


async def test_render_error_pushes_nothing_and_logs_once(client, clock, root):
    source = FakeSource(ROBOT_OFF)
    source.raise_in_view = True
    app = make_app(client, clock, source, root)
    await run_for(app, clock, 3)
    assert app.pusher.pushes == 0 and the_clock(client).apps == {}
    assert logged(app).count("could not map or draw") == 1


async def test_config_reload_keeps_last_good(client, clock, root):
    source = FakeSource(ROBOT_OFF)
    app = make_app(client, clock, source, root)
    path = f"{root}/config.json"
    good = json.loads(Path(path).read_text())
    first = app.cfg

    def edits(c):
        if c.t == 1003:
            with open(path, "w") as fh:
                fh.write('{"timings": ')  # broken JSON
            app.reload_config()
        elif c.t == 1006:
            data = dict(good)
            data["timings"] = dict(good["timings"], heartbeat_s=20)
            with open(path, "w") as fh:
                fh.write(json.dumps(data))
            app.reload_config()

    clock.on_tick = edits
    await run_for(app, clock, 5)
    assert app.cfg is first
    assert "not reloaded, keeping the last good one" in logged(app)
    clock.stop_at = None
    app._exit.clear()
    app._exit_code = None
    await run_for(app, clock, 4)
    assert app.cfg.timings.heartbeat_s == 20
    assert source.configs and source.configs[-1].timings.heartbeat_s == 20
    assert "config reloaded" in logged(app)


# ---- the beat ---------------------------------------------------------------------------


class SlowTransport:
    """The real transport, with every push taking ``delay_s`` of fake time."""

    def __init__(self, inner, clock: FakeTime, delay_s: float) -> None:
        self.inner, self.clock, self.delay_s = inner, clock, delay_s

    async def show(self, frame, ttl_s):
        self.clock.t += self.delay_s
        await self.inner.show(frame, ttl_s)


def tick_times(source: FakeSource, clock: FakeTime) -> list[float]:
    seen: list[float] = []
    view = source.view

    def recording(now):
        seen.append(clock.t)
        return view(now)

    source.view = recording
    return seen


async def test_ticks_land_on_eighths_of_the_wall_clock(client, clock, root):
    clock.t = 1000.3
    source = FakeSource(ROBOT_OFF)
    seen = tick_times(source, clock)
    await run_for(make_app(client, clock, source, root), clock, 3)
    assert seen[0] == 1000.3
    assert seen[1] == 1000.375
    assert all(t * 8 == int(t * 8) for t in seen[1:])
    assert all(b - a == 0.125 for a, b in itertools.pairwise(seen[1:]))
    assert 1001.0 in seen and 1002.0 in seen


async def test_slow_push_drops_ticks_rather_than_queueing(client, clock, root):
    source = FakeSource(ROBOT_OFF)
    seen = tick_times(source, clock)
    app = make_app(client, clock, source, root)
    app.pusher.transport = SlowTransport(app.transport, clock, 0.3)

    def new_frame_every_tick(c):
        source.snapshot = READY if source.snapshot is ROBOT_OFF else ROBOT_OFF

    clock.on_tick = new_frame_every_tick
    await run_for(app, clock, 3)
    assert seen[:4] == [1000.0, 1000.375, 1000.75, 1001.125]
    assert all(b - a == 0.375 for a, b in itertools.pairwise(seen))
    assert app.pusher.pushes == len(seen)
    assert app.late_beats == len(seen)  # each one took 0.3 s


async def test_run_stops_cleanly(client, clock, root):
    source = FakeSource(ROBOT_OFF)
    app = make_app(client, clock, source, root)
    rc = await run_for(app, clock, 2)
    assert rc == 0 and source.stopped == 1 and "litter" in the_clock(client).apps


# ---- the weather and the idle show's isolation (D-0019) ----------------------------------


def record_states(monkeypatch, *, cue: Cue | None = None) -> list:
    states = []

    def mapping(*a, **kw):
        if cue is not None:
            kw["cue"] = cue
        state = to_display_state(*a, **kw)
        states.append(state)
        return state

    monkeypatch.setattr(appmod, "to_display_state", mapping)
    return states


async def test_weather_reaches_ready_and_only_ready(client, clock, root, monkeypatch):
    states = record_states(monkeypatch)
    source = FakeSource(READY)
    weather = FakeWeather(WEATHER)
    clock.on_tick = lambda c: setattr(source, "snapshot", ROBOT_OFF) if c.t >= 1002 else None
    app = make_app(client, clock, source, root, weather=weather)
    await run_for(app, clock, 4)
    ready = [s for s in states if s.kind == "ready"]
    off = [s for s in states if s.kind == "robot_off"]
    assert ready and off
    assert all(s.weather == WEATHER for s in ready)
    assert all(s.weather is None for s in off)
    assert weather.runs == 1 and weather.stopped


async def test_no_weather_table_maps_none(client, clock, tmp_path, monkeypatch):
    data = export()
    del data["weather"]
    (tmp_path / "config.json").write_text(json.dumps(data))
    states = record_states(monkeypatch)
    source = FakeSource(READY)
    app = ClockApp(source, root=str(tmp_path), base_url=base(client), ca_file=None,
                   wall_fn=clock.wall, sleep=clock.sleep, time_fn=clock)
    app.echo = False
    clock.app = app
    assert app.cfg.weather is None and app.weather.view(clock.wall()) is None
    await run_for(app, clock, 2)  # its weather service runs and fetches nothing
    assert states and all(s.kind == "ready" and s.weather is None for s in states)
    assert app.weather.fetches == 0


async def test_weather_view_failing_carries_on_without_weather(client, clock, root,
                                                                 monkeypatch):
    states = record_states(monkeypatch)
    weather = FakeWeather(WEATHER)
    weather.raise_in_view = True
    clock.on_tick = lambda c: setattr(weather, "raise_in_view", c.t < 1003)
    app = make_app(client, clock, FakeSource(READY), root, weather=weather)
    await run_for(app, clock, 5)
    assert app.pusher.pushes >= 1 and "litter" in the_clock(client).apps
    early = states[:8]
    assert early and all(s.kind == "ready" and s.weather is None for s in early)
    assert states[-1].weather == WEATHER
    text = logged(app)
    assert text.count("could not read the weather") == 1
    assert text.count("weather readable again") == 1
    assert "could not map or draw" not in text


def failing_cue_render(monkeypatch) -> list:
    asked = []

    def broken(state, now, cfg, **kw):
        asked.append(state)
        if state.cue is not None:
            raise ValueError("a bug in the scene")
        return render(state, now, cfg, **kw)

    monkeypatch.setattr(appmod, "render", broken)
    return asked


async def test_failing_cue_falls_back_to_the_home_page(client, clock, root, monkeypatch):
    cue = Cue(kind="scene", at=START, length_s=36.0, name="rat")
    record_states(monkeypatch, cue=cue)
    asked = failing_cue_render(monkeypatch)
    app = make_app(client, clock, FakeSource(READY), root)
    await run_for(app, clock, 3)
    c = the_clock(client)
    now = START + timedelta(seconds=c.last_push_at - 1000.0)
    pinned = to_display_state(READY, FeederHealth(last_contact_at=now), now, CFG, cue=cue)
    assert c.screen() == render(replace(pinned, cue=None), now, CFG)
    assert any(s.cue is None for s in asked)
    lines = [ln for _, ln in app.log.read(0, 10**9)[0]
             if "could not draw the idle show" in ln.get("msg", "")]
    assert len(lines) == 1 and lines[0]["level"] == "error" and "trace" in lines[0]
    assert "scene rat" in lines[0]["msg"]
    assert "could not map or draw" not in logged(app)


async def test_each_failing_cue_logs_once_and_repeats_at_debug(client, clock, root,
                                                               monkeypatch):
    cues = [Cue(kind="scene", at=START, length_s=36.0, n=k) for k in range(3)]
    current = [cues[0]]

    def mapping(*a, **kw):
        kw["cue"] = current[0]
        return to_display_state(*a, **kw)

    monkeypatch.setattr(appmod, "to_display_state", mapping)
    failing_cue_render(monkeypatch)
    clock.on_tick = lambda c: current.__setitem__(0, cues[min(int(c.t - 1000), 2)])
    app = make_app(client, clock, FakeSource(READY), root)
    await run_for(app, clock, 4)
    levels = [ln["level"] for _, ln in app.log.read(0, 10**9)[0]
              if "could not draw the idle show" in ln.get("msg", "")]
    assert levels == ["error", "debug", "debug"]


async def test_render_failure_without_a_cue_pushes_nothing(client, clock, root, monkeypatch):
    def broken(state, now, cfg, **kw):
        raise ValueError("a bug in the home page")

    monkeypatch.setattr(appmod, "render", broken)
    app = make_app(client, clock, FakeSource(ROBOT_OFF), root)
    await run_for(app, clock, 3)
    assert app.pusher.pushes == 0 and the_clock(client).apps == {}
    assert logged(app).count("could not map or draw") == 1


class DyingSource(FakeSource):
    async def run(self, stop):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError("source bug")
        await stop.wait()
        self.stopped += 1


class DyingWeather(FakeWeather):
    async def run(self, stop):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError("weather bug")
        await stop.wait()
        self.stopped = True


async def test_a_dead_whisker_task_is_restarted(client, clock, root):
    source = DyingSource(READY)
    weather = FakeWeather()
    app = make_app(client, clock, source, root, weather=weather)
    runs_at = {}
    clock.on_tick = lambda c: runs_at.setdefault(int(c.t - 1000), source.runs)
    await run_for(app, clock, appmod.SOURCE_RESTART_S + 3)
    assert runs_at[int(appmod.SOURCE_RESTART_S) - 1] == 1
    assert source.runs == 2 and source.stopped == 1
    assert weather.runs == 1
    text = logged(app)
    assert "the Whisker source stopped unexpectedly (RuntimeError: source bug)" in text
    assert text.count("stopped unexpectedly") == 1
    assert text.count("restarting the Whisker source") == 1


async def test_a_dead_weather_task_is_restarted(client, clock, root):
    source = FakeSource(READY)
    weather = DyingWeather()
    app = make_app(client, clock, source, root, weather=weather)
    seen = {}
    clock.on_tick = lambda c: seen.setdefault(int(c.t - 1000), (weather.runs, app.pusher.pushes))
    await run_for(app, clock, appmod.SOURCE_RESTART_S + 3)
    runs, pushes = seen[int(appmod.SOURCE_RESTART_S) - 1]
    assert runs == 1 and pushes >= 1
    text = logged(app)
    assert "the weather source stopped unexpectedly (RuntimeError: weather bug)" in text
    assert weather.runs == 2 and weather.stopped and source.stopped == 1
    assert text.count("restarting the weather source") == 1


async def test_config_reload_reaches_the_weather(client, clock, root):
    weather = FakeWeather()
    app = make_app(client, clock, FakeSource(READY), root, weather=weather)
    path = f"{root}/config.json"

    def edit(c):
        if c.t == 1002:
            data = json.loads(Path(path).read_text())
            data["weather"]["refresh_s"] = 600
            with open(path, "w") as fh:
                fh.write(json.dumps(data))
            app.reload_config()

    clock.on_tick = edit
    await run_for(app, clock, 4)
    assert weather.configs and weather.configs[-1].weather.refresh_s == 600


# ---- new on the clock -------------------------------------------------------------------


async def test_waits_for_awtrix_at_start_without_exiting(aiohttp_server, clock, root):
    """No API at start: the app keeps trying (1 s doubling to 10 s) and starts nothing else;
    once the API answers it carries on as usual."""
    from aiohttp import web

    probe = await aiohttp_server(web.Application())  # a free port, then closed
    port = probe.port
    await probe.close()
    source = FakeSource(ROBOT_OFF)
    app = ClockApp(source, root=root, base_url=f"http://127.0.0.1:{port}", ca_file=None,
                   wall_fn=clock.wall, sleep=clock.sleep, time_fn=clock)
    app.echo = False
    app.weather = FakeWeather()
    clock.app = app
    server = {}

    async def bring_up():
        while clock.t < 1020:
            await asyncio.sleep(0)
        server["s"] = await aiohttp_server(create_app(start_tasks=False, time_fn=clock),
                                           port=port)

    up = asyncio.create_task(bring_up())
    rc = await run_for(app, clock, 40)
    await up
    assert rc == 0
    c = server["s"].app[CLOCKS]["52x16"]
    assert "litter" in c.apps and not c.enlarge_apps
    assert source.runs == 1  # started only once AWTRIX answered
    text = logged(app)
    assert text.count("waiting for AWTRIX") == 1 and "AWTRIX answered after" in text
    await server["s"].close()


async def test_stop_while_waiting_for_awtrix_returns(clock, root):
    source = FakeSource(ROBOT_OFF)
    app = ClockApp(source, root=root, base_url="http://127.0.0.1:9", ca_file=None,
                   wall_fn=clock.wall, sleep=clock.sleep, time_fn=clock)
    app.echo = False
    app.weather = FakeWeather()
    clock.app = app
    clock.on_tick = lambda c: app.request_restart() if c.t > 1005 else None
    rc = await asyncio.wait_for(app.run(), 10)
    assert rc == 42 and source.runs == 0


async def test_pause_stops_pushes_and_the_source_resume_starts_both(client, clock, root):
    source = FakeSource(READY)
    app = make_app(client, clock, source, root)
    c = the_clock(client)
    marks = {}

    def control(t):
        if t.t == 1003:
            app.pause()
            marks["paused"] = c.push_count
        elif t.t == 1010:
            marks["before_resume"] = c.push_count
            marks["running"] = source.running
            app.resume()

    clock.on_tick = control
    await run_for(app, clock, 14)
    assert marks["before_resume"] in (marks["paused"], marks["paused"] + 1)
    assert marks["running"] == 0  # the source was stopped while paused
    assert source.runs == 2 and source.most_running == 1  # never two at once
    assert c.push_count > marks["before_resume"]  # pushing again
    st = app.status()
    assert st["paused"] is False
    text = logged(app)
    assert "paused" in text and "resumed" in text


async def test_resume_before_the_source_stopped_never_runs_two(client, clock, root):
    class SlowStop(FakeSource):
        async def run(self, stop):
            self.runs += 1
            self.running += 1
            self.most_running = max(self.most_running, self.running)
            await stop.wait()
            for _ in range(20):  # takes a while to let go of its connection
                await asyncio.sleep(0)
            self.running -= 1
            self.stopped += 1

    source = SlowStop(READY)
    app = make_app(client, clock, source, root)

    def control(t):
        if t.t == 1002:
            app.pause()
        elif t.t == 1002.125:
            app.resume()

    clock.on_tick = control
    await run_for(app, clock, 5)
    assert source.runs == 2 and source.most_running == 1


@pytest.mark.parametrize("how,code", [("stop", 0), ("restart", 42)])
async def test_stop_and_restart_return_their_exit_codes(client, clock, root, how, code):
    source = FakeSource(READY)
    app = make_app(client, clock, source, root)
    beats = []
    clock.on_tick = lambda t: (beats.append(t.t),
                               t.t == 1004 and getattr(app, "request_" + how)())
    rc = await asyncio.wait_for(app.run(), 10)
    assert rc == code
    assert max(beats) <= 1004.125  # at the next beat
    assert source.stopped == 1


async def test_status_is_small_and_json_able(client, clock, root):
    app = make_app(client, clock, FakeSource(READY), root)
    await run_for(app, clock, 3)
    st = app.status()
    assert json.loads(json.dumps(st)) == st
    assert set(st) >= {"version", "uptime_s", "kind", "detail", "health", "since_contact_s",
                       "since_push_s", "pushes", "push_failures", "heap_free", "heap_alloc",
                       "paused", "log_first_seq", "log_last_seq"}
    assert st["kind"] == "ready" and st["pushes"] == app.pusher.pushes >= 1
    assert st["health"] == {"error": None, "ws_connected": True}
    assert st["log_last_seq"] == app.log.last_seq > 0
    assert 2.9 <= st["uptime_s"] <= 3.2


async def test_console_lines_go_to_the_ring_with_sequence_numbers(client, clock, root):
    ring = ClockLog(4096)
    app = ClockApp(FakeSource(READY), root=root, base_url=base(client), ca_file=None,
                   log=ring, wall_fn=clock.wall, sleep=clock.sleep, time_fn=clock)
    app.echo = False
    app.weather = FakeWeather()
    clock.app = app
    await run_for(app, clock, 2)
    lines, nxt = ring.read(0, 10**6)
    assert [s for s, _ in lines] == list(range(1, len(lines) + 1)) and nxt == len(lines)
    assert all(ln["kind"] == "console" and ln["ts"].endswith("Z") for _, ln in lines)
    assert lines[0][1]["ts"] == "2026-11-17T12:00:00.000Z"


# ---- the scene's plan, built ahead -----------------------------------------------------------


def idle_cfg_root(tmp_path):
    data = export()
    data["idle"] = dict(data.get("idle", {}), page_pct=30, scene_pct=20, round_s=180,
                        quiet_from_h=0, quiet_until_h=0)
    (tmp_path / "config.json").write_text(json.dumps(data))
    return str(tmp_path)


async def test_the_scene_plan_is_built_on_a_home_beat_before_its_slot(client, clock, tmp_path,
                                                                     monkeypatch):
    """The plan is built on the beat half a second before the scene slot, after that beat's
    push, and the scene's first body frame finds it kept. Every frame pushed is the one the
    Mac's pipeline draws for the same moment."""
    root = idle_cfg_root(tmp_path)
    built = []
    real = show.scene_beats

    def counting(name, cfg, n, frames):
        built.append((clock.t, name, n))
        return real(name, cfg, n, frames)

    monkeypatch.setattr(show, "scene_beats", counting)
    # START is 12:00 UTC, a round boundary; the scene slot is 144-180 s into the round.
    clock.t = 1000.0 + 140
    source = FakeSource(READY)
    app = make_app(client, clock, source, root)
    pushed = []
    app.trace = lambda now, frame, state, reason: pushed.append((now, frame, state))
    show.forget_plan()
    await run_for(app, clock, 44)  # through the whole scene and back home
    n = int((START - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) // 180
    assert [(t, k) for t, _, k in built] == [(1000.0 + 144 - appmod.PLAN_LEAD_S, n)]
    assert app.stats.parts["plan built ahead"][0] == 1
    cfg = app.cfg
    scenes = [p for p in pushed if p[2].cue is not None and p[2].cue.kind == "scene"]
    assert len(scenes) > 100  # the frames that changed, at 8 a second
    assert show._plan is None  # let go once the slot was over
    for now, frame, state in pushed:
        assert frame == render(state, now, cfg)


async def test_the_weather_is_told_when_the_screen_is_quiet(client, clock, tmp_path):
    """Quiet: the home page with no page or scene within 5 s, or any screen but idle."""
    root = idle_cfg_root(tmp_path)
    app = make_app(client, clock, FakeSource(READY), root)
    seen = {}

    def note(c):
        seen[round(c.t - 1000, 3)] = app._quiet()

    clock.on_tick = note
    clock.t = 1000.0 + 30
    await run_for(app, clock, 120)  # home, the page from 45 s, home again from 99 s
    assert seen[31.0] and seen[39.875]
    assert not seen[40.0] and not seen[60.0] and not seen[98.875]
    assert seen[99.125] and seen[138.875] and not seen[139.0]  # (state from the beat before)
    app.state = replace(app.state, kind="in_use", cue=None)
    assert app._quiet()


async def test_no_plan_is_built_ahead_at_night_or_off_idle(client, clock, tmp_path,
                                                           monkeypatch):
    data = export()
    data["idle"] = dict(data.get("idle", {}), page_pct=30, scene_pct=20, round_s=180,
                        quiet_from_h=0, quiet_until_h=23)  # 12:00 UTC is quiet in Boston
    (tmp_path / "config.json").write_text(json.dumps(data))
    built = []
    monkeypatch.setattr(show, "prepare_scene", lambda cue, cfg: built.append(cue) or True)
    clock.t = 1000.0 + 140
    app = make_app(client, clock, FakeSource(READY), str(tmp_path))
    await run_for(app, clock, 6)
    assert built == []
    clock.t = 1000.0 + 140
    app2 = make_app(client, clock, FakeSource(ROBOT_OFF), idle_cfg_root(tmp_path))
    await run_for(app2, clock, 6)
    assert built == []


# ---- main(root, source_factory) ------------------------------------------------------------


async def test_main_builds_the_app_and_returns_its_exit_code(client, root, monkeypatch):
    from arthur.onclock import main as mainmod

    made = {}

    class Recording(ClockApp):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.echo = False
            self.weather = FakeWeather()
            made["app"] = self

    class StoppingSource(FakeSource):
        async def run(self, stop):
            await asyncio.sleep(0.3)
            made["app"].request_restart()
            await stop.wait()

    def factory(*, cfg, log, tls):
        made.update(cfg=cfg, log=log, tls=tls)
        return StoppingSource(READY)

    monkeypatch.setattr(mainmod, "ClockApp", Recording)
    rc = await asyncio.wait_for(mainmod.main(root, factory, ca_file="/no/such/ca.pem",
                                             base_url=base(client)), 10)
    assert rc == 42
    app = made["app"]
    assert made["log"] is app.log and made["tls"] is app.tls  # one CA bundle for both
    assert made["cfg"] == app.cfg
    assert "litter" in the_clock(client).apps


async def test_main_without_a_config_fails_with_1(tmp_path, capsys):
    from arthur.onclock.main import main

    rc = await main(str(tmp_path), lambda **kw: FakeSource(READY), ca_file=None)
    assert rc == 1
    assert "failed" in capsys.readouterr().err
