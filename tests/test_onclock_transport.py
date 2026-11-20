"""The clock's own transport (``arthur.onclock.awtrix.ClockTransport``) against the fake clock
over real HTTP: the expectations ``tests/test_transport.py`` sets for the Mac's transport,
case for case where they apply, then the kept connection, reconnecting and AWTRIX restarting.
Also the clock's pusher, against ``tests/test_pusher.py``'s expectations."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from test_transport import BODIES, LOG, _app, _apps_clock, _clock_app, _logging_app

from arthur import awtrix_api
from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.onclock.awtrix import ClockTransport, TransportError
from arthur.onclock.pusher import Pusher
from arthur.render import Frame, render
from arthur.sim.server import CLOCKS, create_app
from arthur.transports import AwtrixNgTransport

pytestmark = pytest.mark.usefixtures("loopback_only")

CFG = default_config()
SETTINGS_GET = "GET /api/v1/settings"
SETTINGS_PATCH = "PATCH /api/v1/settings"
PUSH = "PUT /api/v1/apps/pushed/litter"
APPS_GET = "GET /api/v1/apps"
ORDER_PUT = "PUT /api/v1/apps/order"


class FakeTime:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
async def client(aiohttp_client):
    return await aiohttp_client(_logging_app(create_app(start_tasks=False)))


def log(client) -> list[str]:
    return client.app[LOG]


def base(client) -> str:
    return str(client.make_url("")).rstrip("/")


def the_clock(client):
    (clock,) = client.app[CLOCKS].values()
    return clock


def transport(url, **kw) -> ClockTransport:
    kw.setdefault("time_fn", FakeTime())
    return ClockTransport(url, **kw)


def frame(value: int = 0) -> Frame:
    f = Frame(52, 16)
    f.pixel(0, 0, (value, 0, 0))
    return f


# ---- the Mac transport's expectations ----------------------------------------------------


async def test_pushed_frame_equals_screen(client):
    fx = FIXTURES["ready_laser_dirty"]
    f = render(fx.display_state(CFG), fx.now, CFG)
    t = transport(base(client))
    try:
        await t.show(f, 90)
    finally:
        await t.close()
    data = await (await client.get("/api/v1/display/screen")).json()
    assert Frame.from_ints(52, 16, data["pixels"]) == f
    clock = the_clock(client)
    assert clock.apps["litter"].lifetime_ms == 90_000
    assert clock.apps["litter"].expiry == "mark"
    assert not clock.enlarge_apps
    assert log(client)[:5] == [SETTINGS_GET, SETTINGS_PATCH, PUSH, APPS_GET, ORDER_PUT]


async def test_the_body_is_the_mac_transports_byte_for_byte():
    t = transport("http://127.0.0.1:9")
    for name in ("ready", "in_use", "cleaning", "drawer_full"):
        fx = FIXTURES[name]
        f = render(fx.display_state(CFG), fx.now, CFG)
        for ttl in (90, 30.5, 0.0004):
            req = bytes(t.push_request(f, ttl))
            head, _, body = req.partition(b"\r\n\r\n")
            mac = json.dumps(AwtrixNgTransport.payload(f, ttl), separators=(",", ":")).encode()
            assert body == mac
            assert f"Content-Length: {len(mac)}".encode() in head
            assert body.decode() == awtrix_api.payload_text(f.to_rgb888(), 52, 16, ttl)
    assert head.startswith(b"PUT /api/v1/apps/pushed/litter HTTP/1.1\r\n")
    assert b"Content-Type: application/json" in head


async def test_settings_checked_once_then_skipped(client):
    t = transport(base(client), only_app=False)
    await t.show(frame(1), 5)
    await t.show(frame(2), 5)
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, PUSH]
    assert t.conn.opened == 1  # one kept connection


async def test_no_patch_when_already_off(client):
    the_clock(client).enlarge_apps = False
    t = transport(base(client), only_app=False)
    await t.show(frame(), 5)
    assert log(client) == [SETTINGS_GET, PUSH]


async def test_recheck_after_settings_check_s(client):
    now = FakeTime()
    t = transport(base(client), settings_check_s=300, time_fn=now, only_app=False)
    await t.show(frame(), 5)
    the_clock(client).enlarge_apps = True
    now.t += 300
    await t.show(frame(), 5)
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, PUSH]
    now.t += 0.5
    await t.show(frame(), 5)
    assert log(client)[4:] == [SETTINGS_GET, SETTINGS_PATCH, PUSH]
    assert not the_clock(client).enlarge_apps


async def test_recheck_after_failed_request(client):
    t = transport(base(client), only_app=False)
    await t.show(frame(), 5)
    the_clock(client).enlarge_apps = True  # say the clock was reset
    t.app_name = "bad name"  # the clock answers 400
    t._push_key = None
    with pytest.raises(TransportError) as ei:
        await t.show(frame(), 5)
    assert ei.value.status == 400
    t.app_name = "litter"
    t._push_key = None
    await t.show(frame(1), 5)
    assert log(client)[-3:] == [SETTINGS_GET, SETTINGS_PATCH, PUSH]
    assert not the_clock(client).enlarge_apps


async def test_no_patch_when_clock_has_no_such_setting(aiohttp_client):
    c = await aiohttp_client(_clock_app(settings={"brightness": 100}))
    await transport(base(c), only_app=False).show(frame(), 5)
    assert c.app[LOG] == [SETTINGS_GET, PUSH]


async def test_failing_settings_request_raises_and_pushes_nothing(aiohttp_client):
    c = await aiohttp_client(_clock_app(settings_status=500))
    with pytest.raises(TransportError) as ei:
        await transport(base(c), only_app=False).show(frame(), 5)
    assert ei.value.status == 500
    assert c.app[LOG] == [SETTINGS_GET]


async def test_failing_patch_raises_and_pushes_nothing(client):
    the_clock(client).enlarge_apps = True
    t = transport(base(client), only_app=False)
    real = t._request

    async def refuse_patch(method, path, body_text=None):
        if method == "PATCH":
            raise TransportError("PATCH refused", 503)
        return await real(method, path, body_text)

    t._request = refuse_patch
    with pytest.raises(TransportError):
        await t.show(frame(), 5)
    assert log(client) == [SETTINGS_GET]
    assert the_clock(client).apps == {}


async def test_patch_that_leaves_it_on_is_refused(aiohttp_client):
    app = web.Application()

    async def settings(request):
        return web.json_response({"enlargeApps": True})

    app.router.add_route("*", "/api/v1/settings", settings)
    c = await aiohttp_client(app)
    with pytest.raises(TransportError, match="did not turn enlargeApps off"):
        await transport(base(c)).show(frame(), 5)


async def test_clear_removes_app(client):
    t = transport(base(client))
    await t.show(frame(1), 5)
    await t.clear()
    assert the_clock(client).apps == {}


async def test_refusal_surfaces_as_transport_error(client):
    with pytest.raises(TransportError) as ei:
        await transport(base(client), app_name="bad name").show(frame(), 5)
    assert ei.value.status == 400
    assert "invalidName" in (ei.value.body or "")


async def test_connection_error_is_transport_error():
    t = transport("http://127.0.0.1:9", timeout_s=2)
    with pytest.raises(TransportError) as ei:
        await t.show(frame(), 5)
    assert ei.value.status is None


def test_only_this_device():
    for url in ("http://192.168.86.41", "https://127.0.0.1", "http://clock.local"):
        with pytest.raises(ValueError):
            ClockTransport(url, time_fn=FakeTime())


# --- the rotation -----------------------------------------------------------------------


async def test_first_push_leaves_only_ours_in_rotation(client):
    clock = the_clock(client)
    assert clock.in_rotation() == ["Time", "Status"]
    await transport(base(client)).show(frame(), 5)
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, APPS_GET, ORDER_PUT]
    assert clock.in_rotation() == ["litter"]
    assert clock.order == ["litter"] and clock.disabled == ["Time", "Status"]


async def test_no_put_when_already_only_ours(client):
    await transport(base(client)).show(frame(), 5)
    log(client).clear()
    await transport(base(client)).show(frame(), 5)
    assert log(client) == [SETTINGS_GET, PUSH, APPS_GET]


async def test_rotation_rechecked_after_check_time_and_builtin_disabled_again(client):
    now = FakeTime()
    clock = the_clock(client)
    t = transport(base(client), settings_check_s=300, time_fn=now)
    await t.show(frame(), 5)
    clock.disabled.remove("Status")
    log(client).clear()
    now.t += 300
    await t.show(frame(), 5)
    assert log(client) == [PUSH]
    now.t += 0.5
    await t.show(frame(), 5)
    assert log(client)[1:] == [SETTINGS_GET, PUSH, APPS_GET, ORDER_PUT]
    assert clock.in_rotation() == ["litter"]


async def test_rotation_rechecked_after_failed_request(client):
    clock = the_clock(client)
    t = transport(base(client))
    await t.show(frame(), 5)
    clock.order, clock.disabled = [], []
    t.app_name = "bad name"
    t._push_key = None
    with pytest.raises(TransportError):
        await t.show(frame(), 5)
    t.app_name = "litter"
    t._push_key = None
    log(client).clear()
    await t.show(frame(), 5)
    assert log(client) == [SETTINGS_GET, PUSH, APPS_GET, ORDER_PUT]
    assert clock.in_rotation() == ["litter"]


async def test_only_app_false_leaves_rotation_alone(client):
    await transport(base(client), only_app=False).show(frame(), 5)
    assert APPS_GET not in log(client) and ORDER_PUT not in log(client)
    assert the_clock(client).in_rotation() == ["Time", "Status", "litter"]


async def test_order_body_keeps_off_apps_off_and_background_scripts_running(aiohttp_client):
    apps = [
        _app("litter", origin="pushed"),
        _app("Time"),
        _app("Status", enabled=False),
        _app("co2", in_loop=False, present=False, origin=None),
        _app("watch", in_loop=False, origin="script", headless=True),
        _app("menu", in_loop=False, origin="script", ondemand=True),
        {"name": "helpers", "origin": "module", "import": "helpers"},
    ]
    c = await aiohttp_client(_apps_clock(apps))
    await transport(base(c)).show(frame(), 5)
    assert c.app[BODIES] == [{"order": ["litter", "watch"],
                              "disabled": ["Time", "Status", "co2"]}]


async def test_ours_switched_off_is_switched_on(aiohttp_client):
    apps = [_app("litter", enabled=False, origin="pushed"), _app("Time", enabled=False)]
    c = await aiohttp_client(_apps_clock(apps))
    await transport(base(c)).show(frame(), 5)
    assert c.app[BODIES] == [{"order": ["litter"], "disabled": ["Time"]}]


async def test_failing_order_put_raises_and_is_retried_next_show(aiohttp_client):
    c = await aiohttp_client(_apps_clock([_app("litter"), _app("Time")], order_status=507))
    t = transport(base(c))
    with pytest.raises(TransportError) as ei:
        await t.show(frame(), 5)
    assert ei.value.status == 507
    with pytest.raises(TransportError):
        await t.show(frame(), 5)
    assert c.app[LOG].count(ORDER_PUT) == 2
    assert c.app[LOG].count(SETTINGS_GET) == 2


async def test_apps_reply_of_wrong_shape_is_transport_error(aiohttp_client):
    c = await aiohttp_client(_apps_clock({"apps": []}))  # type: ignore[arg-type]
    with pytest.raises(TransportError) as ei:
        await transport(base(c)).show(frame(), 5)
    assert "not an array of apps" in str(ei.value)


def test_rotation_fix_is_the_mac_transports():
    mac = AwtrixNgTransport("http://x")
    cases = [
        [_app("litter"), _app("Time"), _app("Status")],
        [_app("litter", enabled=False)],
        [_app("litter"), _app("Time", enabled=False, in_loop=False)],
        [_app("litter"), _app("w", origin="script", headless=True, in_loop=False),
         _app("w", origin="script", headless=True, in_loop=False), _app("Time")],
        [],
    ]
    for apps in cases:
        fix = awtrix_api.rotation_fix(apps, "litter")
        assert fix == mac.rotation_fix(apps)
        if fix is not None:
            assert awtrix_api.order_text(fix) == json.dumps(fix, separators=(",", ":"))


# ---- the kept connection --------------------------------------------------------------------


class Closer:
    """A middleware that answers ``Connection: close`` on chosen requests."""

    def __init__(self) -> None:
        self.close_on: set[int] = set()
        self.n = 0

    @web.middleware
    async def mw(self, request, handler):
        self.n += 1
        resp = await handler(request)
        if self.n in self.close_on:
            resp.force_close()
        return resp


async def test_a_server_close_reopens_and_rechecks(aiohttp_client):
    closer = Closer()
    app = _logging_app(create_app(start_tasks=False))
    app.middlewares.append(closer.mw)
    c = await aiohttp_client(app)
    t = transport(base(c))
    await t.show(frame(1), 5)  # 5 requests
    closer.close_on = {7}  # the second push of the next three
    for v in (2, 3, 4):
        await t.show(frame(v), 5)
    assert t.conn.opened == 2 and t.conn.server_closed == 1
    # A new connection after the first: both checks again before the next push.
    assert c.app[LOG][5:] == [PUSH, PUSH, SETTINGS_GET, PUSH, APPS_GET]


class IdleDropper:
    """A bare HTTP/1.1 server that keeps a connection while requests keep coming and drops it
    silently after ``idle_s`` without one, as AWTRIX does after 5 s."""

    def __init__(self, idle_s: float = 0.1) -> None:
        self.idle_s = idle_s
        self.log: list[str] = []
        self.connections = 0

    async def handle(self, reader, writer):
        self.connections += 1
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), self.idle_s)
                except TimeoutError:
                    return  # idle: drop it without a word
                if not line:
                    return
                method, path, _ = line.decode().split(" ", 2)
                length = 0
                while (h := await reader.readline()) not in (b"\r\n", b""):
                    name, _, value = h.decode().partition(":")
                    if name.lower() == "content-length":
                        length = int(value)
                if length:
                    await reader.readexactly(length)
                self.log.append(f"{method} {path}")
                body = {"/api/v1/settings": b'{"enlargeApps":false}',
                        "/api/v1/apps": b'[{"name":"litter","enabled":true,"inLoop":true}]'
                        }.get(path, b'{"ok":true}')
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
        finally:
            writer.close()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]


async def test_a_kept_connection_found_closed_is_tried_once_more():
    srv = IdleDropper()
    port = await srv.start()
    t = transport(f"http://127.0.0.1:{port}")
    await t.show(frame(1), 5)
    assert srv.log == [SETTINGS_GET, PUSH, APPS_GET] and srv.connections == 1
    await asyncio.sleep(0.3)  # the server drops the idle connection; we do not know yet
    await t.show(frame(2), 5)
    assert t.stale_retries == 1 and t.conn.opened == 2
    # The push found the connection gone; the show ran again on a new one, checks first.
    assert srv.log[3:] == [SETTINGS_GET, PUSH, APPS_GET]
    await t.close()
    srv.server.close()


async def test_awtrix_restarting_loses_our_app_and_it_comes_back(aiohttp_server, aiohttp_client):
    """A restarted AWTRIX (here: a fresh fake clock on the same port, which has also lost
    its settings and rotation) drops our connection; the next show reconnects, checks both
    again and pushes, which recreates the app."""
    server = await aiohttp_server(create_app(start_tasks=False))
    port = server.port
    t = transport(f"http://127.0.0.1:{port}")
    await t.show(frame(1), 5)
    await server.close()
    with pytest.raises(TransportError):
        await t.show(frame(2), 5)  # AWTRIX is down
    server2 = await aiohttp_server(create_app(start_tasks=False), port=port)
    fresh = server2.app[CLOCKS]["52x16"]
    assert fresh.apps == {} and fresh.enlarge_apps
    await t.show(frame(3), 5)
    assert "litter" in fresh.apps and not fresh.enlarge_apps
    assert fresh.in_rotation() == ["litter"]
    assert fresh.screen() == frame(3)
    await t.close()
    await server2.close()


async def test_restart_between_pushes_is_seen_as_a_stale_connection(aiohttp_server):
    server = await aiohttp_server(create_app(start_tasks=False))
    port = server.port
    t = transport(f"http://127.0.0.1:{port}")
    await t.show(frame(1), 5)
    await server.close()
    server2 = await aiohttp_server(create_app(start_tasks=False), port=port)
    fresh = server2.app[CLOCKS]["52x16"]
    await t.show(frame(2), 5)  # no error: retried on a new connection, with the checks
    assert t.stale_retries == 1
    assert fresh.screen() == frame(2) and not fresh.enlarge_apps
    assert fresh.in_rotation() == ["litter"]
    await t.close()
    await server2.close()


async def test_check_waits_for_nothing_and_turns_double_size_off(client):
    t = transport(base(client))
    await t.check()
    await t.check()
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, SETTINGS_GET]


# ---- the pusher (tests/test_pusher.py's expectations) ---------------------------------------


class FakeTransport:
    def __init__(self) -> None:
        self.shown: list[tuple[bytes, float]] = []
        self.down = False

    async def show(self, f, ttl_s):
        if self.down:
            raise TransportError("PUT http://clock/api/v1/apps/pushed/litter timed out")
        self.shown.append((f.to_rgb888(), ttl_s))


async def test_pushes_on_change_and_heartbeat():
    t, tr = FakeTime(50.0), FakeTransport()
    p = Pusher(tr, time_fn=t)
    assert p.reachable is None
    r = await p.offer(frame(1), 90, 30)
    assert r.reason == "new frame" and r.ok and p.reachable is True and p.pushes == 1
    t.t += 10
    assert await p.offer(frame(1), 90, 30) is None
    r = await p.offer(frame(2), 90, 30)
    assert r.reason == "new frame"
    t.t += 29.9
    assert await p.offer(frame(2), 90, 30) is None
    t.t += 0.1
    assert (await p.offer(frame(2), 90, 30)).reason == "heartbeat"
    assert [ttl for _, ttl in tr.shown] == [90, 90, 90]


async def test_failure_waits_a_second_then_retries_and_recovers():
    t, tr = FakeTime(50.0), FakeTransport()
    p = Pusher(tr, time_fn=t)
    await p.offer(frame(1), 90, 30)
    tr.down = True
    r = await p.offer(frame(2), 90, 30)
    assert not r.ok and isinstance(r.error, TransportError)
    assert p.reachable is False and "timed out" in p.last_error and p.failures == 1
    t.t += 0.5
    assert await p.offer(frame(2), 90, 30) is None
    t.t += 0.5
    assert not (await p.offer(frame(2), 90, 30)).ok
    tr.down = False
    t.t += 1
    r = await p.offer(frame(2), 90, 30)
    assert r.ok and r.reason == "new frame" and p.reachable is True
    assert p.pushes == 2 and p.failures == 2
    assert p.last_error is not None


async def test_forget_pushes_the_same_frame_again():
    t, tr = FakeTime(50.0), FakeTransport()
    p = Pusher(tr, time_fn=t)
    await p.offer(frame(1), 90, 30)
    p.forget()
    assert (await p.offer(frame(1), 90, 30)).reason == "new frame"
