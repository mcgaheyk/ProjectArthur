"""The simulator server: clock routes, studio API, the feeder end to end, and reload."""

import asyncio
import base64
import shutil
import sys
from datetime import UTC, datetime, timedelta

import pytest

import arthur
from arthur.config import DEFAULT_PATH
from arthur.render import Frame
from arthur.sim.clock import BODY_LIMIT, STALE_RGB
from arthur.sim.pipeline import LOOP_S
from arthur.sim.server import (
    CLOCKS,
    FEEDER,
    PIPE,
    STATE_EVERY_S,
    _feeder_loop,
    broadcast,
    check_reload,
    create_app,
    feeder_step,
)


class FakeTime:
    def __init__(self, t: float = 5000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def bitmap(w, h, rgb=(10, 20, 30)):
    data = base64.b64encode(bytes(rgb) * (w * h)).decode()
    return {"draw": [["bitmap", 0, 0, w, h, data]]}


@pytest.fixture
def fake_time():
    return FakeTime()


@pytest.fixture
async def client(aiohttp_client, fake_time):
    app = create_app(time_fn=fake_time, start_tasks=False)
    c = await aiohttp_client(app)
    app[FEEDER].base_url = str(c.make_url("")).rstrip("/")
    return c


def the_clock(client):
    (clock,) = client.app[CLOCKS].values()
    return clock


async def test_one_clock_at_the_bare_api(client):
    assert list(client.app[CLOCKS]) == ["52x16"]
    w, h = 52, 16
    r = await client.put("/api/v1/apps/pushed/litter", json=bitmap(w, h))
    assert r.status == 200 and await r.json() == {"ok": True}
    assert "litter" in the_clock(client).apps
    data = await (await client.get("/api/v1/display/screen")).json()
    assert (data["width"], data["height"]) == (w, h)
    assert data["pixels"] == [0x0A141E] * (w * h)

    r = await client.put("/api/v1/indicators/1", json={"color": "#FF0000"})
    assert r.status == 200
    data = await (await client.get("/api/v1/display/screen")).json()
    assert data["pixels"][w - 1] == 0xFF0000
    assert (await client.delete("/api/v1/indicators/1")).status == 200
    assert (await client.put("/api/v1/indicators/9", json={"color": 1})).status == 404

    r = await client.delete("/api/v1/apps/litter")
    assert r.status == 200
    data = await (await client.get("/api/v1/display/screen")).json()
    assert data["pixels"] == [0] * (w * h)


async def test_no_size_prefixed_clock_routes(client):
    # The per-size /clock/{size}/api/v1/... routes went with the second clock.
    for path in ("/clock/52x16/api/v1/display/screen", "/clock/32x8/api/v1/display/screen"):
        assert (await client.get(path)).status == 404


async def test_clock_errors_shaped_like_ng(client):
    r = await client.put("/api/v1/apps/pushed/litter", json={"bogus": 1})
    assert r.status == 422
    assert await r.json() == {"error": {"code": "validationFailed",
                                        "message": "unknown key 'bogus'", "field": "bogus"}}
    r = await client.put("/api/v1/apps/pushed/litter", json={"text": "HI"})
    assert r.status == 422 and (await r.json())["error"]["code"] == "notSimulated"
    r = await client.put("/api/v1/apps/pushed/bad!", json=bitmap(52, 16))
    assert r.status == 400 and (await r.json())["error"]["code"] == "invalidName"
    r = await client.get("/api/v1/apps/pushed/litter")
    assert r.status == 405 and (await r.json())["error"]["code"] == "methodNotAllowed"
    r = await client.put("/api/v1/apps/pushed/litter", data=b"x" * (BODY_LIMIT + 1),
                         headers={"Content-Type": "application/json"})
    assert r.status == 413 and (await r.json())["error"]["code"] == "payloadTooLarge"


async def test_settings_routes(client):
    r = await client.get("/api/v1/settings")
    assert r.status == 200 and await r.json() == {"enlargeApps": True}  # a fresh TC002
    st = await (await client.get("/studio/state")).json()
    assert st["clocks"]["52x16"]["enlarge_apps"] is True

    r = await client.patch("/api/v1/settings", json={"enlargeApps": False})
    assert r.status == 200 and await r.json() == {"enlargeApps": False}
    assert not the_clock(client).enlarge_apps
    st = await (await client.get("/studio/state")).json()
    assert st["clocks"]["52x16"]["enlarge_apps"] is False

    r = await client.patch("/api/v1/settings", json={"enlargeApps": 1})
    assert r.status == 422
    assert (await r.json())["error"] == {"code": "validationFailed",
                                         "message": "enlargeApps must be true or false",
                                         "field": "enlargeApps"}
    r = await client.patch("/api/v1/settings", json={"enlargeApps": True, "brightness": 9})
    assert r.status == 422
    err = (await r.json())["error"]
    assert err["code"] == "notSimulated" and err["field"] == "brightness"
    assert not the_clock(client).enlarge_apps  # all-or-nothing

    for method in ("PUT", "POST", "DELETE"):
        r = await client.request(method, "/api/v1/settings", json={"enlargeApps": True})
        assert r.status == 405 and (await r.json())["error"]["code"] == "methodNotAllowed"
    r = await client.post("/api/v1/settings/reset")
    assert r.status == 501 and (await r.json())["error"]["code"] == "notSimulated"
    assert not the_clock(client).enlarge_apps


async def test_screen_doubled_while_enlarge_apps_on(client):
    w, h = 52, 16
    pixels = [((i * 7) % 256) << 16 | i % 256 for i in range(w * h)]
    data = base64.b64encode(b"".join(p.to_bytes(3, "big") for p in pixels)).decode()
    await client.put("/api/v1/apps/pushed/litter", json={"draw": [["bitmap", 0, 0, w, h, data]]})
    shown = (await (await client.get("/api/v1/display/screen")).json())["pixels"]
    assert shown == [pixels[(y // 2) * w + x // 2] for y in range(h) for x in range(w)]
    await client.patch("/api/v1/settings", json={"enlargeApps": False})
    assert (await (await client.get("/api/v1/display/screen")).json())["pixels"] == pixels


@pytest.mark.parametrize("path", ["/api/v1/settings/reset", "/api/v1/notifications",
                                  "/api/v1/apps/active/x", "/api/v1"])
async def test_not_simulated_catch_all(client, path):
    r = await client.get(path)
    assert r.status == 501
    err = (await r.json())["error"]
    assert err["code"] == "notSimulated" and "NOT SIMULATED" in err["message"]


async def test_studio_meta(client):
    meta = await (await client.get("/studio/meta")).json()
    assert meta["sizes"] == ["52x16"] and meta["default_size"] == "52x16"
    assert "ready" in meta["kinds"]
    assert meta["variants"]["52x16"]["ready"][0]
    assert any(f["name"] == "ready_laser_dirty" and f["description"] for f in meta["fixtures"])
    sc = {s["name"]: s for s in meta["scenarios"]}["normal_visit"]
    assert sc["duration_s"] > 0 and sc["steps"][0]["label"] == "ready"
    assert meta["palette"]["stale"] == "#6E0700"
    assert meta["timings"]["ttl_s"] == 90
    assert "CAT_DETECTED" in meta["statuses"]


def tile_frames(tile, w=52, h=16):
    """A gallery tile's loop as Frames, one per second."""
    def decode(b64):
        raw = base64.b64decode(b64)
        return Frame.from_ints(w, h, [int.from_bytes(raw[i:i + 3], "big")
                                      for i in range(0, len(raw), 3)])

    distinct = [decode(b) for b in tile["frames"]]
    return [distinct[i] for i in tile["seq"]]


async def test_gallery(client):
    g = await (await client.get("/studio/gallery")).json()
    rows = {r["fixture"]: r for r in g["rows"]}
    assert g["sizes"] == ["52x16"] and g["loop_s"] == LOOP_S
    assert set(rows["ready"]["tiles"]) == {"52x16"}
    tile = rows["ready"]["tiles"]["52x16"][0]
    assert tile["default"] and len(base64.b64decode(tile["frames"][0])) == 52 * 16 * 3

    gs = await (await client.get("/studio/gallery?stale=1")).json()
    st_tile = {r["fixture"]: r for r in gs["rows"]}["ready"]["tiles"]["52x16"][0]
    f = tile_frames(st_tile)[0]
    assert f.get(0, 0) == STALE_RGB and f.get(51, 0) == STALE_RGB


async def test_gallery_tiles_play_their_loop(client):
    pipe = client.app[PIPE].current
    rows = {r["fixture"]: r for r in (await (await client.get("/studio/gallery")).json())["rows"]}

    still = rows["bonnet_removed"]["tiles"]["52x16"]
    assert all(len(t["seq"]) == 1 and len(t["frames"]) == 1 for t in still)

    (drawer,) = rows["drawer_full"]["tiles"]["52x16"]
    frames = tile_frames(drawer)
    assert len(frames) == LOOP_S
    # The face (left part of the screen, away from the ring) changes during the loop.
    faces = {tuple(f.get(x, y) for x in range(26) for y in range(16)) for f in frames}
    assert len(faces) > 2

    (in_use,) = rows["in_use"]["tiles"]["52x16"]
    assert len(set(in_use["seq"])) > 2

    # Frame k of every tile is the fixture played k steps of 1/fps (one second, unless the
    # fixture has its own fps) from its start.
    for row in rows.values():
        for t in row["tiles"]["52x16"]:
            fps = t["fps"]
            assert fps == pipe.fixture_fps(row["fixture"])
            for k, f in enumerate(tile_frames(t)):
                assert f == pipe.fixture_frame(row["fixture"], (52, 16), t["variant"],
                                               k / fps), (row["fixture"], t["variant"], k)


async def test_gallery_stale_border_on_every_frame(client):
    g = await (await client.get("/studio/gallery?stale=1")).json()
    for row in g["rows"]:
        for t in row["tiles"]["52x16"]:
            for f in tile_frames(t):
                assert f.get(0, 0) == STALE_RGB and f.get(51, 15) == STALE_RGB, row["fixture"]
    # The border covers drawer_full's flashing ring, leaving the 15 s face loop.
    (drawer,) = {r["fixture"]: r for r in g["rows"]}["drawer_full"]["tiles"]["52x16"]
    assert len(drawer["seq"]) == 15 and len(drawer["frames"]) > 2


async def test_fixture_mode_loops(client, fake_time):
    feeder = client.app[FEEDER]
    fx = client.app[PIPE].current.fixtures.FIXTURES["drawer_full"]
    await client.post("/studio/control", json={"mode": "fixture", "fixture": "drawer_full"})

    def sim_time():
        return feeder.source()[2]

    assert sim_time() == fx.now
    fake_time.t += 7.4
    assert sim_time() == fx.now + timedelta(seconds=7)
    await feeder.tick()
    st = await (await client.get("/studio/state")).json()
    assert st["fixture_position"] == 7 and st["loop_s"] == LOOP_S
    assert st["showing"]["sim_time"] == (fx.now + timedelta(seconds=7)).isoformat()
    fake_time.t += LOOP_S - 7  # 30.4 s in: wrapped
    assert sim_time() == fx.now
    fake_time.t += 5
    assert sim_time() == fx.now + timedelta(seconds=5)
    # Selecting it again restarts the loop.
    await client.post("/studio/control", json={"fixture": "drawer_full"})
    assert sim_time() == fx.now
    # The live clock shows the looping frames.
    fake_time.t += 3
    await feeder.tick()
    data = await (await client.get("/api/v1/display/screen")).json()
    expected = client.app[PIPE].current.fixture_frame("drawer_full", (52, 16), None, 3)
    assert Frame.from_ints(52, 16, data["pixels"]) == expected


async def test_fixture_mode_keeps_kind_through_the_loop(client, fake_time):
    feeder = client.app[FEEDER]
    pipe = client.app[PIPE].current
    for name, fx in pipe.fixtures.FIXTURES.items():
        feeder.apply({"mode": "fixture", "fixture": name})
        for k in range(LOOP_S + 1):
            shown = feeder.compute()
            assert shown["kind"] == fx.kind, (name, k)
            fake_time.t += 1


async def test_png(client):
    r = await client.get("/studio/png?fixture=ready&size=52x16&scale=4")
    assert r.status == 200 and r.content_type == "image/png"
    assert (await r.read()).startswith(b"\x89PNG")
    assert (await client.get("/studio/png?fixture=nope")).status == 400
    assert (await client.get("/studio/png?fixture=ready&size=32x8")).status == 400


async def test_page_and_scripts_served(client):
    r = await client.get("/")
    page = await r.text()
    assert r.status == 200 and "app.js" in page
    assert "gallery-sizes" not in page and "32x8" not in page
    for name in ("app.js", "led.js", "live.js", "gallery.js", "net.js", "style.css"):
        r = await client.get(f"/static/{name}")
        assert r.status == 200, name
    assert (await client.get("/static/../server.py")).status == 404


async def test_control_validation_and_variants(client):
    r = await client.post("/studio/control", json={"mode": "bogus"})
    assert r.status == 400
    r = await client.post("/studio/control", json={"fixture": "nope"})
    assert r.status == 400
    variants = client.app[PIPE].current.variants("ready", (52, 16))
    pick = variants[-1]
    r = await client.post("/studio/control", json={
        "mode": "fixture", "fixture": "ready", "variants": {"52x16": {"ready": pick}}})
    assert r.status == 200
    st = await r.json()
    assert st["mode"] == "fixture" and st["variants"]["52x16"]["ready"] == pick
    assert st["clocks"]["52x16"]["variant"] == pick and st["clocks"]["52x16"]["kind"] == "ready"
    r = await client.post("/studio/control", json={"variants": {"52x16": {"ready": "nope"}}})
    assert r.status == 400
    r = await client.post("/studio/control", json={"variants": {"32x8": {"ready": pick}}})
    assert r.status == 400


async def test_feeder_pushes_rendered_frame_through_transport(client):
    pipe = client.app[PIPE].current
    await client.post("/studio/control", json={"mode": "fixture", "fixture": "ready"})
    expected = pipe.fixture_frame("ready", (52, 16), None)
    data = await (await client.get("/api/v1/display/screen")).json()
    assert Frame.from_ints(52, 16, data["pixels"]) == expected
    st = await (await client.get("/studio/state")).json()
    assert st["push_count"] >= 1 and st["clocks"]["52x16"]["lifetime_ms"] == 90_000


async def test_dead_feeder_shows_stale_mark_with_fast_expiry(client, fake_time):
    feeder = client.app[FEEDER]
    r = await client.post("/studio/control", json={
        "mode": "fixture", "fixture": "ready", "fast_expiry": True, "feeder_alive": True})
    st = await r.json()
    assert st["ttl_s"] < 10 and st["clocks"]["52x16"]["lifetime_ms"] == st["ttl_s"] * 1000
    await client.post("/studio/control", json={"feeder_alive": False})
    count = feeder.state()["push_count"]
    fake_time.t += st["ttl_s"] + 0.5
    await feeder.tick()  # dead: no push
    assert feeder.state()["push_count"] == count
    data = await (await client.get("/api/v1/display/screen")).json()
    f = Frame.from_ints(52, 16, data["pixels"])
    assert f.get(0, 0) == STALE_RGB and f.get(51, 15) == STALE_RGB
    st = await (await client.get("/studio/state")).json()
    assert st["clocks"]["52x16"]["marked"]
    # Alive again: the next push clears the mark.
    await client.post("/studio/control", json={"feeder_alive": True})
    st = await (await client.get("/studio/state")).json()
    assert not st["clocks"]["52x16"]["marked"]


async def test_scenario_playback_controls(client, fake_time):
    feeder = client.app[FEEDER]
    await client.post("/studio/control", json={
        "mode": "scenario", "scenario": "normal_visit", "playing": False, "position": 0})
    r = await client.post("/studio/control", json={"position": 60, "speed": 60})
    st = await r.json()
    assert st["position"] == 60 and st["showing"]["kind"] == "in_use"
    await client.post("/studio/control", json={"playing": True})
    fake_time.t += 5  # 5 s at 60x = 300 s of scenario time
    assert feeder.position() == pytest.approx(360)


async def test_ws_hello(client):
    ws = await client.ws_connect("/studio/ws")
    msg = await ws.receive_json(timeout=5)
    assert msg["type"] == "hello"
    assert set(msg["clocks"]) == {"52x16"}
    assert "feeder_alive" in msg["state"]
    await ws.close()


async def _next(ws, timeout=2.0):
    return await ws.receive_json(timeout=timeout)


async def _nothing_sent(ws) -> bool:
    try:
        await ws.receive_json(timeout=0.15)
    except TimeoutError:
        return True
    return False


async def test_changed_frame_goes_out_straight_after_the_tick(client, fake_time):
    app = client.app
    feeder = app[FEEDER]
    ws = await client.ws_connect("/studio/ws")
    assert (await _next(ws))["type"] == "hello"

    feeder.apply({"mode": "fixture", "fixture": "robot_off"})
    await feeder_step(app)  # pushes to the fake clock, then broadcasts what it drew
    msg = await _next(ws)
    assert msg["type"] == "tick" and set(msg["clocks"]) == {"52x16"}
    assert "state" in msg  # the first broadcast carries the state
    want = the_clock(client).screen_json()["pixels"]
    assert msg["clocks"]["52x16"]["pixels"] == want

    # A new frame within the same quarter second: the pixels go at once, without the state.
    feeder.apply({"fixture": "drawer_full"})
    await feeder_step(app)
    msg = await _next(ws)
    assert msg["clocks"]["52x16"]["pixels"] == the_clock(client).screen_json()["pixels"] != want
    assert "state" not in msg

    # Nothing changed and the state was sent just now: nothing goes out.
    await feeder_step(app)
    assert await _nothing_sent(ws)
    await ws.close()


async def test_state_goes_out_at_most_four_times_a_second(client, fake_time):
    app = client.app
    ws = await client.ws_connect("/studio/ws")
    await _next(ws)  # hello
    await broadcast(app)
    assert "state" in await _next(ws)
    fake_time.t += STATE_EVERY_S - 0.01
    await broadcast(app)
    assert await _nothing_sent(ws)
    fake_time.t += 0.01
    await broadcast(app)
    msg = await _next(ws)
    assert "state" in msg and msg["clocks"] == {}
    # A forced state (a control change, a reload) is not held back.
    await broadcast(app, force_state=True)
    assert "state" in await _next(ws)
    assert STATE_EVERY_S == 0.25
    await ws.close()


async def test_studio_feeder_ticks_on_the_beat(client, monkeypatch):
    app = client.app
    app[FEEDER].base_url = None  # tick without pushing; only the timing is under test
    start = datetime(2026, 11, 18, 9, 0, 0, 300_000, tzinfo=UTC)
    wall = [start]
    waits: list[float] = []

    async def sleep(s):
        waits.append(s)
        wall[0] += timedelta(seconds=s)
        if len(waits) >= 12:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _feeder_loop(app, wall_fn=lambda: wall[0], sleep=sleep)
    assert waits[0] == pytest.approx(0.075)  # from .300 to the next eighth, .375
    assert waits[1:] == [0.125] * 11


# --- live reload of the config ------------------------------------------------------------


@pytest.fixture
def restore_modules():
    saved = {n: m for n, m in sys.modules.items() if n.startswith("arthur.")}
    attrs = {a: getattr(arthur, a) for a in ("model", "mapping", "fixtures", "render")
             if hasattr(arthur, a)}
    yield
    for n in [n for n in sys.modules if n.startswith("arthur.")]:
        if n not in saved:
            del sys.modules[n]
    sys.modules.update(saved)
    for a, m in attrs.items():
        setattr(arthur, a, m)


async def test_config_reload_and_syntax_error_keeps_last_good(aiohttp_client, tmp_path,
                                                             restore_modules):
    cfg = tmp_path / "display.toml"
    shutil.copy(DEFAULT_PATH, cfg)
    app = create_app(cfg, start_tasks=False)
    client = await aiohttp_client(app)
    pipe = app[PIPE]
    assert pipe.current.version == 1

    text = cfg.read_text()
    cfg.write_text(text.replace('ok        = "#00D040"', 'ok        = "#123456"'))
    assert await check_reload(app) is True
    assert pipe.current.version == 2 and pipe.current.cfg.rgb("ok") == (0x12, 0x34, 0x56)
    meta = await (await client.get("/studio/meta")).json()
    assert meta["palette"]["ok"] == "#123456" and meta["version"] == 2

    cfg.write_text(text + "\n[timings\n")  # broken TOML mid-edit
    assert await check_reload(app) is False
    assert pipe.current.version == 2  # last good version still serving
    st = await (await client.get("/studio/state")).json()
    assert st["reload_error"]
    assert (await client.get("/studio/gallery")).status == 200

    cfg.write_text(text)  # fixed on the next save
    assert await check_reload(app) is True
    assert pipe.error is None and pipe.current.version == 3


async def test_manual_mode(client):
    r = await client.post("/studio/control", json={
        "mode": "manual", "manual": {"status": "CAT_DETECTED", "waste_drawer_level": 90}})
    st = await r.json()
    assert st["showing"]["kind"] == "in_use"
    assert "badges" not in st["showing"]
    st = await (await client.post("/studio/control", json={
        "manual": {"health_error": "auth"}})).json()
    assert st["showing"]["kind"] == "no_data"
    r = await client.post("/studio/control", json={"manual": {"status": "NOPE"}})
    assert r.status == 400
    r = await client.post("/studio/control", json={"manual": {"waste_drawer_level": 101}})
    assert r.status == 400


async def test_manual_wait_accepts_any_whole_minute(client):
    meta = await (await client.get("/studio/meta")).json()
    assert meta["wait_minutes_range"] == [1, 60]
    for v in (1, 8, 60):
        r = await client.post("/studio/control", json={"manual": {"wait_time_minutes": v}})
        assert r.status == 200 and (await r.json())["manual"]["wait_time_minutes"] == v
    for bad in (0, 61, 7.5, True, "8"):
        r = await client.post("/studio/control", json={"manual": {"wait_time_minutes": bad}})
        assert r.status == 400, bad


async def test_manual_cat_left_switch_drives_in_use_to_waiting(client):
    feeder = client.app[FEEDER]
    clock = [feeder.wall_fn()]
    feeder.wall_fn = lambda: clock[0]
    st = await (await client.post("/studio/control", json={
        "mode": "manual", "manual": {"status": "CAT_SENSOR_TIMING", "wait_time_minutes": 8}})).json()
    assert st["showing"]["kind"] == "in_use" and st["manual"]["cat_left"] is False
    clock[0] += timedelta(seconds=30)
    st = await (await client.post("/studio/control", json={"manual": {"cat_left": True}})).json()
    assert st["showing"]["kind"] == "waiting"
    snap, _, now = feeder._manual_source()
    assert snap.cat_left_at == now and snap.visit_started_at == now - timedelta(seconds=30)
    st = await (await client.post("/studio/control", json={"manual": {"cat_left": False}})).json()
    assert st["showing"]["kind"] == "in_use"
    # Without the weight, it falls back to WAIT after cat_left_fallback_s.
    clock[0] += timedelta(seconds=200)
    assert feeder.compute()["kind"] == "waiting"
    # Leaving the visit turns the switch off.
    st = await (await client.post("/studio/control", json={"manual": {"cat_left": True}})).json()
    st = await (await client.post("/studio/control", json={"manual": {"status": "READY"}})).json()
    assert st["manual"]["cat_left"] is False


async def test_manual_interruption_carries_the_cleaning_countdown_on(client):
    feeder = client.app[FEEDER]
    clock = [feeder.wall_fn()]
    feeder.wall_fn = lambda: clock[0]
    await client.post("/studio/control", json={"mode": "manual",
                                                "manual": {"status": "CLEAN_CYCLE"}})
    est = timedelta(seconds=feeder.pipe.current.cfg.timings.clean_cycle_estimate_s)
    first = clock[0]
    clock[0] += timedelta(seconds=40)
    st = await (await client.post("/studio/control", json={
        "manual": {"status": "CAT_SENSOR_INTERRUPTED"}})).json()
    assert st["showing"]["kind"] == "interrupted"
    clock[0] += timedelta(seconds=25)
    st = await (await client.post("/studio/control", json={
        "manual": {"status": "CLEAN_CYCLE"}})).json()
    snap, health, now = feeder._manual_source()
    assert snap.cycled_s == 40.0
    state = feeder.pipe.current.state(snap, health, now)
    assert (state.kind, state.since) == ("cleaning", now)
    assert state.cycle_end == first + est + timedelta(seconds=25)
    # Ending the cycle clears it; a pause does not carry.
    await client.post("/studio/control", json={"manual": {"status": "READY"}})
    assert feeder._manual_source()[0].cycled_s == 0.0
    for status in ("CLEAN_CYCLE", "PAUSED", "CLEAN_CYCLE"):
        clock[0] += timedelta(seconds=10)
        await client.post("/studio/control", json={"manual": {"status": status}})
    assert feeder._manual_source()[0].cycled_s == 0.0


async def test_manual_offline_age_is_from_the_switch(client):
    feeder = client.app[FEEDER]
    clock = [feeder.wall_fn()]
    feeder.wall_fn = lambda: clock[0]
    await client.post("/studio/control", json={"mode": "manual", "manual": {"is_online": False}})
    clock[0] += timedelta(minutes=5)
    snap, health, now = feeder._manual_source()
    state = feeder.pipe.current.state(snap, health, now)
    assert state.kind == "robot_offline" and state.last_seen == now - timedelta(minutes=5)
