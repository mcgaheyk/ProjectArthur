"""``WireSource`` (our own client) against ``WhiskerSource`` (pylitterbot), on the same fake.

Each scenario in ``tests/wire_scenarios.py`` is played twice against a fresh fake Whisker on
127.0.0.1 with the same made-up time: once through the feeder's pylitterbot source (pylitterbot
and pycognito themselves, pointed at the fake) and once through ``WireSource``. Every
``view()`` sampled along the way, the log lines written and the console lines must agree,
except for the differences listed in :data:`KNOWN`, each with its reason (and the test checks
those differences are exactly what is listed).

What may differ in every scenario, and why:

- an error line's ``error`` and ``message``: the exception classes and texts are the two
  libraries' own (the studio reads only ``where``, ``retry_in`` and ``NoRobotFound``, which
  must agree);
- the console's text after "failed [kind]:" for the same reason, and our client's two
  extra lines about the live subscription ("Whisker live updates subscribed", "Whisker live
  connection closed: …"), which pylitterbot does not log.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from pathlib import Path

import pytest
from pylitterbot import Account
from pylitterbot.robot import litterrobot4
from wire_scenarios import SCENARIOS
from wire_source_driver import Clock, Console, Counter, ListSink
from wirefake import FakeWhisker, activity_entry, jwks

from arthur import spikelog as sl
from arthur.config import default_config
from arthur.feeder import build_parser
from arthur.sources.logfile import RobotLog
from arthur.sources.whisker import WhiskerSource
from arthur.sources.wire_source import WireSource
from arthur.wire.net import Tls

pytestmark = [
    pytest.mark.usefixtures("loopback_only"),
    pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow:DeprecationWarning"),
]

CFG = default_config()
PASSWORD = "hunter2-secret"


# =============================================================================================
# The fake's side
# =============================================================================================


def entry(sc, e):
    t, value, action_value = e
    return activity_entry(sc["start_us"] / 1e6 + t, value, action_value)


async def until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        await asyncio.sleep(0.005)
    return True


async def start_fake(sc) -> FakeWhisker:
    fake = FakeWhisker(ka_s=0.1)
    fake.robots = copy.deepcopy(sc["robots"])
    fake.activities = [entry(sc, e) for e in sc["history"]]
    fake.reject_refresh = sc["reject_refresh"]
    if sc["password"] == "wrong":
        fake.password = "not-" + PASSWORD
    await fake.start()
    return fake


async def perform(fake: FakeWhisker, sc: dict, ev: dict) -> None:
    """Carry out one scripted event at the fake."""
    do = ev["do"]
    if do == "push":
        if await until(lambda: bool(fake.subscribers), 3.0):
            await fake.push(ev["update"], partial=ev.get("partial", False))
    elif do == "set":
        fake.robots[0].update(ev["update"])
    elif do == "history":
        fake.activities += [entry(sc, e) for e in ev["add"]]
    elif do == "revoke":
        fake.revoke()
    elif do == "drop":
        n = fake.counts["ws"]
        await fake.drop_sockets()
        await until(lambda: fake.counts["ws"] > n and bool(fake.subscribers))
    elif do == "silent":
        n = fake.counts["ws"]
        fake.silent = True
        await until(lambda: fake.counts["ws"] > n)
        fake.silent = False
        await until(lambda: bool(fake.subscribers))
    elif do == "fail":
        fake.fail[ev["op"]].append(tuple(ev["action"]))
    elif do == "password":
        fake.password = PASSWORD
    elif do == "robots":
        fake.robots = copy.deepcopy(ev["robots"])
    else:
        raise ValueError(do)
    fake.step += 1


def write_token(sc, fake, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sc["token"] == "fresh":
        sl.save_token(path, fake.issue_tokens())
    elif sc["token"] == "expired":
        sl.save_token(path, fake.issue_tokens(ttl=-60))


# =============================================================================================
# The two runs
# =============================================================================================


class Trace:
    def __init__(self, probes, notes, states, activities, console, token, fake_counts):
        self.probes = probes
        self.notes = notes
        self.states = states
        self.activities = activities
        self.console = console
        self.token = token
        self.fake_counts = fake_counts


def _json(obj):
    return json.loads(json.dumps(obj, default=str))


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append([record.levelname.lower(), record.getMessage()])


@pytest.fixture
def pointed_at_fake(monkeypatch, tmp_path):
    """pylitterbot and pycognito pointed at the fake: the LR4 endpoint, boto3's Cognito
    endpoint and the JWKS. Pets are not loaded (our client has none; the fake has none)."""

    def point(fake):
        monkeypatch.setattr(litterrobot4, "LR4_ENDPOINT", fake.url + "/graphql")
        monkeypatch.setenv("AWS_ENDPOINT_URL_COGNITO_IDENTITY_PROVIDER", fake.url)
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
        monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
        monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
        monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
        monkeypatch.setenv("COGNITO_JWKS", json.dumps(jwks()))

    async def no_pets(self):
        return None

    monkeypatch.setattr(Account, "load_pets", no_pets)
    monkeypatch.setattr(litterrobot4.LitterRobot4._WS_PROTOCOL, "stale_timeout", 0.5)
    return point


async def run_pylitterbot(sc, tmp_path: Path, point) -> Trace:
    fake = await start_fake(sc)
    point(fake)
    token = tmp_path / "pl" / "token.json"
    write_token(sc, fake, token)
    counter = Counter()
    src = None

    async def ask(i, ev):
        await perform(fake, sc, ev)

    clock = Clock(sc["start_us"], sc["events"], sc["stop_at"], ask, counter,
                  lambda now: src.view(now))
    src = WhiskerSource(CFG, credentials=(fake.username, PASSWORD), log_dir=tmp_path / "pl",
                        token_path=token, wall_fn=clock.wall, mono_fn=clock.mono,
                        sleep=clock.sleep)
    src.logger.on_record = counter.wrap(src.logger.on_record)
    handler = _Collect()
    logger = logging.getLogger("arthur.whisker")
    old = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        await asyncio.wait_for(src.run(asyncio.Event()), 120)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
        await fake.stop()
    states, acts = [], []
    for f in sorted((tmp_path / "pl").glob("spike-2*.jsonl")):
        states += [json.loads(x) for x in f.read_text().splitlines()]
    for f in sorted((tmp_path / "pl").glob("spike-activity-*.jsonl")):
        acts += [json.loads(x) for x in f.read_text().splitlines()]
    return Trace(clock.probes, clock.notes, states, acts, handler.lines,
                 sl.load_token(token), dict(fake.counts))


def make_wire(sc, fake, clock, tmp_path: Path, **kw) -> WireSource:
    return WireSource(
        CFG, credentials=(fake.username, PASSWORD), tls=Tls(allow_plain=True),
        sink=kw.pop("sink", ListSink()), token_path=tmp_path / "token.json",
        wall_fn=clock.wall, mono_fn=clock.mono, sleep=clock.sleep,
        say=kw.pop("say", Console()),
        client_kw={"lr4_url": fake.url + "/graphql", "cognito_url": fake.url + "/",
                   "stale_s": 0.5, "ws_backoff": (0.05, 0.2), "timeout": 5.0}, **kw)


async def run_wire(sc, tmp_path: Path, **kw) -> Trace:
    fake = await start_fake(sc)
    write_token(sc, fake, tmp_path / "wire" / "token.json")
    counter = Counter()
    src = None

    async def ask(i, ev):
        await perform(fake, sc, ev)

    clock = Clock(sc["start_us"], sc["events"], sc["stop_at"], ask, counter,
                  lambda now: src.view(now))
    console = Console()
    src = make_wire(sc, fake, clock, tmp_path / "wire", say=console, **kw)
    src.monitor.on_record = counter.wrap(src.monitor.on_record)
    try:
        await asyncio.wait_for(src.run(asyncio.Event()), 120)
    finally:
        await fake.stop()
    return Trace(clock.probes, clock.notes, _json(src.sink.states), _json(src.sink.activities),
                 console.lines, sl.load_token(tmp_path / "wire" / "token.json"),
                 dict(fake.counts))


# =============================================================================================
# Comparing
# =============================================================================================

#: Console lines only our client writes (pylitterbot keeps the socket's life to itself).
OURS_ONLY = ("Whisker live updates subscribed", "Whisker live connection closed")


def norm_states(lines):
    out = []
    for x in lines:
        if x.get("kind") == "error":
            x = {k: v for k, v in x.items() if k not in ("error", "message")}
        out.append(x)
    return out


def norm_console(lines):
    out = []
    for level, text in lines:
        if text.startswith(OURS_ONLY):
            continue
        if " failed [" in text:
            text = text[:text.index("]:") + 1]
        out.append([level, text])
    return out


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return i, x, y
    return len(a), len(a), len(b)


def compare(theirs: Trace, ours: Trace):
    """The differences, as a dict of what differs (empty when the two agree)."""
    out = {}
    if theirs.notes or ours.notes:
        out["notes"] = (theirs.notes, ours.notes)
    if theirs.probes != ours.probes:
        out["probes"] = first_diff(theirs.probes, ours.probes)
    a, b = norm_states(theirs.states), norm_states(ours.states)
    if a != b:
        out["states"] = first_diff(a, b)
    if theirs.activities != ours.activities:
        out["activities"] = first_diff(theirs.activities, ours.activities)
    a, b = norm_console(theirs.console), norm_console(ours.console)
    if a != b:
        out["console"] = first_diff(a, b)
    if (theirs.token is None) != (ours.token is None):
        out["token"] = (theirs.token is None, ours.token is None)
    return out


#: Scenarios where the two differ on purpose. Each is checked by its own test below.
KNOWN = {"poll_401", "two_robots"}


def _us(sc, t):
    return sc["start_us"] + round(t * 1_000_000)


def _said(trace, text):
    return any(text in line for _, line in trace.console)


def _events(trace, kind, key):
    return [x.get(key) for x in trace.states if x["kind"] == kind]


#: What each scenario must show, beyond the two sources agreeing (so they cannot agree by
#: both doing nothing).
CHECKS = {
    "gap_missed_visit": lambda sc, tr: (
        _said(tr, "Not watching from") and _said(tr, "Last visit moved from")
        and tr.probes[-1]["snap"]["last_visit_at"] == {"us": _us(sc, 1500)}),
    "gap_mid_visit": lambda sc, tr: (
        _said(tr, "Not watching from")
        and any(p["snap"]["cat_left_at"] == {"us": _us(sc, 4290)} for p in tr.probes)),
    "clean_no_visit": lambda sc, tr: _said(tr, "Clean cycle with no visit seen"),
    "real_visits": lambda sc, tr: _said(tr, "Clean cycle with no visit seen")
    and len([s for s in _events(tr, "state", "status") if s == "CAT_DETECTED"]) >= 3,
    "no_weight": lambda sc, tr: _events(tr, "info", "event").count(
        "cat_left_from_history") == 1,
    "login_rejected": lambda sc, tr: tr.probes[1]["health"]["error"] == "auth"
    and tr.probes[-1]["health"]["error"] is None and tr.fake_counts["login"] == 3,
    "no_robots": lambda sc, tr: tr.probes[1]["health"]["error"] == "no_robots"
    and tr.probes[-1]["snap"] is not None,
    "poll_500": lambda sc, tr: [(x["where"], x["retry_in"]) for x in tr.states
                                if x["kind"] == "error"] == [("poll", 120.0)],
    "rejected_refresh": lambda sc, tr: tr.fake_counts["login"] == 1,
    "drawer_full": lambda sc, tr: "DRAWER_FULL" in _events(tr, "state", "status"),
    "fault": lambda sc, tr: {"PINCH_DETECT", "PAUSED"} <= set(_events(tr, "state", "status")),
    "offline": lambda sc, tr: {"OFFLINE", "OFF"} <= set(_events(tr, "state", "status")),
    "sleeping": lambda sc, tr: any(p["snap"]["is_sleeping"] for p in tr.probes),
    "dropped_socket": lambda sc, tr: tr.fake_counts["ws"] == 2,
    "stale_socket": lambda sc, tr: tr.fake_counts["ws"] == 2,
    "partial_update": lambda sc, tr: any(p["snap"]["cat_left_at"] == {"us": _us(sc, 160)}
                                         for p in tr.probes),
    "watched_visits": lambda sc, tr: tr.fake_counts["graphql"] >= 2,
    "interrupted_clean": lambda sc, tr: (
        "CAT_SENSOR_INTERRUPTED" in _events(tr, "state", "status")
        and any(p["snap"]["status"] == "CLEAN_CYCLE"
                and p["snap"]["previous_status"] == "CAT_SENSOR_INTERRUPTED"
                and abs(float(p["snap"]["cycled_s"]["f"]) - 40.1) < 0.01 for p in tr.probes)
        and tr.probes[-1]["snap"]["cycled_s"] == {"f": "0.0"}),
}


@pytest.mark.parametrize("name", sorted(set(SCENARIOS) - KNOWN - {"an_hour"}))
async def test_same_views_lines_and_console(name, tmp_path, pointed_at_fake):
    sc = SCENARIOS[name]()
    theirs = await run_pylitterbot(sc, tmp_path, pointed_at_fake)
    ours = await run_wire(sc, tmp_path)
    assert compare(theirs, ours) == {}
    assert theirs.probes, "no view was sampled"
    assert CHECKS[name](sc, ours), name
    print(f"{name}: {len(ours.probes)} views, {len(ours.states)} lines, "
          f"{len(ours.activities)} history entries, console {len(ours.console)} lines")


async def test_poll_401_renews_instead_of_logging_in(tmp_path, pointed_at_fake):
    """pylitterbot never renews on a 401: the feeder's source drops the token and logs in
    with the password (an error line, a fresh 'connected' and a history read). Our client
    renews the id token once and retries the poll, which then succeeds: no error, no login.
    The screen is the same either way (the source reconnects in the same tick)."""
    sc = SCENARIOS["poll_401"]()
    theirs = await run_pylitterbot(sc, tmp_path, pointed_at_fake)
    ours = await run_wire(sc, tmp_path)
    assert theirs.probes == ours.probes
    assert theirs.fake_counts["login"] == 1 and ours.fake_counts["login"] == 0
    assert ours.fake_counts["refresh"] == 1
    extra = [x for x in norm_states(theirs.states) if x not in norm_states(ours.states)]
    # The reconnect also restarts its 15-minute heartbeat: 1200 s there, 900 s here.
    assert [(x["kind"], x.get("where") or x.get("event") or x.get("source"), x["t"])
            for x in extra] == [("error", "poll", 300.0), ("info", "connected", 300.0),
                                ("state", "heartbeat", 1200.0)]
    ours_extra = [x for x in norm_states(ours.states) if x not in norm_states(theirs.states)]
    assert [(x["source"], x["t"]) for x in ours_extra] == [("heartbeat", 900.0)]
    assert not [x for x in ours.states if x["kind"] == "error"]


async def test_two_robots_refused_until_one_is_chosen(tmp_path, pointed_at_fake):
    """pylitterbot's source shows the first robot the account lists. Ours refuses to guess:
    NO DATA (no_robots), with the serials' last digits in the message, until
    ``WHISKER_SERIAL`` chooses one; then it is the same as the other source's choice."""
    sc = SCENARIOS["two_robots"]()
    theirs = await run_pylitterbot(sc, tmp_path, pointed_at_fake)
    ours = await run_wire(sc, tmp_path / "a")
    assert theirs.probes[-1]["snap"] is not None
    assert all(p["snap"] is None and p["health"]["error"] == "no_robots" for p in ours.probes)
    errors = [x for x in ours.states if x["kind"] == "error"]
    assert errors[0]["error"] == "NoRobotFound" and "...0001, ...0002" in errors[0]["message"]
    chosen = await run_wire(sc, tmp_path / "b", serial="LR4C000001")
    assert compare(theirs, chosen) == {}


async def test_an_hour_is_the_same(tmp_path, pointed_at_fake):
    sc = SCENARIOS["an_hour"]()
    theirs = await run_pylitterbot(sc, tmp_path, pointed_at_fake)
    ours = await run_wire(sc, tmp_path)
    assert compare(theirs, ours) == {}


# =============================================================================================
# WireSource on its own
# =============================================================================================


async def test_the_studio_reads_our_log(tmp_path):
    """The log written through the daily files reads back in the studio's log reader the same
    as the pylitterbot source's: the visits and the contact errors."""
    sc = SCENARIOS["watched_visits"]()
    fake = await start_fake(sc)
    (tmp_path / "logs").mkdir()
    sl.save_token(tmp_path / "token.json", fake.issue_tokens())
    clock = Clock(sc["start_us"], sc["events"], sc["stop_at"],
                  lambda i, ev: perform(fake, sc, ev), Counter(), lambda now: src.view(now))
    src = WireSource(CFG, credentials=(fake.username, PASSWORD), tls=Tls(allow_plain=True),
                     log_dir=tmp_path / "logs", token_path=tmp_path / "token.json",
                     wall_fn=clock.wall, mono_fn=clock.mono, sleep=clock.sleep,
                     say=Console(), client_kw={"lr4_url": fake.url + "/graphql",
                                               "cognito_url": fake.url + "/",
                                               "ws_backoff": (0.05, 0.2), "timeout": 5.0})
    src.monitor.on_record = clock.counter.wrap(src.monitor.on_record)
    try:
        await asyncio.wait_for(src.run(asyncio.Event()), 60)
    finally:
        await fake.stop()
    rlog = RobotLog(tmp_path / "logs", just_cleaned_s=CFG.timings.just_cleaned_s,
                    visit_min_s=CFG.timings.visit_min_s)
    rlog.refresh()
    assert len(rlog.timeline.visits()) == 2
    assert rlog.timeline.contact_error() is None
    acts = (next((tmp_path / "logs").glob("spike-activity-*.jsonl")).read_text())
    assert '"code": "CD"' in acts


async def test_missing_credentials_never_connect(tmp_path):
    console = Console()
    sink = ListSink()
    src = WireSource(CFG, credentials=None, tls=Tls(allow_plain=True), sink=sink,
                     token_path=tmp_path / "t.json", say=console)
    stop = asyncio.Event()
    task = asyncio.create_task(src.run(stop))
    await asyncio.sleep(0.01)
    snap, health = src.view(src._wall())
    assert snap is None and health.error == "auth"
    stop.set()
    await asyncio.wait_for(task, 1)
    assert console.lines == [["error", ("Whisker credentials are not set (WHISKER_USERNAME, "
                                       "WHISKER_PASSWORD in .env); showing NO DATA. Restart "
                                       "after setting them.")]]
    assert [(x["kind"], x.get("where")) for x in sink.states] == [("error", "login"),
                                                                 ("info", None)]
    assert src.client.counts == {"refresh": 0, "login": 0, "graphql": 0, "ws_connect": 0}


async def test_tokens_are_shared_with_the_pylitterbot_source(tmp_path):
    """A renewal is saved in the same file and shape pylitterbot's callback writes, and the
    refresh token is kept."""
    sc = SCENARIOS["sleeping"]()
    sc["token"] = "expired"
    sc["stop_at"] = 30
    trace = await run_wire(sc, tmp_path)
    assert set(trace.token) == {"access_token", "id_token", "refresh_token"}
    assert trace.fake_counts["refresh"] == 1 and trace.fake_counts["login"] == 0


async def test_secrets_never_reach_the_log_or_console(tmp_path):
    sc = SCENARIOS["rejected_refresh"]()
    trace = await run_wire(sc, tmp_path)
    text = json.dumps([trace.states, trace.activities, trace.console])
    for secret in (PASSWORD, "kyle@example.com", "LR4C000001", "UNIT-1", "eyJ"):
        assert secret not in text, secret


def test_feeder_switch():
    p = build_parser()
    assert p.parse_args([]).source is None
    assert p.parse_args(["--source", "wire"]).source == "wire"
    with pytest.raises(SystemExit):
        p.parse_args(["--source", "other"])


def test_feeder_builds_the_wire_source(tmp_path, monkeypatch):
    from arthur import feeder as fd

    built = {}

    async def fake_run(opts, cfg, base_url, credentials, **kw):
        built["source"] = opts.source
        built["serial"] = opts.serial
        return 0

    monkeypatch.setattr(fd, "run", fake_run)
    assert fd.main(["--clock", "h"], environ={"ARTHUR_SOURCE": "wire",
                                              "WHISKER_SERIAL": "LR4X"}) == 0
    assert built == {"source": "wire", "serial": "LR4X"}
    assert fd.main(["--clock", "h"], environ={}) == 0
    assert built["source"] == "pylitterbot"
    assert fd.main(["--clock", "h"], environ={"ARTHUR_SOURCE": "nope"}) == fd.EXIT_USAGE
