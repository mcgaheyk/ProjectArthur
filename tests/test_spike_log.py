"""Tests for the spike logger's pure parts. No network: every robot here is a fake."""

from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from pylitterbot.enums import LitterBoxStatus

from arthur import spikelog as sl

T0 = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def wall(self) -> datetime:
        return T0 + timedelta(seconds=self.t)

    def mono(self) -> float:
        return self.t


def raw_data(**overrides):
    data = {
        "unitId": "UNIT-123",
        "serial": "LR4C000001",
        "userId": "USER-9",
        "name": "Arthur's box",
        "sessionId": "abc",
        "robotStatus": "ROBOT_IDLE",
        "robotCycleState": "CYCLE_STATE_NONE",
        "robotCycleStatus": "CYCLE_IDLE",
        "displayCode": "DC_MODE_IDLE",
        "catDetect": "CAT_DETECT_CLEAR",
        "isCatDetectPending": False,
        "weightSensor": 0.0,
        "pinchStatus": "CLEAR",
        "isDFIFull": False,
        "isDFIPartialFull": False,
        "DFILevelPercent": 42,
        "litterLevel": 451,
        "litterLevelPercentage": 0.8,
        "sleepStatus": "WAKE",
        "unitPowerStatus": "ON",
        "isBonnetRemoved": False,
        "wifiRssi": -55,
        "globeMotorFaultStatus": "FAULT_CLEAR",
        "weekdaySleepModeEnabled": {"Monday": {"isEnabled": False, "sleepTime": 0}},
        "ownerEmail": "kyle@example.com",
        "lanIp": "192.168.1.20",
    }
    data.update(overrides)
    return data


class FakeRobot:
    """Looks like pylitterbot's LitterRobot4 for the properties the logger reads."""

    def __init__(self, **raw) -> None:
        self._data = raw_data(**raw)
        self.status = LitterBoxStatus.READY
        self.status_code = "RDY"
        self.status_text = "Ready"
        self.is_online = True
        self.is_on = True
        self.is_sleeping = False
        self.sleep_mode_enabled = False
        self.waste_drawer_level = 42.0
        self.is_waste_drawer_full = False
        self.is_drawer_full_indicator_triggered = False
        self.litter_level = 80.0
        self.litter_level_state = None
        self.pet_weight = 9.5
        self.clean_cycle_wait_time_minutes = 7
        self.is_laser_dirty = False
        self.power_type = "AC"
        self.last_seen = T0
        self.cycle_count = 1234
        self.cycle_capacity = 30
        self.cycles_after_drawer_full = 0
        self.globe_motor_fault_status = None
        self.hopper_status = None
        self.is_hopper_removed = False
        self.name = "Arthur's box"
        self.serial = "LR4C000001"

    def to_dict(self):
        return self._data

    def set_status(self, status: LitterBoxStatus, robot_status: str) -> None:
        self.status = status
        self.status_code = status.value
        self.status_text = status.text
        self._data["robotStatus"] = robot_status


# ---- record building ---------------------------------------------------------------------


def test_build_record_has_properties_and_raw_fields():
    rec = sl.build_record(FakeRobot())
    assert rec["status"] == "READY"
    assert rec["status_code"] == "RDY"
    assert rec["last_seen"] == T0.isoformat()
    assert rec["waste_drawer_level"] == 42.0
    for key in sl.TRACKED_RAW_KEYS:
        assert key in rec
    assert rec["robotStatus"] == "ROBOT_IDLE"
    assert rec["DFILevelPercent"] == 42
    # identifying values are never in the flat record
    flat = json.dumps(rec)
    assert "LR4C000001" not in flat and "UNIT-123" not in flat and "Arthur's box" not in flat


def test_build_record_survives_a_broken_property_and_missing_raw_key():
    class Broken(FakeRobot):
        @property
        def litter_level_state(self):
            raise KeyError("litterLevelState")

        @litter_level_state.setter
        def litter_level_state(self, value):
            pass

    robot = Broken()
    del robot._data["catDetect"]
    rec = sl.build_record(robot)
    assert rec["litter_level_state"] == "<error: KeyError>"
    assert rec["catDetect"] is None
    assert rec["status"] == "READY"


def test_tracked_raw_keys_exist_in_installed_lr4_model():
    from pylitterbot.robot.models import LITTER_ROBOT_4_MODEL

    for key in sl.TRACKED_RAW_KEYS:
        assert f"\n    {key}\n" in LITTER_ROBOT_4_MODEL, key


def test_tracked_properties_exist_on_installed_lr4():
    from pylitterbot import LitterRobot4

    for name in sl.TRACKED_PROPERTIES:
        assert hasattr(LitterRobot4, name), name


# ---- diffing and the noisy-field rate limit -------------------------------------------------


def test_diff_records():
    assert sl.diff_records({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {
        "b": [2, 3],
        "c": [None, 4],
    }
    assert sl.diff_records({"a": 1}, {"a": 1}) == {}


def make_recorder():
    clock = Clock()
    return clock, sl.Recorder(wall=clock.wall, mono=clock.mono)


def test_first_line_is_start_with_redacted_raw():
    _clock, rec = make_recorder()
    robot = FakeRobot()
    line = rec.observe(sl.build_record(robot), "start", robot.to_dict())
    assert line["source"] == "start"
    assert line["ts"] == "2026-11-15T12:00:00.000Z"
    assert line["t"] == 0.0
    assert line["changed"] == {}
    assert line["raw"]["serial"] == sl.REDACTED
    assert line["raw"]["robotStatus"] == "ROBOT_IDLE"


def test_unchanged_writes_nothing_and_status_change_writes_with_raw():
    clock, rec = make_recorder()
    robot = FakeRobot()
    rec.observe(sl.build_record(robot), "start", robot.to_dict())
    clock.t = 1.0
    assert rec.observe(sl.build_record(robot), "poll", robot.to_dict()) is None

    clock.t = 2.5
    robot.set_status(LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT")
    line = rec.observe(sl.build_record(robot), "ws", robot.to_dict())
    assert line["source"] == "ws"
    assert line["status"] == "CAT_DETECTED" and line["status_code"] == "CD"
    assert line["changed"]["status"] == ["READY", "CAT_DETECTED"]
    assert line["changed"]["robotStatus"] == ["ROBOT_IDLE", "ROBOT_CAT_DETECT"]
    assert line["t"] == 2.5
    assert "raw" in line and line["raw"]["unitId"] == sl.REDACTED


def test_non_status_change_has_no_raw():
    _clock, rec = make_recorder()
    robot = FakeRobot()
    rec.observe(sl.build_record(robot), "start", robot.to_dict())
    robot._data["catDetect"] = "CAT_DETECT_LASER"
    line = rec.observe(sl.build_record(robot), "ws", robot.to_dict())
    assert line["changed"] == {"catDetect": ["CAT_DETECT_CLEAR", "CAT_DETECT_LASER"]}
    assert "raw" not in line


def test_noisy_only_changes_are_rate_limited_but_kept():
    clock, rec = make_recorder()
    robot = FakeRobot()
    rec.observe(sl.build_record(robot), "start", robot.to_dict())

    clock.t = 1.0
    robot._data["weightSensor"] = 3.1
    assert rec.observe(sl.build_record(robot), "ws") is None  # within 5 s: held
    clock.t = 2.0
    robot._data["weightSensor"] = 4.4
    robot._data["wifiRssi"] = -60
    assert rec.observe(sl.build_record(robot), "ws") is None
    clock.t = 4.0
    assert rec.flush_pending() is None  # still inside the interval

    clock.t = 5.5
    line = rec.flush_pending()
    assert line is not None
    assert line["changed"] == {"weightSensor": [0.0, 4.4], "wifiRssi": [-55, -60]}
    assert line["source"] == "ws"
    assert rec.flush_pending() is None

    clock.t = 11.0  # more than 5 s after the last line: written straight away
    robot._data["weightSensor"] = 9.0
    line = rec.observe(sl.build_record(robot), "ws")
    assert line["changed"] == {"weightSensor": [4.4, 9.0]}


def test_real_change_is_written_immediately_and_carries_held_noise():
    clock, rec = make_recorder()
    robot = FakeRobot()
    rec.observe(sl.build_record(robot), "start", robot.to_dict())
    clock.t = 1.0
    robot._data["weightSensor"] = 4.0
    assert rec.observe(sl.build_record(robot), "ws") is None
    clock.t = 1.2
    robot.set_status(LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT")
    line = rec.observe(sl.build_record(robot), "ws", robot.to_dict())
    assert set(line["changed"]) >= {"status", "weightSensor", "robotStatus"}
    assert rec.flush_pending() is None


def test_heartbeat_always_writes():
    clock, rec = make_recorder()
    robot = FakeRobot()
    rec.observe(sl.build_record(robot), "start", robot.to_dict())
    clock.t = 900
    line = rec.observe(sl.build_record(robot), "heartbeat")
    assert line["source"] == "heartbeat" and line["changed"] == {}


# ---- redaction and scrubbing ----------------------------------------------------------------


def test_redact_by_key_and_value_keeps_keys():
    out = sl.redact(raw_data())
    for key in ("unitId", "serial", "userId", "name", "sessionId", "ownerEmail", "lanIp"):
        assert out[key] == sl.REDACTED, key
    assert set(out) == set(raw_data())
    assert out["robotStatus"] == "ROBOT_IDLE"
    assert out["weekdaySleepModeEnabled"]["Monday"]["isEnabled"] is False
    nested = sl.redact({"x": [{"petId": "p1", "email": "a@b.co"}], "note": "ip 10.0.0.1"})
    assert nested == {"x": [{"petId": sl.REDACTED, "email": sl.REDACTED}], "note": sl.REDACTED}
    assert sl.redact({"serial": None, "name": ""}) == {"serial": None, "name": ""}


def test_tracked_keys_are_not_redacted():
    for key in (*sl.TRACKED_RAW_KEYS, *sl.TRACKED_PROPERTIES):
        assert sl.redact({key: "value"}) == {key: "value"}, key


def test_scrub_removes_tokens():
    text = (
        "403 url='wss://lr4.iothings.site/graphql/realtime?header=eyJBdXRo&payload=e30=' "
        "Authorization: Bearer abc.def-ghi eyJhbGciOi.eyJzdWIi.sig"
    )
    out = sl.scrub(text)
    assert "eyJBdXRo" not in out and "abc.def-ghi" not in out and "eyJzdWIi" not in out
    assert "header=" in out


# ---- status timeline and summary ------------------------------------------------------------


def test_tracker_summary_and_visit_phases():
    tr = sl.StatusTracker()
    seq = [
        ("READY", 0),
        ("CAT_DETECTED", 100),
        ("CAT_SENSOR_TIMING", 160),
        ("CLEAN_CYCLE", 580),
        ("READY", 720),
        ("CAT_DETECTED", 1000),  # an incomplete visit
        ("CAT_SENSOR_TIMING", 1030),
    ]
    assert tr.update("READY", 0, T0) is None
    assert tr.update("READY", 5, T0) is False
    for status, t in seq[1:]:
        tr.update(status, t, T0 + timedelta(seconds=t))
    assert tr.transitions == 6
    totals = tr.time_in_status(1100)
    assert totals == {
        "READY": 100 + 280,
        "CAT_DETECTED": 60 + 30,
        "CAT_SENSOR_TIMING": 420 + 70,
        "CLEAN_CYCLE": 140,
    }
    visits = tr.visit_sequences()
    assert len(visits) == 1
    assert visits[0]["phases"] == {
        "CAT_DETECTED": 60,
        "CAT_SENSOR_TIMING": 420,
        "CLEAN_CYCLE": 140,
    }
    assert visits[0]["total"] == 620
    assert visits[0]["start"] == "2026-11-15T12:01:40.000Z"
    text = tr.summary_text(1100)
    assert "transitions: 6" in text and "Complete visits" in text and "7m00s" in text


def test_console_line():
    line = sl.console_line(T0, "READY", "CAT_DETECTED", "ws", 3725.0, 42.0)
    assert "READY → CAT_DETECTED" in line and "[ws]" in line
    assert "1h02m05s" in line and "drawer 42%" in line
    assert line[8] == "." and line[9:12] == "000"


# ---- environment, options, files ------------------------------------------------------------


def test_read_credentials():
    assert sl.read_credentials({}) == ["WHISKER_USERNAME", "WHISKER_PASSWORD"]
    assert sl.read_credentials({"WHISKER_USERNAME": "u", "WHISKER_PASSWORD": " "}) == [
        "WHISKER_PASSWORD"
    ]
    assert sl.read_credentials({"WHISKER_USERNAME": "u", "WHISKER_PASSWORD": "p"}) == ("u", "p")


def test_main_without_credentials_exits_2_and_never_connects(monkeypatch, capsys):
    async def boom(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(sl, "run", boom)
    assert sl.main([], environ={}) == sl.EXIT_MISSING_ENV
    err = capsys.readouterr().err
    assert ".env.example" in err and "uv run --env-file .env python scripts/spike_log.py" in err


def test_minimum_poll_interval(monkeypatch, capsys):
    assert sl.clamp_poll_seconds(30) == (120.0, True)
    assert sl.clamp_poll_seconds(120) == (120.0, False)
    assert sl.clamp_poll_seconds(600) == (600.0, False)
    seen = {}

    async def fake_run(opts, username, password):
        seen["poll"] = opts.poll_seconds
        return 0

    monkeypatch.setattr(sl, "run", fake_run)
    env = {"WHISKER_USERNAME": "u", "WHISKER_PASSWORD": "p"}
    assert sl.main(["--poll-seconds", "10"], environ=env) == 0
    assert seen["poll"] == 120.0
    assert "minimum" in capsys.readouterr().err
    assert sl.build_parser().parse_args([]).poll_seconds == 300


def test_token_cache_mode_600_and_roundtrip(tmp_path):
    path = tmp_path / "tok.json"
    assert sl.load_token(path) is None
    sl.save_token(path, {"access_token": "a", "id_token": "i", "refresh_token": "r"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sl.load_token(path)["refresh_token"] == "r"
    sl.save_token(path, None)  # the library can pass None; keep the old cache
    assert sl.load_token(path) is not None
    path.write_text("not json")
    assert sl.load_token(path) is None


def test_daily_file_switches_at_date_change(tmp_path):
    day = [date(2026, 11, 15)]
    w = sl.DailyJsonl(tmp_path, "spike", today=lambda: day[0])
    w.write({"a": 1})
    day[0] = date(2026, 11, 16)
    w.write({"a": 2})
    w.close()
    assert (tmp_path / "spike-20261115.jsonl").read_text() == '{"a": 1}\n'
    assert (tmp_path / "spike-20261116.jsonl").read_text() == '{"a": 2}\n'


def test_backoff():
    b = sl.Backoff()
    assert [b.next() for _ in range(7)] == [30, 60, 120, 240, 480, 600, 600]
    b.reset()
    assert b.next() == 30


def test_is_auth_error():
    from aiohttp import ClientResponseError
    from pylitterbot.exceptions import LitterRobotException, LitterRobotLoginException

    assert sl.is_auth_error(LitterRobotLoginException("bad"))
    assert not sl.is_auth_error(LitterRobotException("api down"))
    assert sl.is_auth_error(ClientResponseError(None, (), status=401))
    assert not sl.is_auth_error(ClientResponseError(None, (), status=503))
    assert not sl.is_auth_error(OSError("network down"))
    from pycognito.exceptions import ForceChangePasswordException

    assert sl.is_auth_error(ForceChangePasswordException("change it"))

    class FakeClientError(Exception):
        def __init__(self) -> None:
            super().__init__()
            self.response = {"Error": {"Code": "NotAuthorizedException"}}

    try:
        try:
            raise FakeClientError()
        except FakeClientError:
            raise KeyError("message")  # what a missing key in the library's handler looks like
    except KeyError as exc:
        assert sl.is_auth_error(exc)


# ---- the live logger with a fake robot ------------------------------------------------------


def test_spike_logger_writes_lines_and_console(tmp_path, capsys):
    import io

    out = io.StringIO()
    spike = sl.SpikeLogger(tmp_path, out=out)
    robot = FakeRobot()
    spike.robot = robot
    spike.observe("start")
    robot.set_status(LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT")
    spike.on_update()
    spike.close()
    lines = [json.loads(x) for x in spike.states.path.read_text().splitlines()]
    assert [x["source"] for x in lines] == ["start", "ws"]
    console = out.getvalue().splitlines()
    assert len(console) == 2 and "READY → CAT_DETECTED" in console[1] and "[ws]" in console[1]


def test_update_callback_never_raises(tmp_path, monkeypatch, capsys):
    spike = sl.SpikeLogger(tmp_path)
    spike.robot = FakeRobot()

    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(spike.recorder, "observe", explode)
    spike.on_update()  # must not raise
    spike.close()
    line = json.loads(spike.states.path.read_text().splitlines()[-1])
    assert line["kind"] == "error" and line["where"] == "update-callback"


async def test_poll_vs_push_source_tagging(tmp_path):
    spike = sl.SpikeLogger(tmp_path)
    robot = FakeRobot()
    spike.robot = robot
    spike.observe("start")
    sources = []
    original = spike.observe
    spike.observe = lambda source: (sources.append(source), original(source))[1]
    push_arrived = asyncio.Event()

    async def refresh():
        # a push lands (in another task) while our refresh() is awaiting the response
        await push_arrived.wait()
        robot.set_status(LitterBoxStatus.CLEAN_CYCLE, "ROBOT_CLEAN")
        spike.on_update()  # what _update_data's emit does inside refresh()

    async def push():
        await asyncio.sleep(0)
        robot._data["catDetect"] = "CAT_DETECT_LASER"
        spike.on_update()
        push_arrived.set()

    robot.refresh = refresh
    await asyncio.gather(spike.poll(), push())
    spike.close()
    assert sources[0] == "ws"  # the push, even though a poll was in flight
    assert sources[1] == "poll"  # emitted inside refresh()
    lines = [json.loads(x) for x in spike.states.path.read_text().splitlines()]
    assert [x["source"] for x in lines] == ["start", "ws", "poll"]


@dataclass
class Act:
    timestamp: datetime
    action: object


def test_activity_history_is_deduplicated(tmp_path):
    spike = sl.SpikeLogger(tmp_path)
    acts = [
        Act(T0, LitterBoxStatus.CAT_DETECTED),
        Act(T0 + timedelta(minutes=9), "Pet Weight Recorded: 9.5 lbs"),
    ]
    pet = SimpleNamespace(name="Arthur", weight_history=[SimpleNamespace(timestamp=T0, weight=9.5)])
    assert spike.record_activity(acts, [pet]) == 3
    assert spike.record_activity(acts, [pet]) == 0
    assert spike._activity_since == T0 + timedelta(minutes=9)
    spike.close()
    lines = [json.loads(x) for x in spike.activity.path.read_text().splitlines()]
    assert lines[0]["action"] == "Cat Detected" and lines[0]["code"] == "CD"
    assert lines[2] == {**lines[2], "kind": "pet_weight", "pet": "pet1", "weight_lbs": 9.5}
    assert "Arthur" not in spike.activity.path.read_text()


@pytest.mark.parametrize("seconds,text", [(5.04, "5.0s"), (61, "1m01s"), (3600, "1h00m00s")])
def test_format_duration(seconds, text):
    assert sl.format_duration(seconds) == text


# ---- the run loop, with connect_account replaced by fakes ------------------------------------


class FakeAccount:
    def __init__(self, robot) -> None:
        self.robot = robot
        self.pets = []
        self.disconnected = False

    def get_robots(self, cls):
        return [self.robot]

    async def disconnect(self):
        self.disconnected = True


class LiveFakeRobot(FakeRobot):
    def __init__(self) -> None:
        super().__init__()
        self.listeners = []
        self.refreshes = 0

    def on(self, event, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def push(self, status, robot_status):
        self.set_status(status, robot_status)
        for cb in list(self.listeners):
            cb()

    async def refresh(self):
        self.refreshes += 1
        for cb in list(self.listeners):
            cb()

    async def get_activity_history(self, limit=100, start=None):
        return [Act(T0, LitterBoxStatus.CLEAN_CYCLE_COMPLETE)]


def opts(tmp_path, duration):
    return sl.build_parser().parse_args(["--duration", str(duration), "--log-dir", str(tmp_path)])


async def test_run_logs_pushes_and_prints_summary(tmp_path, monkeypatch):
    import io

    robot = LiveFakeRobot()
    account = FakeAccount(robot)

    async def fake_connect(username, password, token_path):
        async def script():
            for status, raw in [
                (LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT"),
                (LitterBoxStatus.CAT_SENSOR_TIMING, "ROBOT_CAT_DETECT_DELAY"),
                (LitterBoxStatus.CLEAN_CYCLE, "ROBOT_CLEAN"),
                (LitterBoxStatus.READY, "ROBOT_IDLE"),
            ]:
                await asyncio.sleep(0.05)
                robot.push(status, raw)

        asyncio.get_running_loop().call_later(0.01, lambda: asyncio.ensure_future(script()))
        return account, robot

    monkeypatch.setattr(sl, "connect_account", fake_connect)
    out = io.StringIO()
    code = await sl.run(opts(tmp_path, 0.5), "u", "p", out=out, token_path=tmp_path / "t.json")
    assert code == 0 and account.disconnected and robot.listeners == []
    states = [json.loads(x) for x in next(tmp_path.glob("spike-2*.jsonl")).read_text().splitlines()]
    sources = [x["source"] for x in states if x["kind"] == "state"]
    assert sources == ["start", "ws", "ws", "ws", "ws"]
    activity = next(tmp_path.glob("spike-activity-*.jsonl")).read_text()
    assert "Clean Cycle Complete" in activity
    text = out.getvalue()
    assert "CLEAN_CYCLE → READY" in text and "Complete visits" in text
    assert "Complete visits (CAT_DETECTED → CAT_SENSOR_TIMING → CLEAN_CYCLE → READY): 1" in text


async def test_run_exits_3_on_rejected_login(tmp_path, monkeypatch, capsys):
    async def fake_connect(*a):
        raise sl.AuthFailed("nope")

    monkeypatch.setattr(sl, "connect_account", fake_connect)
    code = await sl.run(opts(tmp_path, 5), "u", "p", token_path=tmp_path / "t.json")
    assert code == sl.EXIT_AUTH
    assert "Login rejected" in capsys.readouterr().err


async def test_run_retries_network_errors_with_backoff(tmp_path, monkeypatch, capsys):
    calls = []

    async def fake_connect(*a):
        calls.append(1)
        raise OSError("network down")

    monkeypatch.setattr(sl, "connect_account", fake_connect)
    code = await sl.run(opts(tmp_path, 0.3), "u", "p", token_path=tmp_path / "t.json")
    assert code == 0 and len(calls) == 1  # waiting 30 s for the retry when stop arrived
    lines = [json.loads(x) for x in next(tmp_path.glob("spike-2*.jsonl")).read_text().splitlines()]
    assert lines[0]["kind"] == "error" and lines[0]["retry_in"] == 30


async def test_connect_falls_back_to_password_when_cached_token_rejected(tmp_path, monkeypatch):
    import pylitterbot
    from pylitterbot.exceptions import LitterRobotLoginException

    tokens_seen = []

    class FakeAcct:
        def __init__(self, token=None, token_update_callback=None, robot_types=None):
            self.token = token
            self.cb = token_update_callback
            tokens_seen.append(token)

        async def connect(self, **kwargs):
            assert kwargs["load_robots"] and kwargs["subscribe_for_updates"] and kwargs["load_pets"]
            if self.token is not None:
                raise LitterRobotLoginException("token rejected")
            self.cb({"access_token": "a2", "id_token": "i2", "refresh_token": "r2"})

        def get_robots(self, cls):
            return ["robot"]

        async def disconnect(self):
            pass

    monkeypatch.setattr(pylitterbot, "Account", FakeAcct)
    path = tmp_path / "tok.json"
    sl.save_token(path, {"access_token": "a", "id_token": "i", "refresh_token": "r"})
    _account, robot = await sl.connect_account("u", "p", path)
    assert robot == "robot"
    assert tokens_seen[0]["access_token"] == "a" and tokens_seen[1] is None
    assert sl.load_token(path)["access_token"] == "a2"


async def test_connect_without_lr4_is_an_error(tmp_path, monkeypatch):
    import pylitterbot

    class EmptyAcct:
        def __init__(self, **kwargs):
            pass

        async def connect(self, **kwargs):
            pass

        def get_robots(self, cls):
            return []

        async def disconnect(self):
            pass

    monkeypatch.setattr(pylitterbot, "Account", EmptyAcct)
    with pytest.raises(sl.NoRobotFound):
        await sl.connect_account("u", "p", tmp_path / "none.json")


async def test_corrupt_token_cache_falls_back_to_password(tmp_path, monkeypatch):
    import pylitterbot

    tokens_seen = []

    class Acct:
        def __init__(self, token=None, token_update_callback=None, robot_types=None):
            tokens_seen.append(token)
            if token is not None:
                import jwt

                jwt.decode("not-a-jwt", options={"verify_signature": False})

        async def connect(self, **kwargs):
            pass

        def get_robots(self, cls):
            return ["robot"]

        async def disconnect(self):
            pass

    monkeypatch.setattr(pylitterbot, "Account", Acct)
    path = tmp_path / "tok.json"
    sl.save_token(path, {"access_token": "x", "id_token": "y", "refresh_token": "z"})
    _account, robot = await sl.connect_account("u", "p", path)
    assert robot == "robot" and tokens_seen[-1] is None
