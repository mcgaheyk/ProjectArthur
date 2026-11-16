"""AwtrixNgTransport against the fake clock over real HTTP."""

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES
from arthur.mapping import to_display_state
from arthur.render import Frame, render
from arthur.sim.server import CLOCKS, create_app
from arthur.transports import AwtrixNgTransport, DisplayTransport, TransportError

CFG = default_config()


@pytest.fixture
async def client(aiohttp_client):
    return await aiohttp_client(create_app(start_tasks=False))


def base(client, size=None) -> str:
    root = str(client.make_url("")).rstrip("/")
    return f"{root}/clock/{size}" if size else root


@pytest.mark.parametrize("size", [(32, 8), (52, 16)])
async def test_pushed_frame_equals_screen(client, size):
    fx = FIXTURES["ready_badges"]
    frame = render(to_display_state(fx.snapshot, fx.health, fx.now, CFG), fx.now, CFG, size)
    sk = f"{size[0]}x{size[1]}"
    t = AwtrixNgTransport(base(client, sk))
    assert isinstance(t, DisplayTransport)
    try:
        await t.show(frame, 90)
    finally:
        await t.close()
    resp = await client.get(f"/clock/{sk}/api/v1/display/screen")
    data = await resp.json()
    assert (data["width"], data["height"]) == size
    assert Frame.from_ints(size[0], size[1], data["pixels"]) == frame
    clock = client.app[CLOCKS][sk]
    assert clock.apps["litter"].lifetime_ms == 90_000
    assert clock.apps["litter"].expiry == "mark"


async def test_clear_removes_app(client):
    t = AwtrixNgTransport(base(client, "32x8"), session=client.session)
    await t.show(Frame(32, 8, (1, 1, 1)), 5)
    await t.clear()
    assert client.app[CLOCKS]["32x8"].apps == {}
    await t.close()  # does not close a session it does not own
    assert not client.session.closed


async def test_refusal_surfaces_as_transport_error(client):
    with pytest.raises(TransportError) as ei:
        await AwtrixNgTransport(base(client, "32x8"), app_name="bad name",
                                session=client.session).show(Frame(32, 8), 5)
    assert ei.value.status == 400
    assert "invalidName" in (ei.value.body or "")


async def test_body_over_limit_is_transport_error(client):
    class Huge:
        width, height = 60, 60  # 10800 bytes RGB888 → over 8192 after base64

        def to_rgb888(self):
            return bytes(60 * 60 * 3)

    t = AwtrixNgTransport(base(client, "32x8"), session=client.session)
    with pytest.raises(TransportError) as ei:
        await t.show(Huge(), 5)
    assert ei.value.status == 413
    assert "payloadTooLarge" in ei.value.body


async def test_connection_error_is_transport_error():
    t = AwtrixNgTransport("http://127.0.0.1:9", timeout_s=2)
    try:
        with pytest.raises(TransportError) as ei:
            await t.show(Frame(32, 8), 5)
        assert ei.value.status is None
    finally:
        await t.close()


def test_payload_shape():
    f = Frame(32, 8)
    p = AwtrixNgTransport.payload(f, 90)
    assert set(p) == {"draw", "lifetimeMs", "lifetimeExpiry"}
    assert p["lifetimeMs"] == 90_000 and p["lifetimeExpiry"] == "mark"
    cmd = p["draw"][0]
    assert cmd[:5] == ["bitmap", 0, 0, 32, 8] and len(cmd[5]) == 1024
