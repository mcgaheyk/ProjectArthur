"""The feeder end to end against the fake clock, with a fake source and fake time."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from arthur import feeder as fd
from arthur.config import DEFAULT_PATH, default_config
from arthur.mapping import to_display_state
from arthur.model import FeederHealth, RobotSnapshot
from arthur.render import Frame, overlay_stale, render
from arthur.sim.server import CLOCKS, create_app
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
