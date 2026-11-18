"""AwtrixNgTransport against the fake clock over real HTTP."""

import pytest
from aiohttp import web

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.render import Frame, render
from arthur.sim.server import CLOCKS, create_app
from arthur.transports import AwtrixNgTransport, DisplayTransport, TransportError

CFG = default_config()
LOG = web.AppKey("log", list)
BODIES = web.AppKey("bodies", list)


class FakeTime:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _logging_app(app: web.Application) -> web.Application:
    """Record every request as ``"METHOD /path"`` in ``app["log"]``."""
    log: list[str] = []

    @web.middleware
    async def record(request, handler):
        log.append(f"{request.method} {request.path}")
        return await handler(request)

    app.middlewares.append(record)
    app[LOG] = log
    return app


@pytest.fixture
async def client(aiohttp_client):
    return await aiohttp_client(_logging_app(create_app(start_tasks=False)))


def log(client) -> list[str]:
    return client.app[LOG]


SETTINGS_GET = "GET /api/v1/settings"
SETTINGS_PATCH = "PATCH /api/v1/settings"
PUSH = "PUT /api/v1/apps/pushed/litter"
APPS_GET = "GET /api/v1/apps"
ORDER_PUT = "PUT /api/v1/apps/order"


def base(client) -> str:
    return str(client.make_url("")).rstrip("/")


def the_clock(client):
    (clock,) = client.app[CLOCKS].values()
    return clock


async def test_pushed_frame_equals_screen(client):
    fx = FIXTURES["ready_laser_dirty"]
    frame = render(fx.display_state(CFG), fx.now, CFG)
    assert frame.size == (52, 16)
    t = AwtrixNgTransport(base(client))
    assert isinstance(t, DisplayTransport)
    try:
        await t.show(frame, 90)
    finally:
        await t.close()
    resp = await client.get("/api/v1/display/screen")
    data = await resp.json()
    assert (data["width"], data["height"]) == (52, 16)
    assert Frame.from_ints(52, 16, data["pixels"]) == frame
    clock = the_clock(client)
    assert clock.apps["litter"].lifetime_ms == 90_000
    assert clock.apps["litter"].expiry == "mark"
    # The fake clock starts with enlargeApps on, like a new TC002; the first show turned it off.
    assert not clock.enlarge_apps
    assert log(client)[:5] == [SETTINGS_GET, SETTINGS_PATCH, PUSH, APPS_GET, ORDER_PUT]


async def test_settings_checked_once_then_skipped(client):
    t = AwtrixNgTransport(base(client), session=client.session, time_fn=FakeTime(),
                          only_app=False)
    await t.show(Frame(52, 16, (1, 2, 3)), 5)
    await t.show(Frame(52, 16, (4, 5, 6)), 5)
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, PUSH]


async def test_no_patch_when_already_off(client):
    the_clock(client).enlarge_apps = False
    t = AwtrixNgTransport(base(client), session=client.session, only_app=False)
    await t.show(Frame(52, 16), 5)
    assert log(client) == [SETTINGS_GET, PUSH]


async def test_recheck_after_settings_check_s(client):
    now = FakeTime()
    t = AwtrixNgTransport(base(client), session=client.session, settings_check_s=300,
                          time_fn=now, only_app=False)
    await t.show(Frame(52, 16), 5)
    the_clock(client).enlarge_apps = True  # flipped in the clock's web UI
    now.t += 300
    await t.show(Frame(52, 16), 5)  # not yet due
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, PUSH]
    now.t += 0.5
    await t.show(Frame(52, 16), 5)
    assert log(client)[4:] == [SETTINGS_GET, SETTINGS_PATCH, PUSH]
    assert not the_clock(client).enlarge_apps


async def test_recheck_after_failed_request(client):
    t = AwtrixNgTransport(base(client), session=client.session, time_fn=FakeTime(),
                          only_app=False)
    await t.show(Frame(52, 16), 5)
    the_clock(client).enlarge_apps = True  # say the clock was reset
    class Short:  # claims 52x16 but carries too few bytes: the clock answers 422
        width, height = 52, 16

        def to_rgb888(self):
            return bytes(30)

    with pytest.raises(TransportError) as ei:
        await t.show(Short(), 5)
    assert ei.value.status == 422
    await t.show(Frame(52, 16, (1, 1, 1)), 5)
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, PUSH,
                           SETTINGS_GET, SETTINGS_PATCH, PUSH]
    assert not the_clock(client).enlarge_apps


def _clock_app(settings_status: int = 200, settings: dict | None = None) -> web.Application:
    """A minimal stand-in clock whose settings route can fail or lack ``enlargeApps``."""
    app = web.Application()
    app[LOG] = []

    async def h_settings(request):
        app[LOG].append(f"{request.method} {request.path}")
        if settings_status != 200:
            return web.json_response({"error": {"code": "internal", "message": "boom"}},
                                     status=settings_status)
        return web.json_response(settings or {})

    async def h_push(request):
        app[LOG].append(f"{request.method} {request.path}")
        return web.json_response({"ok": True})

    app.router.add_route("*", "/api/v1/settings", h_settings)
    app.router.add_put("/api/v1/apps/pushed/{name}", h_push)
    return app


async def test_no_patch_when_clock_has_no_such_setting(aiohttp_client):
    c = await aiohttp_client(_clock_app(settings={"brightness": 100}))
    t = AwtrixNgTransport(base(c), session=c.session, only_app=False)
    await t.show(Frame(52, 16), 5)
    assert c.app[LOG] == [SETTINGS_GET, PUSH]


async def test_failing_settings_request_raises_and_pushes_nothing(aiohttp_client):
    c = await aiohttp_client(_clock_app(settings_status=500))
    t = AwtrixNgTransport(base(c), session=c.session, only_app=False)
    with pytest.raises(TransportError) as ei:
        await t.show(Frame(52, 16), 5)
    assert ei.value.status == 500
    assert c.app[LOG] == [SETTINGS_GET]


async def test_failing_patch_raises_and_pushes_nothing(client):
    the_clock(client).enlarge_apps = True
    t = AwtrixNgTransport(base(client), session=client.session, only_app=False)
    t_patch = t._request

    async def refuse_patch(method, path, body=None):
        if method == "PATCH":
            raise TransportError("PATCH refused", 503)
        return await t_patch(method, path, body)

    t._request = refuse_patch  # type: ignore[method-assign]
    with pytest.raises(TransportError):
        await t.show(Frame(52, 16), 5)
    assert log(client) == [SETTINGS_GET]
    assert the_clock(client).apps == {}


async def test_clear_removes_app(client):
    t = AwtrixNgTransport(base(client), session=client.session)
    await t.show(Frame(52, 16, (1, 1, 1)), 5)
    await t.clear()
    assert the_clock(client).apps == {}
    await t.close()  # does not close a session it does not own
    assert not client.session.closed


async def test_refusal_surfaces_as_transport_error(client):
    with pytest.raises(TransportError) as ei:
        await AwtrixNgTransport(base(client), app_name="bad name",
                                session=client.session).show(Frame(52, 16), 5)
    assert ei.value.status == 400
    assert "invalidName" in (ei.value.body or "")


async def test_body_over_limit_is_transport_error(client):
    class Huge:
        # 1,728,000 bytes RGB888 → about 2.3 MB after base64: over the TC002's 2 MiB limit,
        # under the server's own 3 MiB cap, so the clock is the one that refuses it.
        width, height = 800, 720

        def to_rgb888(self):
            return bytes(800 * 720 * 3)

    t = AwtrixNgTransport(base(client), session=client.session)
    with pytest.raises(TransportError) as ei:
        await t.show(Huge(), 5)
    assert ei.value.status == 413
    assert "payloadTooLarge" in ei.value.body


async def test_connection_error_is_transport_error():
    t = AwtrixNgTransport("http://127.0.0.1:9", timeout_s=2)
    try:
        with pytest.raises(TransportError) as ei:
            await t.show(Frame(52, 16), 5)
        assert ei.value.status is None
    finally:
        await t.close()


def test_payload_shape():
    f = Frame(52, 16)
    p = AwtrixNgTransport.payload(f, 90)
    assert set(p) == {"draw", "lifetimeMs", "lifetimeExpiry"}
    assert p["lifetimeMs"] == 90_000 and p["lifetimeExpiry"] == "mark"
    cmd = p["draw"][0]
    assert cmd[:5] == ["bitmap", 0, 0, 52, 16] and len(cmd[5]) == 3328


# --- the rotation: ours is the only app ---------------------------------------------------


async def test_first_push_leaves_only_ours_in_rotation(client):
    clock = the_clock(client)
    assert clock.in_rotation() == ["Time", "Status"]  # a fresh TC002
    t = AwtrixNgTransport(base(client), session=client.session, time_fn=FakeTime())
    await t.show(Frame(52, 16), 5)
    assert log(client) == [SETTINGS_GET, SETTINGS_PATCH, PUSH, APPS_GET, ORDER_PUT]
    assert clock.in_rotation() == ["litter"]
    assert clock.order == ["litter"] and clock.disabled == ["Time", "Status"]


async def test_no_put_when_already_only_ours(client):
    t = AwtrixNgTransport(base(client), session=client.session, time_fn=FakeTime())
    await t.show(Frame(52, 16), 5)
    log(client).clear()
    t2 = AwtrixNgTransport(base(client), session=client.session, time_fn=FakeTime())
    await t2.show(Frame(52, 16), 5)  # a fresh transport checks, and finds nothing to do
    assert log(client) == [SETTINGS_GET, PUSH, APPS_GET]


async def test_rotation_rechecked_after_check_time_and_builtin_disabled_again(client):
    now = FakeTime()
    clock = the_clock(client)
    t = AwtrixNgTransport(base(client), session=client.session, settings_check_s=300,
                          time_fn=now)
    await t.show(Frame(52, 16), 5)
    clock.disabled.remove("Status")  # it turned itself back on (NG issue #142)
    assert clock.in_rotation() == ["litter", "Status"]
    log(client).clear()
    now.t += 300
    await t.show(Frame(52, 16), 5)  # not yet due
    assert log(client) == [PUSH]
    now.t += 0.5
    await t.show(Frame(52, 16), 5)
    assert log(client)[1:] == [SETTINGS_GET, PUSH, APPS_GET, ORDER_PUT]
    assert clock.in_rotation() == ["litter"]


async def test_rotation_rechecked_after_failed_request(client):
    clock = the_clock(client)
    t = AwtrixNgTransport(base(client), session=client.session, time_fn=FakeTime())
    await t.show(Frame(52, 16), 5)
    clock.order, clock.disabled = [], []  # say someone reset the rotation in the web UI
    t.app_name = "bad name"
    with pytest.raises(TransportError):
        await t.show(Frame(52, 16), 5)  # the push fails (400): both checks are forgotten
    t.app_name = "litter"
    log(client).clear()
    await t.show(Frame(52, 16), 5)
    assert log(client) == [SETTINGS_GET, PUSH, APPS_GET, ORDER_PUT]
    assert clock.in_rotation() == ["litter"]


async def test_only_app_false_leaves_rotation_alone(client):
    t = AwtrixNgTransport(base(client), session=client.session, only_app=False)
    await t.show(Frame(52, 16), 5)
    assert APPS_GET not in log(client) and ORDER_PUT not in log(client)
    assert the_clock(client).in_rotation() == ["Time", "Status", "litter"]


def _apps_clock(apps: list, order_status: int = 200) -> web.Application:
    """A stand-in clock with settings already right and a fixed ``GET /api/v1/apps``."""
    app = web.Application()
    app[LOG] = []
    bodies: list = []
    app[BODIES] = bodies

    async def handle(request):
        app[LOG].append(f"{request.method} {request.path}")
        if request.path == "/api/v1/settings":
            return web.json_response({"enlargeApps": False})
        if request.path == "/api/v1/apps":
            return web.json_response(apps)
        if request.path == "/api/v1/apps/order":
            bodies.append(await request.json())
            if order_status != 200:
                return web.json_response(
                    {"error": {"code": "insufficientStorage",
                               "message": "applied, not saved yet"}}, status=order_status)
        return web.json_response({"ok": True})

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def _app(name, enabled=True, in_loop=None, **extra):
    return {"name": name, "enabled": enabled,
            "inLoop": enabled if in_loop is None else in_loop, "slot": None,
            "present": True, "origin": "builtin", **extra}


async def test_order_body_keeps_off_apps_off_and_background_scripts_running(aiohttp_client):
    apps = [
        _app("litter", origin="pushed"),
        _app("Time"),
        _app("Status", enabled=False),  # already off: must stay in `disabled`
        _app("co2", in_loop=False, present=False, origin=None),  # holds a place only
        _app("watch", in_loop=False, origin="script", headless=True),
        _app("menu", in_loop=False, origin="script", ondemand=True),
        {"name": "helpers", "origin": "module", "import": "helpers"},
    ]
    c = await aiohttp_client(_apps_clock(apps))
    await AwtrixNgTransport(base(c), session=c.session).show(Frame(52, 16), 5)
    assert c.app[BODIES] == [{"order": ["litter", "watch"],
                                "disabled": ["Time", "Status", "co2"]}]


async def test_ours_switched_off_is_switched_on(aiohttp_client):
    apps = [_app("litter", enabled=False, origin="pushed"), _app("Time", enabled=False)]
    c = await aiohttp_client(_apps_clock(apps))
    await AwtrixNgTransport(base(c), session=c.session).show(Frame(52, 16), 5)
    assert c.app[BODIES] == [{"order": ["litter"], "disabled": ["Time"]}]


async def test_failing_order_put_raises_and_is_retried_next_show(aiohttp_client):
    c = await aiohttp_client(_apps_clock([_app("litter"), _app("Time")], order_status=507))
    t = AwtrixNgTransport(base(c), session=c.session, time_fn=FakeTime())
    with pytest.raises(TransportError) as ei:
        await t.show(Frame(52, 16), 5)
    assert ei.value.status == 507
    with pytest.raises(TransportError):
        await t.show(Frame(52, 16), 5)  # no retry inside a show; the next show checks again
    assert c.app[LOG].count(ORDER_PUT) == 2
    assert c.app[LOG].count(SETTINGS_GET) == 2  # the failure also forgot the settings check


async def test_apps_reply_of_wrong_shape_is_transport_error(aiohttp_client):
    c = await aiohttp_client(_apps_clock({"apps": []}))  # type: ignore[arg-type]
    with pytest.raises(TransportError) as ei:
        await AwtrixNgTransport(base(c), session=c.session).show(Frame(52, 16), 5)
    assert "not an array of apps" in str(ei.value)


async def test_fake_clock_rotation_routes(client):
    r = await client.get("/api/v1/apps")
    assert r.status == 200 and [a["name"] for a in await r.json()] == ["Time", "Status"]
    r = await client.get("/api/v1/apps/order")  # as on the real clock
    assert r.status == 405 and (await r.json())["error"]["code"] == "methodNotAllowed"
    r = await client.put("/api/v1/apps/order", json={"order": ["litter"]})
    assert r.status == 400 and (await r.json())["error"]["code"] == "invalidJson"
    r = await client.put("/api/v1/apps/order",
                         json={"order": ["litter"], "disabled": ["Time", "Status"]})
    assert r.status == 200 and await r.json() == {"ok": True}
    st = await (await client.get("/studio/state")).json()
    assert st["clocks"]["52x16"]["rotation"] == []  # litter holds a place, not pushed yet
    assert st["clocks"]["52x16"]["disabled"] == ["Time", "Status"]
    r = await client.post("/api/v1/apps")
    assert r.status == 405
