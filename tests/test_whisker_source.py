"""The live Whisker source with fake robots and fake time. No network, no real Whisker."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import timedelta

from pylitterbot.enums import LitterBoxStatus
from pylitterbot.exceptions import LitterRobotLoginException
from robotfakes import T0, Act, FakeAccount, LiveFakeRobot

from arthur import spikelog as sl
from arthur.config import default_config
from arthur.feeder import setup_logging
from arthur.mapping import to_display_state
from arthur.sources.logfile import RobotLog, follow_view
from arthur.sources.whisker import WhiskerSource, latest_weight_since, seeds_from_activity

CFG = default_config()
START = T0 + timedelta(hours=1)  # the fake wall clock's zero
USER, PASSWORD = "kyle-user@example.com", "hunter2-secret"
JWT = "eyJhbGciOi.eyJzdWIi.sig"


class FakeTime:
    """Monotonic and wall time that move only when the source sleeps. Events scheduled with
    :meth:`at` run at their time, inside the sleep that passes it."""

    def __init__(self) -> None:
        self.t = 0.0
        self.stop_at: float | None = None
        self.events: list[tuple[float, int, object]] = []
        self._n = 0

    def mono(self) -> float:
        return self.t

    def wall(self, t: float | None = None):
        return START + timedelta(seconds=self.t if t is None else t)

    def at(self, t: float, fn) -> None:
        self._n += 1
        self.events.append((t, self._n, fn))
        self.events.sort(key=lambda e: (e[0], e[1]))

    async def sleep(self, stop: asyncio.Event, seconds: float) -> bool:
        end = self.t + seconds
        while self.events and self.events[0][0] <= end and not stop.is_set():
            at, _, fn = self.events.pop(0)
            self.t = max(self.t, at)
            fn()
        if not stop.is_set():
            self.t = end
        if self.stop_at is not None and self.t >= self.stop_at:
            stop.set()
        await asyncio.sleep(0)
        return stop.is_set()


def make_source(tmp_path, clock: FakeTime, connect, **kw) -> WhiskerSource:
    return WhiskerSource(
        CFG, credentials=kw.pop("credentials", (USER, PASSWORD)), log_dir=tmp_path / "logs",
        token_path=tmp_path / "token.json", connect=connect, wall_fn=clock.wall,
        mono_fn=clock.mono, sleep=clock.sleep, **kw,
    )


async def drive(source: WhiskerSource, clock: FakeTime, until: float) -> None:
    clock.stop_at = until
    await asyncio.wait_for(source.run(asyncio.Event()), timeout=10)


def mapped(source: WhiskerSource, clock: FakeTime):
    now = clock.wall()
    snap, health = source.view(now)
    return to_display_state(snap, health, now, CFG)


def connect_to(robot, account=None, calls=None):
    account = account or FakeAccount(robot)

    async def connect(username, password, token_path):
        if calls is not None:
            calls.append(username)
        return account, robot

    return connect


def push(robot: LiveFakeRobot, status: LitterBoxStatus, raw: str, **attrs):
    def go():
        for k, v in attrs.items():
            setattr(robot, k, v)
        robot.push(status, raw)

    return go


def state_lines(tmp_path) -> list[dict]:
    out = []
    for f in sorted((tmp_path / "logs").glob("spike-2*.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


def visit_script(clock: FakeTime, robot: LiveFakeRobot) -> None:
    """Detected at 100 s, the weight changes at 160 s (still CAT_DETECTED), clean at 640 s,
    ready at 770 s."""
    clock.at(100, push(robot, LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT"))
    clock.at(106, push(robot, LitterBoxStatus.CAT_SENSOR_TIMING, "ROBOT_CAT_DETECT_DELAY"))
    clock.at(160, push(robot, LitterBoxStatus.CAT_SENSOR_TIMING, "ROBOT_CAT_DETECT_DELAY",
                       pet_weight=9.7))
    clock.at(640, push(robot, LitterBoxStatus.CLEAN_CYCLE, "ROBOT_CLEAN"))
    clock.at(770, push(robot, LitterBoxStatus.READY, "ROBOT_IDLE"))


# ---- a visit ---------------------------------------------------------------------------------


async def test_visit_in_use_then_waiting_then_cleaning(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    robot.clean_cycle_wait_time_minutes = 8
    source = make_source(tmp_path, clock, connect_to(robot))
    visit_script(clock, robot)
    seen = {}

    def look(name):
        return lambda: seen.__setitem__(name, (mapped(source, clock), source.view(clock.wall())))

    for t, name in [(50, "before"), (130, "in_use"), (200, "waiting"), (700, "cleaning"),
                    (772, "just_cleaned"), (800, "after")]:
        clock.at(t, look(name))
    await drive(source, clock, 900)

    assert seen["before"][0].kind == "ready"
    state, (snap, health) = seen["in_use"]
    assert state.kind == "in_use" and state.since == clock.wall(100)
    assert snap.status == "CAT_SENSOR_TIMING" and snap.status_since == clock.wall(106)
    assert health.error is None and health.last_contact_at == clock.wall(106)
    state = seen["waiting"][0]
    assert state.kind == "waiting" and state.since == clock.wall(160)
    assert state.deadline == clock.wall(160) + timedelta(minutes=8)
    state = seen["cleaning"][0]
    assert state.kind == "cleaning" and state.cycle_start == clock.wall(640)
    assert seen["just_cleaned"][0].kind == "just_cleaned"
    after = seen["after"][0]
    assert after.kind == "ready" and after.last_visit_at == clock.wall(100)
    # Polls happen every poll_s and count as contact.
    assert robot.refreshes == 2  # at 300 and 600 s (it stops at 900 s)
    assert source.fallback_fetches == 0  # the weight arrived


async def test_log_lines_round_trip_through_logfile(tmp_path):
    """What the source writes, read back by the studio's Robot log source, maps the same."""
    clock, robot = FakeTime(), LiveFakeRobot()
    source = make_source(tmp_path, clock, connect_to(robot))
    visit_script(clock, robot)
    rlog = RobotLog(tmp_path / "logs", just_cleaned_s=CFG.timings.just_cleaned_s,
                    visit_min_s=CFG.timings.visit_min_s)
    pairs = []

    def compare():
        rlog.refresh()
        now = clock.wall()
        view = follow_view(rlog.timeline, now)
        theirs = to_display_state(view.snapshot, view.health, now, CFG)
        pairs.append((clock.t, mapped(source, clock), theirs))

    for t in (50, 101, 130, 161, 200, 645, 700, 771, 800, 1000):
        clock.at(t, compare)
    await drive(source, clock, 1000)

    kinds = [(t, ours.kind, theirs.kind) for t, ours, theirs in pairs]
    assert [k[1] for k in kinds] == [k[2] for k in kinds], kinds
    assert {k[1] for k in kinds} >= {"ready", "in_use", "waiting", "cleaning", "just_cleaned"}
    for _, ours, theirs in pairs:
        assert ours.since == theirs.since and ours.deadline == theirs.deadline

    lines = state_lines(tmp_path)
    states = [x for x in lines if x["kind"] == "state"]
    assert states[0]["source"] == "start" and "raw" in states[0]
    assert [x["status"] for x in states if "status" in x["changed"]] == [
        "CAT_DETECTED", "CAT_SENSOR_TIMING", "CLEAN_CYCLE", "READY"]
    assert all("raw" in x for x in states if "status" in x["changed"])
    infos = [x["event"] for x in lines if x["kind"] == "info"]
    assert infos[0] == "connected" and infos[-1] == "exit"
    activity = next((tmp_path / "logs").glob("spike-activity-*.jsonl")).read_text()
    assert "Clean Cycle Complete" in activity


async def test_heartbeat_line_every_15_minutes_and_not_a_contact(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    source = make_source(tmp_path, clock, connect_to(robot))
    await drive(source, clock, 1000)
    beats = [x for x in state_lines(tmp_path) if x.get("source") == "heartbeat"]
    assert len(beats) == 1 and beats[0]["ts"] == sl.utc_iso_ms(clock.wall(900))
    # The last contact was the poll at 900 s (polls every 300 s), not the heartbeat.
    assert source.last_contact_at == clock.wall(900)


async def test_silence_turns_into_no_data_after_no_data_after_s(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    source = make_source(tmp_path, clock, connect_to(robot))
    await drive(source, clock, 5)
    last = source.last_contact_at
    assert last == clock.wall(0)
    limit = CFG.timings.no_data_after_s
    snap, health = source.view(last + timedelta(seconds=limit - 1))
    assert to_display_state(snap, health, last + timedelta(seconds=limit - 1), CFG).kind == "ready"
    later = last + timedelta(seconds=limit + 1)
    state = to_display_state(*source.view(later), later, CFG)
    assert state.kind == "no_data" and state.detail == "timeout" and state.last_seen == last


# ---- seeding and the fallback fetch -------------------------------------------------------


def test_seeds_from_activity():
    cd, w1, w2 = START - timedelta(minutes=3), START - timedelta(minutes=2), START
    acts = [Act(cd - timedelta(hours=5), LitterBoxStatus.CAT_DETECTED),
            Act(cd, LitterBoxStatus.CAT_DETECTED),
            Act(w1, "Pet Weight Recorded: 9.5 lbs"), Act(w2, "Pet Weight Recorded: 9.6 lbs")]
    assert seeds_from_activity(acts, mid_visit=False) == {"last_visit_at": cd}
    assert seeds_from_activity(acts, mid_visit=True) == {
        "last_visit_at": cd, "visit_started_at": cd, "cat_left_at": w2}
    # A clean cycle after the newest detection: the visit under way is not in the history yet.
    cleaned = [*acts, Act(START + timedelta(seconds=5), LitterBoxStatus.CLEAN_CYCLE)]
    assert seeds_from_activity(cleaned, mid_visit=True) == {"last_visit_at": cd}
    assert seeds_from_activity([], mid_visit=True) == {}
    assert latest_weight_since(acts, START + timedelta(seconds=1)) is None


async def test_seeded_last_visit_at_start(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    earlier = START - timedelta(hours=3)
    robot.activities = [Act(earlier, LitterBoxStatus.CAT_DETECTED),
                        Act(earlier + timedelta(minutes=1), "Pet Weight Recorded: 9.5 lbs")]
    source = make_source(tmp_path, clock, connect_to(robot))
    await drive(source, clock, 5)
    assert mapped(source, clock).last_visit_at == earlier
    # The first fetch asks for a bounded window, not the API's default.
    limit, start = robot.activity_calls[0]
    assert limit == 100 and start == clock.wall(0) - timedelta(hours=72)


async def test_started_mid_visit_after_the_cat_left(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    robot.set_status(LitterBoxStatus.CAT_SENSOR_TIMING, "ROBOT_CAT_DETECT_DELAY")
    cd, weighed = START - timedelta(seconds=200), START - timedelta(seconds=140)
    robot.activities = [Act(cd, LitterBoxStatus.CAT_DETECTED),
                        Act(weighed, "Pet Weight Recorded: 9.5 lbs")]
    source = make_source(tmp_path, clock, connect_to(robot))
    await drive(source, clock, 5)
    state = mapped(source, clock)
    assert state.kind == "waiting" and state.since == weighed
    assert state.deadline == weighed + timedelta(minutes=robot.clean_cycle_wait_time_minutes)
    assert state.last_visit_at == cd
    seeded = [x for x in state_lines(tmp_path) if x.get("event") == "seeded"]
    assert seeded and seeded[0]["visit_started_at"] == sl.utc_iso_ms(cd)


async def test_started_mid_visit_without_weight_then_fallback_fetch_once(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    robot.set_status(LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT")
    cd = START - timedelta(seconds=50)
    robot.activities = [Act(cd, LitterBoxStatus.CAT_DETECTED)]
    source = make_source(tmp_path, clock, connect_to(robot))
    kinds = {}
    clock.at(10, lambda: kinds.__setitem__("start", mapped(source, clock)))
    # The weight reaches the history at 30 s, but no push carries it (same weight as before).
    weighed = START + timedelta(seconds=25)
    clock.at(30, lambda: robot.activities.append(Act(weighed, "Pet Weight Recorded: 9.5 lbs")))
    clock.at(100, lambda: kinds.__setitem__("later", mapped(source, clock)))
    await drive(source, clock, 2000)

    assert kinds["start"].kind == "in_use" and kinds["start"].since == cd
    # 120 s after the visit's start (at 70 s) one fetch found the weight.
    later = kinds["later"]
    assert later.kind == "waiting" and later.since == weighed
    assert later.deadline == weighed + timedelta(minutes=7)
    fallback_calls = [c for c in robot.activity_calls if c[0] == 20]
    assert fallback_calls == [(20, cd - timedelta(seconds=30))]
    assert source.fallback_fetches == 1
    assert any(x.get("event") == "cat_left_from_history" for x in state_lines(tmp_path))


async def test_fallback_fetch_finds_nothing_and_is_not_repeated(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    source = make_source(tmp_path, clock, connect_to(robot))
    clock.at(100, push(robot, LitterBoxStatus.CAT_DETECTED, "ROBOT_CAT_DETECT"))
    seen = {}
    clock.at(150, lambda: seen.__setitem__("a", mapped(source, clock)))
    clock.at(400, lambda: seen.__setitem__("b", mapped(source, clock)))
    await drive(source, clock, 1500)
    assert seen["a"].kind == "in_use"
    assert seen["b"].kind == "waiting" and seen["b"].deadline is None  # the mapping's fallback
    assert [c for c in robot.activity_calls if c[0] == 20] == [
        (20, clock.wall(100) - timedelta(seconds=30))]


# ---- failures ------------------------------------------------------------------------------


async def test_rejected_login_is_auth_and_retried_slowly(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    calls = []
    real = connect_to(robot)

    async def connect(username, password, token_path):
        calls.append(clock.t)
        if len(calls) <= 2:
            raise sl.AuthFailed("rejected")
        return await real(username, password, token_path)

    source = make_source(tmp_path, clock, connect)
    seen = {}
    clock.at(10, lambda: seen.__setitem__("a", mapped(source, clock)))
    await drive(source, clock, 1810)
    assert calls == [0, 600, 1800]  # 600 s, then 1200 s
    assert seen["a"].kind == "no_data" and seen["a"].detail == "auth"
    assert source.error is None and mapped(source, clock).kind == "ready"
    errors = [x for x in state_lines(tmp_path) if x["kind"] == "error"]
    assert [(e["where"], e["retry_in"]) for e in errors] == [("login", 600), ("login", 1200)]


def test_auth_backoff_caps_at_an_hour():
    b = sl.Backoff(600, 3600)
    assert [b.next() for _ in range(5)] == [600, 1200, 2400, 3600, 3600]


async def test_missing_credentials_are_auth_and_never_connect(tmp_path, caplog):
    clock = FakeTime()

    async def connect(*a):
        raise AssertionError("must not connect")

    source = make_source(tmp_path, clock, connect, credentials=None)
    stop = asyncio.Event()
    task = asyncio.create_task(source.run(stop))
    await asyncio.sleep(0.01)
    snap, health = source.view(clock.wall())
    assert snap is None and health.error == "auth"
    assert mapped(source, clock).detail == "auth"
    stop.set()
    await asyncio.wait_for(task, 1)
    assert caplog.text.count("credentials are not set") == 1
    errors = [x for x in state_lines(tmp_path) if x["kind"] == "error"]
    assert len(errors) == 1 and errors[0]["where"] == "login" and errors[0]["retry_in"] is None


async def test_connect_network_error_is_api_with_backoff(tmp_path):
    clock, robot = FakeTime(), LiveFakeRobot()
    calls = []
    real = connect_to(robot)

    async def connect(*a):
        calls.append(clock.t)
        if len(calls) <= 2:
            raise OSError("network down")
        return await real(*a)

    source = make_source(tmp_path, clock, connect)
    seen = {}
    clock.at(5, lambda: seen.__setitem__("a", mapped(source, clock)))
    await drive(source, clock, 100)
    assert calls == [0, 30, 90]
    assert seen["a"].detail == "api"
    assert source.error is None


async def test_no_robots_is_its_own_error_and_reads_back(tmp_path):
    clock = FakeTime()
    calls = []

    async def connect(*a):
        calls.append(clock.t)
        raise sl.NoRobotFound("none")

    source = make_source(tmp_path, clock, connect)
    await drive(source, clock, 100)
    assert calls == [0, 30, 90]
    assert mapped(source, clock).detail == "no_robots"
    rlog = RobotLog(tmp_path / "logs")
    rlog.refresh()
    assert rlog.timeline.contact_error() == "no_robots"


class FlakyRobot(LiveFakeRobot):
    def __init__(self, failures) -> None:
        super().__init__()
        self.failures = list(failures)

    async def refresh(self):
        self.refreshes += 1
        if self.failures:
            raise self.failures.pop(0)
        await super().refresh()
        self.refreshes -= 1


async def test_poll_api_failure_then_recovery(tmp_path):
    clock = FakeTime()
    robot = FlakyRobot([OSError("timeout")])
    source = make_source(tmp_path, clock, connect_to(robot))
    seen = {}
    clock.at(310, lambda: seen.__setitem__("failed", mapped(source, clock)))
    clock.at(430, lambda: seen.__setitem__("back", mapped(source, clock)))
    await drive(source, clock, 450)
    assert seen["failed"].kind == "no_data" and seen["failed"].detail == "api"
    assert seen["back"].kind == "ready"  # the retry at 420 s (no sooner than 120 s) worked
    errors = [x for x in state_lines(tmp_path) if x["kind"] == "error"]
    assert [(e["where"], e["retry_in"]) for e in errors] == [("poll", 120)]


async def test_poll_auth_error_drops_token_and_logs_in_again(tmp_path):
    clock = FakeTime()
    robot = FlakyRobot([LitterRobotLoginException("401")])
    calls = []
    source = make_source(tmp_path, clock, connect_to(robot, calls=calls))
    sl.save_token(source.token_path, {"access_token": "a", "id_token": "i"})
    await drive(source, clock, 310)
    assert len(calls) == 2  # logged in again straight away
    assert not source.token_path.exists()
    assert source.error is None
    errors = [x for x in state_lines(tmp_path) if x["kind"] == "error"]
    assert [(e["where"], e["retry_in"]) for e in errors] == [("poll", None)]


# ---- no secrets in any output --------------------------------------------------------------


async def test_credentials_and_tokens_never_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    clock, robot = FakeTime(), LiveFakeRobot()
    real = connect_to(robot)
    calls = []

    async def connect(username, password, token_path):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(f"403 wss://x/realtime?header={JWT}&payload=e30= Bearer abc.def-ghi")
        return await real(username, password, token_path)

    source = make_source(tmp_path, clock, connect)
    visit_script(clock, robot)
    await drive(source, clock, 900)

    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, logging.getLogger("arthur").level)
    try:
        setup_logging(stream)
        logging.getLogger("pylitterbot.transport").warning(
            "WebSocket error (%s)", f"wss://x/realtime?header={JWT}&payload=e30=")
        logging.getLogger("arthur.whisker").warning("token %s", JWT)
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        logging.getLogger("arthur").setLevel(saved[2])

    files = "".join(p.read_text() for p in (tmp_path / "logs").iterdir())
    for text in (files, caplog.text, stream.getvalue()):
        for secret in (USER, PASSWORD, "eyJzdWIi", "abc.def-ghi", "LR4C000001", "UNIT-123"):
            assert secret not in text, secret
    assert "WebSocket error" in stream.getvalue() and sl.REDACTED in stream.getvalue()
