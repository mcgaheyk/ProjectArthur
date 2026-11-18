"""The feeder end to end against the fake clock, with a fake source and fake time."""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from arthur import feeder as fd
from arthur.config import DEFAULT_PATH, default_config
from arthur.mapping import to_display_state
from arthur.model import Cue, FeederHealth, RobotSnapshot, Weather
from arthur.render import Frame, overlay_stale, render
from arthur.sim.server import CLOCKS, create_app
from arthur.sources.weather import WeatherSource
from arthur.transports import AwtrixNgTransport, TransportError

CFG = default_config()
START = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)


class FakeTime:
    """Monotonic and wall time that move only when the feeder sleeps."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.stop = asyncio.Event()
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
        if self.stop_at is not None and self.t >= self.stop_at:
            self.stop.set()
        await asyncio.sleep(0)


class FakeSource:
    def __init__(self, clock: FakeTime, snapshot: RobotSnapshot | None, error=None) -> None:
        self.clock = clock
        self.snapshot = snapshot
        self.error = error
        self.configs = []
        self.raise_in_view = False
        self.stopped = False

    def view(self, now):
        if self.raise_in_view:
            raise RuntimeError("a bug in the source")
        # Always in contact, unless told otherwise.
        return self.snapshot, FeederHealth(last_contact_at=now, error=self.error)

    async def run(self, stop):
        await stop.wait()
        self.stopped = True

    def update_config(self, cfg):
        self.configs.append(cfg)


class FlakyTransport:
    """The real transport, with a switch that makes the clock unreachable."""

    def __init__(self, inner: AwtrixNgTransport) -> None:
        self.inner = inner
        self.down = False
        self.closed = False

    async def show(self, frame, ttl_s):
        if self.down:
            raise TransportError("GET http://clock/api/v1/settings: ClientConnectorError")
        await self.inner.show(frame, ttl_s)

    async def clear(self):
        await self.inner.clear()

    async def close(self):
        self.closed = True


ROBOT_OFF = RobotSnapshot(status="OFF", is_on=False, status_since=START - timedelta(hours=1))
READY = RobotSnapshot(status="READY", status_since=START - timedelta(hours=1))


@pytest.fixture
def clock():
    return FakeTime()


@pytest.fixture
async def client(aiohttp_client, clock):
    return await aiohttp_client(create_app(start_tasks=False, time_fn=clock))


def base(client) -> str:
    return str(client.make_url("")).rstrip("/")


def the_clock(client):
    (c,) = client.app[CLOCKS].values()
    return c


def make_feeder(client, clock, source, *, flaky=False, config_path=None):
    transport = AwtrixNgTransport(base(client), session=client.session, time_fn=clock)
    if flaky:
        transport = FlakyTransport(transport)
    return fd.Feeder(source, transport, CFG, config_path=config_path, clock_label=base(client),
                     time_fn=clock, wall_fn=clock.wall, sleep=clock.sleep)


async def run_for(feeder, clock, seconds: float) -> None:
    clock.stop = asyncio.Event()
    clock.stop_at = clock.t + seconds
    await asyncio.wait_for(feeder.run(clock.stop), timeout=20)


def expected(snapshot, health, now) -> Frame:
    return render(to_display_state(snapshot, health, now, CFG), now, CFG)


async def test_mapped_frame_lands_and_heartbeats(client, clock, caplog):
    caplog.set_level(logging.INFO)
    source = FakeSource(clock, ROBOT_OFF)
    feeder = make_feeder(client, clock, source)
    await run_for(feeder, clock, 65)
    c = the_clock(client)
    assert not c.enlarge_apps
    assert c.in_rotation() == ["litter"]  # Time and Status were taken out
    want = expected(ROBOT_OFF, FeederHealth(last_contact_at=clock.wall()), clock.wall())
    assert c.screen() == want
    assert feeder.pusher.pushes == 3  # the first frame, then heartbeats at 30 s and 60 s
    assert c.apps["litter"].lifetime_ms == round(CFG.timings.ttl_s * 1000)
    assert source.stopped  # the source task was stopped with the feeder
    assert "display: robot_off" in caplog.text and "first frame pushed" in caplog.text


async def test_source_error_pushes_no_data(client, clock, caplog):
    caplog.set_level(logging.INFO)
    source = FakeSource(clock, READY)
    clock.on_tick = lambda c: setattr(source, "error", "api") if c.t >= 1005 else None
    feeder = make_feeder(client, clock, source)
    await run_for(feeder, clock, 10)
    assert feeder.shown == ("no_data", "api")
    now = clock.wall()
    want = expected(READY, FeederHealth(last_contact_at=now, error="api"), now)
    assert the_clock(client).screen() == want
    assert "display: ready → no_data (api)" in caplog.text
    assert caplog.text.count("Whisker contact lost (api)") == 1


async def test_whisker_contact_regained_is_logged(client, clock, caplog):
    caplog.set_level(logging.INFO)
    source = FakeSource(clock, READY, error="auth")
    clock.on_tick = lambda c: setattr(source, "error", None) if c.t >= 1003 else None
    await run_for(make_feeder(client, clock, source), clock, 5)
    assert caplog.text.count("Whisker contact lost (auth)") == 1
    assert caplog.text.count("Whisker contact regained") == 1


async def test_clock_outage_is_logged_once_and_recovers(client, clock, caplog):
    caplog.set_level(logging.INFO)
    source = FakeSource(clock, ROBOT_OFF)
    feeder = make_feeder(client, clock, source, flaky=True)
    transport = feeder.transport

    def outage(c):
        transport.down = 1002 <= c.t < 1010
        if c.t >= 1002 and c.t < 1010:
            source.snapshot = READY  # a new frame to push while the clock is away

    clock.on_tick = outage
    await run_for(feeder, clock, 15)
    assert caplog.text.count("clock unreachable") == 1
    assert caplog.text.count("clock reachable again") == 1
    # Retried about once a second during the 8 s outage, not every tick.
    assert 6 <= feeder.pusher.failures <= 9
    now = clock.wall()
    assert the_clock(client).screen() == expected(READY, FeederHealth(last_contact_at=now), now)
    assert transport.closed


async def test_stopped_feeder_goes_stale_after_ttl(client, clock):
    source = FakeSource(clock, ROBOT_OFF)
    await run_for(make_feeder(client, clock, source), clock, 5)
    c = the_clock(client)
    last = c.screen()
    clock.t = c.last_push_at + CFG.timings.ttl_s - 0.5
    assert c.screen() == last
    clock.t += 1
    assert c.screen() == overlay_stale(last, CFG.rgb("stale"))
    assert "litter" in c.apps  # not cleared


async def test_render_error_pushes_nothing_and_logs_once(client, clock, caplog):
    source = FakeSource(clock, ROBOT_OFF)
    source.raise_in_view = True
    feeder = make_feeder(client, clock, source)
    await run_for(feeder, clock, 3)
    assert feeder.pusher.pushes == 0 and the_clock(client).apps == {}
    assert caplog.text.count("could not map or draw") == 1


async def test_config_reload_keeps_last_good(client, clock, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "display.toml"
    shutil.copy(DEFAULT_PATH, path)
    source = FakeSource(clock, ROBOT_OFF)
    feeder = make_feeder(client, clock, source, config_path=path)
    good = path.read_text()
    stamp = [path.stat().st_mtime_ns]

    def touch(text: str) -> None:
        path.write_text(text)
        stamp[0] += 10**9
        os.utime(path, ns=(stamp[0], stamp[0]))

    def edits(c):
        if c.t == 1003:
            touch(good.replace("[timings]", "[timings\n"))  # broken TOML
        elif c.t == 1006:
            touch(good.replace("heartbeat_s = 30", "heartbeat_s = 20"))

    clock.on_tick = edits
    await run_for(feeder, clock, 5)
    assert feeder.cfg is CFG
    assert "not reloaded, keeping the last good one" in caplog.text
    await run_for(feeder, clock, 4)
    assert feeder.cfg.timings.heartbeat_s == 20
    assert source.configs and source.configs[-1].timings.heartbeat_s == 20
    assert "config reloaded" in caplog.text


# ---- the beat ---------------------------------------------------------------------------


class SlowTransport:
    """The real transport, with every push taking ``delay_s`` of fake time."""

    def __init__(self, inner: AwtrixNgTransport, clock: FakeTime, delay_s: float) -> None:
        self.inner, self.clock, self.delay_s = inner, clock, delay_s

    async def show(self, frame, ttl_s):
        self.clock.t += self.delay_s
        await self.inner.show(frame, ttl_s)

    async def clear(self):
        await self.inner.clear()

    async def close(self):
        await self.inner.close()


def tick_times(source: FakeSource, clock: FakeTime) -> list[float]:
    """Record the fake time of every tick (each tick asks the source once)."""
    seen: list[float] = []
    view = source.view

    def recording(now):
        seen.append(clock.t)
        return view(now)

    source.view = recording
    return seen


async def test_ticks_land_on_eighths_of_the_wall_clock(client, clock):
    clock.t = 1000.3  # start between beats: 0.3 s past a whole second of the wall clock
    source = FakeSource(clock, ROBOT_OFF)
    seen = tick_times(source, clock)
    await run_for(make_feeder(client, clock, source), clock, 3)
    assert seen[0] == 1000.3
    assert seen[1] == 1000.375  # the next eighth, not 0.125 s after the start
    assert all(t * 8 == int(t * 8) for t in seen[1:])  # every later tick is on an eighth
    assert all(b - a == 0.125 for a, b in itertools.pairwise(seen[1:]))
    assert 1001.0 in seen and 1002.0 in seen  # whole seconds are beats


async def test_slow_push_drops_ticks_rather_than_queueing(client, clock):
    source = FakeSource(clock, ROBOT_OFF)
    seen = tick_times(source, clock)
    inner = AwtrixNgTransport(base(client), session=client.session, time_fn=clock)
    feeder = fd.Feeder(source, SlowTransport(inner, clock, 0.3), CFG, time_fn=clock,
                       wall_fn=clock.wall, sleep=clock.sleep)

    def new_frame_every_tick(c):  # so every tick pushes, and every push is slow
        source.snapshot = READY if source.snapshot is ROBOT_OFF else ROBOT_OFF

    clock.on_tick = new_frame_every_tick
    await run_for(feeder, clock, 3)
    # Each tick takes 0.3 s: the next one is the first beat after it ends (0.375 s on), and the
    # two beats it covered are dropped, not run late back to back.
    assert seen[:4] == [1000.0, 1000.375, 1000.75, 1001.125]
    assert all(b - a == 0.375 for a, b in itertools.pairwise(seen))
    assert feeder.pusher.pushes == len(seen)


# ---- the command line ------------------------------------------------------------------


def test_cli_needs_a_clock(capsys):
    assert fd.main([], environ={}) == fd.EXIT_USAGE
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "--clock" in err[0] and "CLOCK_URL" in err[0]


@pytest.mark.parametrize("argv,env,url", [
    ([], {"CLOCK_URL": "192.168.1.50"}, "http://192.168.1.50"),
    (["--clock", "http://clock.local/"], {"CLOCK_URL": "192.168.1.50"}, "http://clock.local"),
])
def test_cli_clock_address(monkeypatch, argv, env, url):
    seen = {}

    async def fake_run(opts, cfg, base_url, credentials, **kw):
        seen.update(url=base_url, creds=credentials, app=opts.app, log_dir=str(opts.log_dir))
        return 0

    monkeypatch.setattr(fd, "run", fake_run)
    monkeypatch.setattr(fd, "setup_logging", lambda *a: None)
    env = {**env, "WHISKER_USERNAME": "u", "WHISKER_PASSWORD": "p"}
    assert fd.main(argv, environ=env) == 0
    assert seen == {"url": url, "creds": ("u", "p"), "app": "litter", "log_dir": "logs"}


def test_cli_missing_credentials_still_runs(monkeypatch):
    seen = {}

    async def fake_run(opts, cfg, base_url, credentials, **kw):
        seen["creds"] = credentials
        return 0

    monkeypatch.setattr(fd, "run", fake_run)
    monkeypatch.setattr(fd, "setup_logging", lambda *a: None)
    assert fd.main(["--clock", "h"], environ={}) == 0
    assert seen["creds"] is None


def test_cli_bad_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[timings\n")
    assert fd.main(["--clock", "h", "--config", str(bad)], environ={}) == fd.EXIT_USAGE
    assert "cannot load the config" in capsys.readouterr().err


async def test_run_stops_cleanly(client, clock, tmp_path):
    """fd.run with an injected source and transport; stop ends it with exit status 0."""
    source = FakeSource(clock, ROBOT_OFF)
    transport = AwtrixNgTransport(base(client), session=client.session, time_fn=clock)
    opts = fd.build_parser().parse_args(["--clock", base(client), "--log-dir", str(tmp_path)])
    clock.stop_at = clock.t + 2
    rc = await fd.run(opts, CFG, base(client), None, source=source, transport=transport,
                      stop=clock.stop, time_fn=clock, wall_fn=clock.wall, sleep=clock.sleep,
                      install_signals=False)
    assert rc == 0 and source.stopped and "litter" in the_clock(client).apps


# ---- the weather and the idle show's isolation (D-0019) ----------------------------------


WEATHER = Weather(temp_f=52.5, code=0, is_day=True, high_f=61.9, low_f=38.9,
                  fetched_at=START - timedelta(minutes=5))


class FakeWeather:
    def __init__(self, reading: Weather | None = WEATHER) -> None:
        self.reading = reading
        self.raise_in_view = False
        self.configs = []
        self.runs = 0
        self.stopped = False

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


def record_states(monkeypatch, *, cue: Cue | None = None) -> list:
    """Record every mapped state; with ``cue``, pin it on ready states."""
    states = []

    def mapping(*a, **kw):
        if cue is not None:
            kw["cue"] = cue
        state = to_display_state(*a, **kw)
        states.append(state)
        return state

    monkeypatch.setattr(fd, "to_display_state", mapping)
    return states


def weather_feeder(client, clock, source, weather):
    transport = AwtrixNgTransport(base(client), session=client.session, time_fn=clock)
    return fd.Feeder(source, transport, CFG, clock_label=base(client), time_fn=clock,
                     wall_fn=clock.wall, sleep=clock.sleep, weather=weather)


async def test_weather_reaches_ready_and_only_ready(client, clock, monkeypatch):
    states = record_states(monkeypatch)
    source = FakeSource(clock, READY)
    weather = FakeWeather()
    clock.on_tick = lambda c: setattr(source, "snapshot", ROBOT_OFF) if c.t >= 1002 else None
    await run_for(weather_feeder(client, clock, source, weather), clock, 4)
    ready = [s for s in states if s.kind == "ready"]
    off = [s for s in states if s.kind == "robot_off"]
    assert ready and off
    assert all(s.weather == WEATHER for s in ready)
    assert all(s.weather is None for s in off)
    assert weather.runs == 1 and weather.stopped  # run beside the loop, stopped with it


async def test_no_weather_source_maps_none(client, clock, monkeypatch):
    states = record_states(monkeypatch)
    feeder = make_feeder(client, clock, FakeSource(clock, READY))
    assert feeder.weather is None
    await run_for(feeder, clock, 2)
    assert states and all(s.kind == "ready" and s.weather is None for s in states)
    assert [svc.name for svc in feeder._services()] == ["Whisker source"]


async def test_weather_view_failing_carries_on_without_weather(client, clock, monkeypatch,
                                                                 caplog):
    caplog.set_level(logging.INFO)
    states = record_states(monkeypatch)
    weather = FakeWeather()
    weather.raise_in_view = True
    clock.on_tick = lambda c: setattr(weather, "raise_in_view", c.t < 1003)
    feeder = weather_feeder(client, clock, FakeSource(clock, READY), weather)
    await run_for(feeder, clock, 5)
    assert feeder.pusher.pushes >= 1 and "litter" in the_clock(client).apps
    early = states[:8]
    assert early and all(s.kind == "ready" and s.weather is None for s in early)
    assert states[-1].weather == WEATHER  # back once it reads again
    assert caplog.text.count("could not read the weather") == 1
    assert caplog.text.count("weather readable again") == 1
    assert "could not map or draw" not in caplog.text


def failing_cue_render(monkeypatch) -> list:
    """render raises whenever a cue is set; returns the states it was asked to draw."""
    asked = []

    def broken(state, now, cfg, **kw):
        asked.append(state)
        if state.cue is not None:
            raise ValueError("a bug in the scene")
        return render(state, now, cfg, **kw)

    monkeypatch.setattr(fd, "render", broken)
    return asked


async def test_failing_cue_falls_back_to_the_home_page(client, clock, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="arthur")
    cue = Cue(kind="scene", at=START, length_s=36.0, name="rat")
    record_states(monkeypatch, cue=cue)
    asked = failing_cue_render(monkeypatch)
    feeder = make_feeder(client, clock, FakeSource(clock, READY))
    await run_for(feeder, clock, 3)
    c = the_clock(client)
    now = START + timedelta(seconds=c.last_push_at - 1000.0)  # when the shown frame was drawn
    pinned = to_display_state(READY, FeederHealth(last_contact_at=now), now, CFG, cue=cue)
    assert the_clock(client).screen() == render(replace(pinned, cue=None), now, CFG)
    assert any(s.cue is None for s in asked)
    errors = [r for r in caplog.records if "could not draw the idle show" in r.getMessage()]
    assert len(errors) == 1 and errors[0].levelno == logging.ERROR  # once for the cue
    assert "scene rat" in errors[0].getMessage()
    assert "could not map or draw" not in caplog.text


async def test_each_failing_cue_logs_once_and_repeats_at_debug(client, clock, monkeypatch,
                                                               caplog):
    caplog.set_level(logging.DEBUG, logger="arthur")
    cues = [Cue(kind="scene", at=START, length_s=36.0, n=k) for k in range(3)]
    current = [cues[0]]

    def mapping(*a, **kw):
        kw["cue"] = current[0]
        return to_display_state(*a, **kw)

    monkeypatch.setattr(fd, "to_display_state", mapping)
    failing_cue_render(monkeypatch)
    clock.on_tick = lambda c: current.__setitem__(0, cues[min(int(c.t - 1000), 2)])
    await run_for(make_feeder(client, clock, FakeSource(clock, READY)), clock, 4)
    msgs = [r for r in caplog.records if "could not draw the idle show" in r.getMessage()]
    assert [r.levelno for r in msgs] == [logging.ERROR, logging.DEBUG, logging.DEBUG]


async def test_render_failure_without_a_cue_pushes_nothing(client, clock, monkeypatch, caplog):
    def broken(state, now, cfg, **kw):
        raise ValueError("a bug in the home page")

    monkeypatch.setattr(fd, "render", broken)
    feeder = make_feeder(client, clock, FakeSource(clock, ROBOT_OFF))
    await run_for(feeder, clock, 3)
    assert feeder.pusher.pushes == 0 and the_clock(client).apps == {}
    assert caplog.text.count("could not map or draw") == 1


class DyingSource(FakeSource):
    """Its first run dies of a bug; later runs behave."""

    runs = 0

    async def run(self, stop):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError("source bug")
        await super().run(stop)


class DyingWeather(FakeWeather):
    async def run(self, stop):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError("weather bug")
        await stop.wait()
        self.stopped = True


async def test_a_dead_whisker_task_is_restarted(client, clock, caplog):
    caplog.set_level(logging.INFO)
    source = DyingSource(clock, READY)
    weather = FakeWeather()
    feeder = weather_feeder(client, clock, source, weather)
    runs_at = {}
    clock.on_tick = lambda c: runs_at.setdefault(int(c.t - 1000), source.runs)
    await run_for(feeder, clock, fd.SOURCE_RESTART_S + 3)
    assert runs_at[int(fd.SOURCE_RESTART_S) - 1] == 1  # not restarted before the delay
    assert source.runs == 2 and source.stopped
    assert weather.runs == 1  # the other service was left alone
    assert "the Whisker source stopped unexpectedly (RuntimeError: source bug)" in caplog.text
    assert caplog.text.count("stopped unexpectedly") == 1
    assert caplog.text.count("restarting the Whisker source") == 1


async def test_a_dead_weather_task_is_restarted(client, clock, caplog):
    caplog.set_level(logging.INFO)
    source = FakeSource(clock, READY)
    weather = DyingWeather()
    feeder = weather_feeder(client, clock, source, weather)
    seen = {}

    def note(c):
        seen.setdefault(int(c.t - 1000), (weather.runs, feeder.pusher.pushes))

    clock.on_tick = note
    await run_for(feeder, clock, fd.SOURCE_RESTART_S + 3)
    runs, pushes = seen[int(fd.SOURCE_RESTART_S) - 1]
    assert runs == 1 and pushes >= 1  # the display went on meanwhile
    assert "the weather source stopped unexpectedly (RuntimeError: weather bug)" in caplog.text
    assert weather.runs == 2 and weather.stopped and source.stopped
    assert caplog.text.count("restarting the weather source") == 1


async def test_config_reload_reaches_the_weather(client, clock, tmp_path):
    path = tmp_path / "display.toml"
    shutil.copy(DEFAULT_PATH, path)
    weather = FakeWeather()
    transport = AwtrixNgTransport(base(client), session=client.session, time_fn=clock)
    feeder = fd.Feeder(FakeSource(clock, READY), transport, CFG, config_path=path,
                       time_fn=clock, wall_fn=clock.wall, sleep=clock.sleep, weather=weather)
    stamp = path.stat().st_mtime_ns

    def edit(c):
        if c.t == 1002:
            path.write_text(path.read_text().replace("refresh_s = 900", "refresh_s = 600"))
            os.utime(path, ns=(stamp + 10**9, stamp + 10**9))

    clock.on_tick = edit
    await run_for(feeder, clock, 4)
    assert weather.configs and weather.configs[-1].weather.refresh_s == 600


def test_cli_builds_a_weather_source_from_the_config(monkeypatch):
    seen = {}

    async def fake_run(opts, cfg, base_url, credentials, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(fd, "run", fake_run)
    monkeypatch.setattr(fd, "setup_logging", lambda *a: None)
    assert fd.main(["--clock", "h"], environ={}) == 0
    assert isinstance(seen["weather"], WeatherSource)
    assert seen["weather"].cfg.weather == CFG.weather


def test_cli_without_a_weather_table_has_no_weather(monkeypatch, tmp_path):
    path = tmp_path / "display.toml"
    text = DEFAULT_PATH.read_text()
    start = text.index("[weather]")
    end = text.index("\n[", start + 1)
    path.write_text(text[:start] + text[end + 1:])
    seen = {}

    async def fake_run(opts, cfg, base_url, credentials, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(fd, "run", fake_run)
    monkeypatch.setattr(fd, "setup_logging", lambda *a: None)
    assert fd.main(["--clock", "h", "--config", str(path)], environ={}) == 0
    assert "weather" not in seen
