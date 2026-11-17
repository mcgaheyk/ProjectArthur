"""The studio's "Robot log" source: control, state, frames pushed to the fake clock, page."""

from __future__ import annotations

from datetime import timedelta

import pytest
from logsynth import at, error, state, visit_lines, write

from arthur.render import Frame
from arthur.sim.server import FEEDER, PIPE, create_app
from arthur.sources.logfile import FRESH_FOR_S, VISIT_PAD_S


class FakeTime:
    def __init__(self, t: float = 5000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Wall:
    def __init__(self, dt) -> None:
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def fake_time():
    return FakeTime()


@pytest.fixture
def wall():
    return Wall(at(130))


@pytest.fixture
def logdir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    write(d / "spike-20260301.jsonl", visit_lines(100))
    return d


@pytest.fixture
async def client(aiohttp_client, fake_time, wall, logdir):
    app = create_app(time_fn=fake_time, start_tasks=False, log_dir=logdir)
    app[FEEDER].wall_fn = wall
    c = await aiohttp_client(app)
    app[FEEDER].base_url = str(c.make_url("")).rstrip("/")
    return c


async def control(client, body, status=200):
    r = await client.post("/studio/control", json=body)
    assert r.status == status, await r.text()
    return await r.json()


async def test_log_not_read_until_chosen(client):
    st = await (await client.get("/studio/state")).json()
    assert st["log"]["loaded"] is False and client.app[FEEDER].robot_log is None
    meta = await (await client.get("/studio/meta")).json()
    assert "log" in meta["modes"] and meta["log_sub_modes"] == ["follow", "replay"]


async def test_log_control_validation(client):
    for bad in ({"log": 1}, {"log": {"sub_mode": "rewind"}}, {"log": {"speed": 7}},
                {"log": {"position": "x"}}, {"log": {"playing": "yes"}},
                {"log": {"visit": "nope"}}, {"log": {"bogus": 1}}):
        r = await client.post("/studio/control", json=bad)
        assert r.status == 400, bad
        assert "error" in await r.json()


async def test_follow_block_and_frame(client, wall, logdir):
    pipe = client.app[PIPE].current
    st = await control(client, {"mode": "log", "log": {"sub_mode": "follow"}})
    lg = st["log"]
    assert st["mode"] == "log" and lg["sub_mode"] == "follow"
    assert lg["has_files"] and lg["file"] == "spike-20260301.jsonl" and lg["dir"] == str(logdir)
    assert lg["skipped"] == 0 and lg["lines"] == 5
    # wall is at(130); the newest line is at(710), "in the future": fresh.
    assert lg["fresh"] is True
    assert set(lg["raw"]) == {"status", "robotStatus", "robotCycleState", "robotCycleStatus",
                              "displayCode", "catDetect"}
    # Follow shows the newest line's state, mapped at real (wall) time.
    assert st["showing"]["kind"] == "ready" and lg["raw"]["status"] == "READY"
    (v,) = lg["visits"]
    assert v["statuses"] == ["CAT_DETECTED", "CAT_SENSOR_TIMING", "CLEAN_CYCLE"]
    assert v["changes"][0] == {"at_s": 0.0, "status": "READY"}

    # The frame on the fake clock is the real pipeline's frame.
    snap, health, now, _ = client.app[FEEDER].source()
    expected = pipe.render(pipe.state(snap, health, now), now, (52, 16), None)
    data = await (await client.get("/api/v1/display/screen")).json()
    assert Frame.from_ints(52, 16, data["pixels"]) == expected


async def test_follow_goes_no_data_when_log_is_quiet(client, wall, fake_time):
    wall.dt = at(710) + timedelta(seconds=FRESH_FOR_S + 5)
    st = await control(client, {"mode": "log", "log": {"sub_mode": "follow"}})
    assert st["log"]["fresh"] is False
    assert st["log"]["newest_age_s"] == pytest.approx(FRESH_FOR_S + 5)
    assert st["showing"]["kind"] == "no_data" and st["showing"]["detail"] == "timeout"


async def test_follow_picks_up_new_lines(client, wall, fake_time, logdir):
    await control(client, {"mode": "log", "log": {"sub_mode": "follow"}})
    wall.dt = at(805)
    write(logdir / "spike-20260301.jsonl", [error(800, "connect")], mode="a")
    fake_time.t += 1.5  # past the refresh interval
    feeder = client.app[FEEDER]
    await feeder.tick()
    st = feeder.state()
    assert st["showing"]["kind"] == "no_data" and st["showing"]["detail"] == "api"
    assert st["log"]["contact_error"] == "api"
    write(logdir / "spike-20260302.jsonl", [state(900, "CAT_DETECTED", source="start",
                                                    pet_weight=9.7)])
    wall.dt = at(905)
    fake_time.t += 1.5
    await feeder.tick()
    st = feeder.state()
    assert st["showing"]["kind"] == "in_use" and st["log"]["file"] == "spike-20260302.jsonl"


async def test_replay_through_the_mapping(client, fake_time):
    st = await control(client, {"mode": "log", "log": {"sub_mode": "replay"}})
    lg = st["log"]
    vid = lg["visit"]
    assert vid == lg["visits"][0]["id"] and lg["duration"] == lg["visits"][0]["window_s"]
    pad = VISIT_PAD_S
    expect = [(pad - 1, "ready"), (pad + 1, "in_use"), (pad + 61, "waiting"),
              (pad + 481, "cleaning"), (pad + 611, "just_cleaned"), (lg["duration"], "ready")]
    for pos, kind in expect:
        st = await control(client, {"log": {"position": pos, "playing": False}})
        assert st["showing"]["kind"] == kind, (pos, st["showing"])
        assert st["log"]["position"] == pytest.approx(pos)
    # What the feeder observed is shown beside the robot's raw fields.
    st = await control(client, {"log": {"position": pad + 61, "playing": False}})
    obs = st["log"]["observed"]
    assert obs["visit_started_at"] == at(100).isoformat()
    assert obs["cat_left_at"] == at(160).isoformat()
    assert obs["previous_status"] == "CAT_DETECTED"
    # Raw fields follow the line in force.
    st = await control(client, {"log": {"position": pad + 100}})
    assert st["log"]["raw"]["robotStatus"] == "ROBOT_CAT_DETECT_DELAY"
    assert st["log"]["log_time"] == at(200).isoformat()

    # Play at 60x stops at the end of the window.
    await control(client, {"log": {"visit": vid, "position": 0, "speed": 60, "playing": True}})
    fake_time.t += 10  # 600 s of log time
    assert client.app[FEEDER].log_position() == pytest.approx(600)
    fake_time.t += 100
    st = (await (await client.get("/studio/state")).json())["log"]
    assert st["position"] == pytest.approx(st["duration"]) and st["playing"] is False


async def test_feeder_switches_still_work_in_log_mode(client, fake_time):
    await control(client, {"mode": "log", "fast_expiry": True})
    st = await control(client, {"feeder_alive": False})
    count = st["push_count"]
    fake_time.t += st["ttl_s"] + 0.5
    await client.app[FEEDER].tick()
    st = await (await client.get("/studio/state")).json()
    assert st["push_count"] == count and st["clocks"]["52x16"]["marked"]


async def test_no_log_files(aiohttp_client, tmp_path, fake_time):
    app = create_app(time_fn=fake_time, start_tasks=False, log_dir=tmp_path / "nothing")
    c = await aiohttp_client(app)
    st = await control(c, {"mode": "log"})
    lg = st["log"]
    assert lg["loaded"] and not lg["has_files"] and lg["visits"] == []
    assert "spike_log.py" in lg["spike_command"]
    assert st["showing"]["kind"] == "no_data"
    st = await control(c, {"log": {"sub_mode": "replay"}})
    assert st["showing"]["kind"] == "no_data" and st["log"]["visit"] is None


async def test_page_has_robot_log_tab(client):
    page = await (await client.get("/")).text()
    assert 'data-mode="log"' in page and 'id="log-visits"' in page
    js = await (await client.get("/static/live.js")).text()
    assert '"Robot log"' in js and "Follow" in js and "Replay" in js
