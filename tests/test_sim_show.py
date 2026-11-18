"""The studio and the idle show (D-0019): show fixtures, the Idle show switch, sample weather,
the idle-show scenarios, reload of the time base and pushes from outside feeders."""

import base64
import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

import arthur
from arthur.config import DEFAULT_PATH
from arthur.render import Frame
from arthur.sim.feeder import SHOW_SHARES, WEATHER_AGE_S
from arthur.sim.pipeline import LOOP_S, MODULES, ROOT, fresh_pipeline, watched_files
from arthur.sim.server import CLOCKS, FEEDER, PIPE, create_app
from arthur.timebase import SHOW_FPS


class FakeTime:
    def __init__(self, t: float = 5000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def fake_time():
    return FakeTime()


@pytest.fixture
async def client(aiohttp_client, fake_time):
    app = create_app(time_fn=fake_time, start_tasks=False)
    c = await aiohttp_client(app)
    app[FEEDER].base_url = str(c.make_url("")).rstrip("/")
    return c


def tile_frames(tile, w=52, h=16):
    def decode(b64):
        raw = base64.b64decode(b64)
        return Frame.from_ints(w, h, [int.from_bytes(raw[i:i + 3], "big")
                                      for i in range(0, len(raw), 3)])

    distinct = [decode(b) for b in tile["frames"]]
    return [distinct[i] for i in tile["seq"]]


def show_fixtures(pipe):
    """Every fixture that plays at its own rate; none of the tests depend on the names."""
    return [f for f in pipe.fixtures.FIXTURES.values() if f.fps]


def screen(client):
    (clock,) = client.app[CLOCKS].values()
    return Frame.from_ints(52, 16, clock.screen_json()["pixels"])


# --- show fixtures --------------------------------------------------------------------------


async def test_show_fixtures_map_with_their_cue_and_weather(client):
    pipe = client.app[PIPE].current
    shows = show_fixtures(pipe)
    assert len(shows) >= 3
    for fx in shows:
        st = pipe.fixture_state(fx.name)
        assert st.kind == "ready" and st.cue == fx.cue and st.weather == fx.weather, fx.name


async def test_show_tiles_play_their_whole_slot_at_their_rate(client):
    pipe = client.app[PIPE].current
    resp = await client.get("/studio/gallery", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("Content-Encoding") == "gzip"
    g = await resp.json()
    rows = {r["fixture"]: r for r in g["rows"]}
    for fx in show_fixtures(pipe):
        st = pipe.fixture_state(fx.name)
        for t in rows[fx.name]["tiles"]["52x16"]:
            assert t["fps"] == fx.fps == SHOW_FPS
            n = round(fx.loop_s * fx.fps)
            assert len(t["seq"]) == n, fx.name
            frames = tile_frames(t)
            for k in range(0, n, 7):
                assert frames[k] == pipe.render(st, fx.time_at(k / fx.fps), (52, 16),
                                                t["variant"]), (fx.name, k)
            # The loop is the slot: one step past the last frame is the first again.
            assert pipe.fixture_frame(fx.name, (52, 16), t["variant"], n / fx.fps) == frames[0]
            # Distinct frames go once each: a tile is far smaller than its steps.
            assert len(t["frames"]) < n
    rat = next(f for f in show_fixtures(pipe) if f.cue.kind == "scene")
    tile = rows[rat.name]["tiles"]["52x16"][0]
    assert 30 < len(tile["frames"]) < 200
    assert len(json.dumps(tile)) < 400_000


async def test_ordinary_tiles_are_unchanged(client):
    """Fixtures without their own rate: one-second steps, at most LOOP_S, mapped as before."""
    pipe = client.app[PIPE].current
    g = await (await client.get("/studio/gallery")).json()
    rows = {r["fixture"]: r for r in g["rows"]}
    for fx in pipe.fixtures.FIXTURES.values():
        if fx.fps:
            continue
        if fx.cue is None and fx.weather is None:
            assert pipe.fixture_state(fx.name) == pipe.state(fx.snapshot, fx.health, fx.now)
        for t in rows[fx.name]["tiles"]["52x16"]:
            assert t["fps"] == 1 and len(t["seq"]) <= LOOP_S
            st = pipe.fixture_state(fx.name)
            for k, f in enumerate(tile_frames(t)):
                assert f == pipe.render(st, fx.now + timedelta(seconds=k), (52, 16),
                                        t["variant"]), (fx.name, k)


async def test_fixture_mode_plays_a_scene_at_eight_frames_a_second(client, fake_time):
    feeder = client.app[FEEDER]
    pipe = client.app[PIPE].current
    fx = next(f for f in show_fixtures(pipe) if f.cue.kind == "scene")
    n = round(fx.loop_s * fx.fps)
    wall = [datetime(2026, 11, 18, 9, 0, 0, 30_000, tzinfo=UTC)]  # 95 ms to the next beat
    feeder.wall_fn = lambda: wall[0]
    feeder.apply({"mode": "fixture", "fixture": fx.name})
    assert feeder.fixture_position() == 0
    await feeder.tick()
    assert screen(client) == pipe.fixture_frame(fx.name, (52, 16), None, 0)

    def beat(j, jitter=0.004):
        """Fake time at the feeder's j-th beat after the selection, a little late."""
        return 5000.0 + 0.095 + j / fx.fps + jitter

    for j in (0, 1, 2, 7, 8, 9, 100, n - 1):
        fake_time.t = beat(j)
        assert feeder.fixture_position() == j / fx.fps, j
    fake_time.t = beat(9, jitter=0.03)  # a slow tick still gets its own frame
    assert feeder.fixture_position() == 9 / fx.fps
    fake_time.t = beat(n)  # the slot ends: the glance again
    assert feeder.fixture_position() == 0
    st = await (await client.get("/studio/state")).json()
    assert st["loop_s"] == fx.loop_s and st["fixture_fps"] == fx.fps

    fake_time.t = beat(150)
    await feeder.tick()
    assert screen(client) == pipe.fixture_frame(fx.name, (52, 16), None, 150 / fx.fps)
    assert feeder.compute()["show"]["part"] == "scene"


# --- the Idle show switch -------------------------------------------------------------------


async def test_switch_turns_the_show_on_in_memory_only(client):
    feeder = client.app[FEEDER]
    pipe = client.app[PIPE].current
    before = DEFAULT_PATH.read_bytes()
    assert pipe.cfg.idle.page_pct == 0 and pipe.cfg.idle.scene_pct == 0  # the repo's config
    r = await client.post("/studio/control", json={
        "mode": "scenario", "scenario": "idle_hour", "playing": False, "position": 50})
    st = await r.json()
    assert st["idle_show"] is False
    assert st["showing"]["kind"] == "ready" and st["showing"]["show"]["part"] == "home"
    assert feeder.last_state.cue is None

    st = await (await client.post("/studio/control", json={"idle_show": True})).json()
    assert st["idle_show"] is True and st["idle_show_shares"] == SHOW_SHARES
    show = st["showing"]["show"]
    assert (show["part"], show["name"]) == ("page", "weather")
    assert show["left_s"] == pytest.approx(99 - 50)
    assert feeder.last_state.cue.kind == "page"
    # Nothing written, nothing changed in the loaded config.
    assert DEFAULT_PATH.read_bytes() == before
    assert pipe.cfg.idle.page_pct == 0 and client.app[PIPE].current is pipe
    assert st["config_shares"] == {"page_pct": 0, "scene_pct": 0}

    st = await (await client.post("/studio/control", json={"idle_show": False})).json()
    assert st["showing"]["show"]["part"] == "home" and feeder.last_state.cue is None
    assert (await client.post("/studio/control", json={"idle_show": 1})).status == 400


async def test_idle_hour_plays_pages_and_scenes_at_their_offsets(client):
    feeder = client.app[FEEDER]
    feeder.apply({"mode": "scenario", "scenario": "idle_hour", "playing": False,
                  "idle_show": True})
    sc = client.app[PIPE].current.fixtures.SCENARIOS["idle_hour"]

    def cue_at(t):
        feeder.apply({"position": t})
        shown = feeder.compute()
        assert shown["kind"] == "ready"
        return feeder.last_state.cue, shown["show"]

    for t, kind, start in [(0, None, None), (44, None, None), (45, "page", 45),
                           (98, "page", 45), (99, None, None), (143, None, None),
                           (144, "scene", 144), (179, "scene", 144), (180, None, None),
                           (225, "page", 225), (3600 - 36, "scene", 3600 - 36)]:
        cue, show = cue_at(t)
        if kind is None:
            assert cue is None and show["part"] == "home", t
        else:
            assert cue.kind == kind and cue.at == sc.start + timedelta(seconds=start), t
            assert show["part"] == kind
    cue, show = cue_at(144)
    assert show["name"] in client.app[PIPE].current.render_mod.idle_show.SCENES
    assert show["phase"] == "glance" and show["left_s"] == pytest.approx(36)
    _, show = cue_at(100)
    assert show["part"] == "home" and show["next"] == "scene" and show["left_s"] == 44
    # The clock really moves: a scene's frames differ from the home page's.
    feeder.apply({"position": 150})
    in_scene = feeder.compute()["frames"]["52x16"]["frame"]
    feeder.apply({"idle_show": False})
    home = feeder.compute()["frames"]["52x16"]["frame"]
    assert in_scene != home


async def test_a_visit_replaces_a_scene_on_the_next_tick(client, fake_time):
    feeder = client.app[FEEDER]
    pipe = client.app[PIPE].current
    sc = pipe.fixtures.SCENARIOS["visit_mid_scene"]
    in_at = next(s.at_s for s in sc.steps if s.label == "in_use")
    feeder.apply({"mode": "scenario", "scenario": "visit_mid_scene", "idle_show": True,
                  "playing": False, "position": in_at - 1 / SHOW_FPS})
    await feeder.tick()
    assert feeder.last_state.kind == "ready" and feeder.last_state.cue.kind == "scene"
    assert feeder.compute()["show"]["phase"] == "body"
    feeder.apply({"speed": 1, "playing": True})
    fake_time.t += 1 / SHOW_FPS
    await feeder.tick()
    assert feeder.last_state.kind == "in_use"
    expected = pipe.render(feeder.last_state, sc.start + timedelta(seconds=in_at), (52, 16),
                           None)
    assert screen(client) == expected


# --- sample weather -------------------------------------------------------------------------


async def test_sample_weather_reaches_the_mapped_state(client):
    feeder = client.app[FEEDER]
    pipe = client.app[PIPE].current
    sample = pipe.fixtures.SAMPLE_WEATHER
    st = await (await client.post("/studio/control", json={"mode": "manual"})).json()
    assert st["weather"] == {"reading": True, "code": sample.code, "is_day": sample.is_day,
                             "temp_f": sample.temp_f}
    w = feeder.last_state.weather
    assert (w.code, w.is_day, w.temp_f, w.high_f, w.low_f) == (
        sample.code, sample.is_day, sample.temp_f, sample.high_f, sample.low_f)
    now = datetime.fromisoformat(st["showing"]["sim_time"])
    assert w.fetched_at == now - timedelta(seconds=WEATHER_AGE_S)

    await client.post("/studio/control", json={
        "weather": {"code": 95, "is_day": False, "temp_f": 72}})
    w = feeder.last_state.weather
    assert (w.code, w.is_day, w.temp_f) == (95, False, 72.0)

    st = await (await client.post("/studio/control", json={"weather": {"reading": False}})
                ).json()
    assert st["weather"]["reading"] is False and feeder.last_state.weather is None

    # Scenario mode gets it too.
    await client.post("/studio/control", json={"weather": {"reading": True},
                                               "mode": "scenario", "scenario": "idle_hour"})
    assert feeder.last_state.weather.code == 95

    for bad in ({"code": 4}, {"temp_f": "hot"}, {"temp_f": 500}, {"is_day": 1},
                {"wind": 3}, [1]):
        assert (await client.post("/studio/control", json={"weather": bad})).status == 400


async def test_meta_lists_the_weather_conditions(client):
    meta = await (await client.get("/studio/meta")).json()
    codes = {c: label for c, label in meta["weather_codes"]}
    assert {0, 3, 45, 95} <= set(codes) and codes[0] == "clear"
    assert meta["idle_show_shares"] == SHOW_SHARES
    assert all("fps" in f and "loop_s" in f for f in meta["fixtures"])


# --- reload ---------------------------------------------------------------------------------


@pytest.fixture
def restore_modules():
    saved = {n: m for n, m in sys.modules.items() if n.startswith("arthur.")}
    attrs = {a: getattr(arthur, a) for a in ("timebase", "model", "schedule", "mapping",
                                             "fixtures", "render") if hasattr(arthur, a)}
    yield
    for n in [n for n in sys.modules if n.startswith("arthur.")]:
        if n not in saved:
            del sys.modules[n]
    sys.modules.update(saved)
    for a, m in attrs.items():
        setattr(arthur, a, m)


def test_timebase_reloads_with_the_renderer(restore_modules):
    assert "arthur.timebase" in MODULES
    assert ROOT / "timebase.py" in watched_files(DEFAULT_PATH)
    old = sys.modules["arthur.timebase"]
    p = fresh_pipeline(None, 2)
    assert p.timebase is sys.modules["arthur.timebase"] is not old
    assert p.render_mod.idle_show.frames_since is p.timebase.frames_since
    assert p.schedule is sys.modules["arthur.schedule"]


# --- outside feeders ------------------------------------------------------------------------


async def test_a_push_from_outside_reaches_the_page_at_once(client):
    ws = await client.ws_connect("/studio/ws")
    assert (await ws.receive_json(timeout=2))["type"] == "hello"
    data = base64.b64encode(bytes((200, 10, 10)) * (52 * 16)).decode()
    r = await client.put("/api/v1/apps/pushed/outside",
                         json={"draw": [["bitmap", 0, 0, 52, 16, data]]})
    assert r.status == 200
    msg = await ws.receive_json(timeout=2)
    assert msg["type"] == "tick"
    assert msg["clocks"]["52x16"]["pixels"][0] == 0xC80A0A
    await ws.close()
