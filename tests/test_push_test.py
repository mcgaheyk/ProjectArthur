"""scripts/push_test.py (arthur/pushtest.py) against the fake clock over real HTTP."""

import io
import itertools
import socket
from datetime import timedelta

import pytest
from aiohttp import web

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.pushtest import READBACK_SETTLE_S, build_parser, clock_url, compare, guarded, main
from arthur.render import Frame, render
from arthur.sim.server import CLOCKS, create_app
from arthur.timebase import SHOW_FPS
from arthur.transports import AwtrixNgTransport, TransportError

CFG = default_config()


class FakeTime:
    """A clock that only moves when the script sleeps."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


@pytest.fixture
async def client(aiohttp_client):
    return await aiohttp_client(create_app(start_tasks=False))


def base(client) -> str:
    return str(client.make_url("")).rstrip("/")


def the_clock(client):
    (clock,) = client.app[CLOCKS].values()
    return clock


def opts(*argv: str):
    return build_parser().parse_args(list(argv))


async def push(client_or_url, *argv: str, fake: FakeTime | None = None):
    url = client_or_url if isinstance(client_or_url, str) else base(client_or_url)
    out, err = io.StringIO(), io.StringIO()
    fake = fake or FakeTime()
    rc = await guarded(opts("--clock", url, *argv), CFG, out=out, err=err,
                       time_fn=fake, sleep=fake.sleep)
    return rc, out.getvalue(), err.getvalue()


def fixture_frame(name: str) -> Frame:
    fx = FIXTURES[name]
    return render(fx.display_state(CFG), fx.now, CFG)


async def test_one_frame_lands_on_the_clock(client):
    rc, out, err = await push(client, "ready_laser_dirty", "--seconds", "0")
    assert rc == 0, err
    clock = the_clock(client)
    assert not clock.enlarge_apps  # the transport turned it off
    app = clock.apps["litter"]
    assert app.expiry == "mark" and app.lifetime_ms == round(CFG.timings.ttl_s * 1000)
    resp = await client.get("/api/v1/display/screen")
    data = await resp.json()
    assert Frame.from_ints(52, 16, data["pixels"]) == fixture_frame("ready_laser_dirty")
    assert "push 1 at +0.0s (new frame)" in out
    assert "stays up" in out


async def test_readback_agrees_with_the_sim(client):
    rc, out, err = await push(client, "drawer_full", "--seconds", "0", "--readback")
    assert rc == 0, err
    assert "sent 52x16 (832 px), got 52x16 (832 px)" in out
    assert "0 of 832 pixels differ" in out


async def test_animates_and_pushes_only_on_change(client):
    fake = FakeTime()
    rc, out, _ = await push(client, "ready", "--seconds", "20", fake=fake)
    assert rc == 0
    fx = FIXTURES["ready"]
    state = fx.display_state(CFG)
    # Count distinct consecutive frames over the same 20 s, at the script's beat (fx.now is
    # on a whole second, so the beats are every 1/SHOW_FPS s from the start).
    frames, t = [], 0.0
    while t <= 20:
        frames.append(render(state, fx.now + timedelta(seconds=t), CFG).to_rgb888())
        t += 1 / SHOW_FPS
    changes = 1 + sum(a != b for a, b in itertools.pairwise(frames))
    assert 1 < changes < len(frames)
    assert out.count("(new frame)") == changes
    assert "(heartbeat)" not in out  # 20 s is shorter than the 30 s heartbeat


async def test_plays_a_show_fixture_at_eight_frames_a_second_and_loops(client):
    fx = FIXTURES["idle_rat"]
    seconds = fx.loop_s + 4  # past the end of the slot, into the second time round
    rc, out, _ = await push(client, "idle_rat", "--seconds", f"{seconds:g}", fake=FakeTime())
    assert rc == 0
    assert f"looping every {fx.loop_s:g}s" in out
    state = fx.display_state(CFG)
    frames = [render(state, fx.time_at(k / SHOW_FPS), CFG).to_rgb888()
              for k in range(int(seconds * SHOW_FPS) + 1)]
    changes = 1 + sum(a != b for a, b in itertools.pairwise(frames))
    assert changes > 2 * seconds  # the walks and wipes change every eighth of a second
    assert out.count("(new frame)") == changes
    # The second time round starts with the scene's first frame again.
    assert frames[int(fx.loop_s * SHOW_FPS)] == frames[0]


class RecordingTime(FakeTime):
    def __init__(self) -> None:
        super().__init__()
        self.wakes: list[tuple[float, float]] = []  # (slept, woke at)

    async def sleep(self, s: float) -> None:
        self.t += s
        self.wakes.append((s, self.t))


async def test_ticks_on_the_beat_and_read_back_drops_beats(client):
    fake = RecordingTime()
    rc, _out, _ = await push(client, "ready", "--seconds", "3", fake=fake)
    assert rc == 0
    assert [s for s, _ in fake.wakes] == [1 / SHOW_FPS] * (3 * SHOW_FPS)

    fake = RecordingTime()
    rc, _out, _ = await push(client, "ready", "--seconds", "3", "--readback", fake=fake)
    assert rc == 0
    beats = [t for s, t in fake.wakes if s != READBACK_SETTLE_S]
    # Every tick wakes on an eighth of the fixture's clock, even after a read-back's 0.2 s.
    assert all((t - 1000) * SHOW_FPS == round((t - 1000) * SHOW_FPS) for t in beats)
    assert any(s == READBACK_SETTLE_S for s, _ in fake.wakes)


async def test_still_screen_gets_heartbeats(client):
    rc, out, _ = await push(client, "robot_off", "--seconds", "65", "--ttl", "90")
    assert rc == 0
    assert out.count("(new frame)") == 1
    assert out.count("(heartbeat)") == 2  # at 30 s and 60 s


async def test_clear_removes_the_app(client):
    rc, out, _ = await push(client, "ready", "--seconds", "0", "--clear")
    assert rc == 0
    assert the_clock(client).apps == {}
    assert "cleared app litter" in out


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_unreachable_clock_is_one_line():
    rc, _, err = await push(f"http://127.0.0.1:{_closed_port()}", "--seconds", "0")
    assert rc == 1
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("push_test: error: GET http://127.0.0.1:")
    assert "/api/v1/settings" in lines[0] and "ClientConnectorError" in lines[0]


async def test_non_2xx_shows_status_and_body(aiohttp_client):
    app = web.Application()

    async def refuse(request):
        return web.json_response({"error": {"code": "internal", "message": "boom"}}, status=500)

    app.router.add_route("*", "/api/v1/settings", refuse)
    c = await aiohttp_client(app)
    rc, _, err = await push(c, "ready", "--seconds", "0")
    assert rc == 1
    (line,) = err.strip().splitlines()
    assert "(HTTP 500)" in line and '"message": "boom"' in line


def test_compare_lists_differences():
    sent = Frame(52, 16)
    got = sent.to_ints()
    got[52 * 2 + 3] = 0xFF0000
    lines = compare(sent, {"width": 52, "height": 16, "pixels": got})
    assert "1 of 832 pixels differ" in lines[1]
    assert "(3, 2, #000000, #FF0000)" in lines[2]
    short = compare(sent, {"width": 32, "height": 8, "pixels": [0] * 256})
    assert "sizes differ" in short[1]


async def test_read_screen_rejects_a_wrong_shape(aiohttp_client):
    app = web.Application()
    async def wrong(request):
        return web.json_response({"w": 1})

    app.router.add_get("/api/v1/display/screen", wrong)
    c = await aiohttp_client(app)
    t = AwtrixNgTransport(base(c), session=c.session)
    with pytest.raises(TransportError):
        await t.read_screen()


def test_cli_list_and_bad_names(capsys):
    assert main(["--list"]) == 0
    assert "fixtures:" in capsys.readouterr().out
    assert main(["no_such", "--clock", "http://127.0.0.1:9"]) == 2
    assert "try --list" in capsys.readouterr().err
    assert main(["ready"]) == 2
    assert "--clock" in capsys.readouterr().err


def test_cli_unreachable_exit_status(capsys):
    assert main(["--clock", f"127.0.0.1:{_closed_port()}", "--seconds", "0"]) == 1
    (line,) = capsys.readouterr().err.strip().splitlines()
    assert line.startswith("push_test: error:")


def test_clock_url():
    assert clock_url("192.168.1.50") == "http://192.168.1.50"
    assert clock_url("http://clock.local/") == "http://clock.local"
