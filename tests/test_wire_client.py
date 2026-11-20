"""The wire client against the fake Whisker: tokens, the robot, failures, the history, the
live subscription and TLS. Everything runs on 127.0.0.1; ``loopback_only`` fails any test
that looks up another host."""

from __future__ import annotations

import asyncio
import time

import pytest
from wirefake import FakeWhisker, activity_entry, robot_raw
from wirehelp import make_client, real_lines, real_raws, server_ssl

from arthur.wire import errors, util
from arthur.wire.const import USER_AGENT
from arthur.wire.net import Tls

pytestmark = pytest.mark.usefixtures("loopback_only")

USER, PASSWORD = "kyle@example.com", "hunter2-secret"


async def kind_of(coro) -> str:
    with pytest.raises(errors.WireError) as info:
        await coro
    return info.value.kind


def test_the_guard_refuses_other_hosts():
    """The guard fails before any lookup happens."""
    import socket

    with pytest.raises(AssertionError, match="example.invalid"):
        socket.getaddrinfo("example.invalid", 443)


# -- tokens ------------------------------------------------------------------------------------


async def test_valid_token_is_used_as_is(fake):
    c = make_client(fake)
    record = await c.connect()
    assert record["status"] == "READY"
    assert fake.counts["refresh"] == 0
    assert c.user_id() == "USER-9"


async def test_expired_token_is_renewed_and_refresh_token_kept(fake):
    saved = []
    tokens = fake.issue_tokens(ttl=-60)
    c = make_client(fake, tokens=tokens, on_tokens=saved.append)
    await c.connect()
    assert fake.counts["refresh"] == 1
    assert len(saved) == 1
    assert saved[0]["refresh_token"] == tokens["refresh_token"]
    assert saved[0]["id_token"] != tokens["id_token"]
    assert c.token_valid()


async def test_renewed_30_seconds_before_expiry(fake):
    c = make_client(fake, tokens=fake.issue_tokens(ttl=29))
    await c.connect()
    assert fake.counts["refresh"] == 1
    c2 = make_client(fake, tokens=fake.issue_tokens(ttl=40))
    await c2.connect()
    assert fake.counts["refresh"] == 1


async def test_a_401_renews_once_and_retries(fake):
    c = make_client(fake)
    await c.connect()
    fake.revoke()
    record = await c.refresh_robot()
    assert record["status"] == "READY"
    assert fake.counts["refresh"] == 1
    assert c.counts["graphql"] == 3  # load, refused refresh, retried refresh


async def test_two_401s_are_auth(fake):
    c = make_client(fake)
    await c.connect()
    fake.fail["graphql"] += [("status", 401), ("status", 401)]
    assert await kind_of(c.refresh_robot()) == errors.AUTH
    assert fake.counts["refresh"] == 1


async def test_rejected_refresh_without_password_is_auth(fake):
    fake.reject_refresh = True
    c = make_client(fake, tokens=fake.issue_tokens(ttl=-60))
    assert await kind_of(c.connect()) == errors.AUTH
    assert fake.counts["login"] == 0


async def test_rejected_refresh_with_password_logs_in(fake):
    fake.reject_refresh = True
    saved = []
    c = make_client(fake, tokens=fake.issue_tokens(ttl=-60), credentials=(USER, PASSWORD),
                    on_tokens=saved.append)
    await c.connect()
    assert fake.counts["login"] == 1
    assert saved[-1]["refresh_token"].startswith("fake-refresh-")
    assert c.counts["login"] == 1


async def test_password_login_with_no_tokens(fake):
    c = make_client(fake, tokens=None, credentials=(USER, PASSWORD))
    assert (await c.connect())["status"] == "READY"
    assert fake.counts["login"] == 1


@pytest.mark.parametrize("creds", [(USER, "wrong"), ("someone@example.com", PASSWORD)])
async def test_rejected_login_is_auth(fake, creds):
    c = make_client(fake, tokens=None, credentials=creds)
    with pytest.raises(errors.WireError) as info:
        await c.connect()
    assert info.value.kind == errors.AUTH
    assert info.value.code == "NotAuthorizedException"


async def test_cognito_throttling_is_api(fake):
    fake.fail["cognito"].append(("status", 400, {"__type": "TooManyRequestsException",
                                                 "message": "Rate exceeded"}))
    c = make_client(fake, tokens=fake.issue_tokens(ttl=-60))
    assert await kind_of(c.connect()) == errors.API


async def test_no_token_no_password_is_auth(fake):
    c = make_client(fake, tokens=None)
    assert await kind_of(c.connect()) == errors.AUTH


# -- the robot ---------------------------------------------------------------------------------


async def test_no_robots(fake):
    fake.robots = []
    c = make_client(fake)
    assert await kind_of(c.connect()) == errors.NO_ROBOTS


async def test_robot_not_onboarded_is_left_out(fake):
    fake.robots = [robot_raw(isOnboarded=False)]
    c = make_client(fake)
    assert await kind_of(c.connect()) == errors.NO_ROBOTS


async def test_two_robots_need_a_serial(fake):
    fake.robots = [robot_raw(), robot_raw(serial="LR4C000002", unit_id="UNIT-2",
                                          robotStatus="ROBOT_CLEAN")]
    c = make_client(fake)
    with pytest.raises(errors.WireError) as info:
        await c.connect()
    assert info.value.kind == errors.NO_ROBOTS
    assert "2 Litter-Robot 4s" in str(info.value)
    c2 = make_client(fake, serial="LR4C000002")
    assert (await c2.connect())["status"] == "CLEAN_CYCLE"
    c3 = make_client(fake, serial="LR4C000009")
    assert await kind_of(c3.connect()) == errors.NO_ROBOTS


async def test_refresh_merges(fake):
    c = make_client(fake)
    await c.connect()
    fake.robots[0].update(robotStatus="ROBOT_CAT_DETECT", catWeight=9.5)
    record = await c.refresh_robot()
    assert (record["status"], record["pet_weight"]) == ("CAT_DETECTED", 9.5)


@pytest.mark.parametrize("fault, kind", [
    (("status", 500), errors.API),
    (("status", 503), errors.API),
    (("status", 403), errors.API),
    (("garbage",), errors.API),
    (("json", {"data": None, "errors": [{"errorType": "X", "message": "boom"}]}), errors.API),
    (("json", {"data": {"getLitterRobot4BySerial": None}}), errors.API),
    (("json", ["not", "an", "object"]), errors.API),
    (("drop",), errors.API),
    (("slow", 2.0), errors.TIMEOUT),
])
async def test_poll_failures_are_classified(fake, fault, kind):
    c = make_client(fake, timeout=0.5)
    await c.connect()
    fake.fail["graphql"].append(fault)
    assert await kind_of(c.refresh_robot()) == kind
    assert (await c.refresh_robot())["status"] == "READY"  # and the next one works


async def test_unauthorized_graphql_error_renews(fake):
    c = make_client(fake)
    await c.connect()
    fake.fail["graphql"].append(("json", {"data": None, "errors": [
        {"errorType": "UnauthorizedException", "message": "Token has expired."}]}))
    assert (await c.refresh_robot())["status"] == "READY"
    assert fake.counts["refresh"] == 1


async def test_nothing_listening_is_api():
    c = make_client(FakeWhisker(), tokens=None)
    c.lr4_url = "http://127.0.0.1:9/graphql"
    c.tokens = FakeWhisker().issue_tokens()
    assert await kind_of(c.load_robot()) == errors.API


async def test_requests_carry_our_user_agent_and_no_compression(fake):
    c = make_client(fake)
    await c.connect()
    headers = fake.requests[-1]["headers"]
    assert headers["User-Agent"] == USER_AGENT
    assert headers["Accept-Encoding"] == "identity"
    assert headers["Authorization"].startswith("Bearer ey")


# -- history -----------------------------------------------------------------------------------


def fill_history(fake: FakeWhisker, hours: int, t0: float) -> None:
    for i in range(hours):
        fake.activities.append(activity_entry(t0 - 3600 * i, "robotStatusCatDetect"))
        fake.activities.append(activity_entry(t0 - 3600 * i + 60, "catWeight", 4.6))


@pytest.mark.parametrize("order", ["desc", "asc"])
async def test_history_pages_give_what_one_request_would(fake, order):
    fake.order = order
    t0 = float(int(time.time()))
    fill_history(fake, 60, t0)
    c = make_client(fake)
    await c.connect()
    before = fake.counts["graphql"]
    acts = await c.history(start=t0 - 100 * 3600, limit=100, page=25)
    # Pages overlap by the edge entry (the bounds are inclusive, and entries can share a
    # second), so 100 entries take five or six pages of 25.
    pages = fake.counts["graphql"] - before
    assert 4 < pages <= 6
    assert len(acts) == 100
    texts = {a.iso for a in acts}
    assert len(texts) == 100
    newest = max(a.ts for a in acts)
    oldest = min(a.ts for a in acts)
    if order == "desc":  # the server drops the oldest: we get the newest 100
        assert newest == t0 + 60
    else:  # the server drops the newest: we get the oldest 100
        assert oldest == t0 - 59 * 3600
    sent = [r["json"]["variables"] for r in fake.requests if r["path"] == "/graphql"][-pages:]
    assert sent[0]["startTimestamp"] == util.api_timestamp(t0 - 100 * 3600)
    assert all(v["limit"] <= 25 and v["consumer"] == "app" for v in sent)


@pytest.mark.parametrize("order", ["desc", "asc"])
async def test_history_keeps_entries_that_share_a_second_across_pages(fake, order):
    """A clean cycle's entries share a second; with tiny pages they straddle page edges and
    fill whole pages, and none may be lost or doubled."""
    fake.order = order
    t0 = float(int(time.time()))
    for k in range(4):
        for v in ("robotCycleStatusIdle", "odometerCleanCycles", "DFILevelPercent",
                  "robotCycleStatusDump", "robotStatusCatDetect"):
            fake.activities.append(activity_entry(t0 - 600 * k, v, 3000 + k))
    c = make_client(fake)
    await c.connect()
    acts = await c.history(start=t0 - 3600, limit=100, page=2)
    assert len(acts) == 20
    assert len({(a.iso, a.action.text) for a in acts}) == 20


async def test_history_without_start_is_the_servers_default(fake):
    t0 = float(int(time.time()))
    fill_history(fake, 24 * 8, t0)
    c = make_client(fake)
    await c.connect()
    acts = await c.history(limit=500, page=100)
    assert "startTimestamp" not in fake.requests[-1]["json"]["variables"]
    assert min(a.ts for a in acts) >= t0 - 6 * 86400


async def test_history_null_is_api(fake):
    c = make_client(fake)
    await c.connect()
    fake.fail["graphql"].append(("json", {"data": {"getLitterRobot4Activity": None}}))
    assert await kind_of(c.history(start=time.time() - 60)) == errors.API


# -- the live subscription ---------------------------------------------------------------------


class Live:
    """Run ``client.live`` as a task and collect what it hands on."""

    def __init__(self, client):
        self.client = client
        self.records: list[dict] = []
        self.events: list[tuple[str, str]] = []
        self.task = None

    def __enter__(self):
        self.task = asyncio.ensure_future(self.client.live(
            lambda: self.records.append(self.client.record()),
            lambda k, m: self.events.append((k, m))))
        return self

    def __exit__(self, *exc):
        self.task.cancel()

    async def until(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > end:
                raise AssertionError(f"timed out; events: {self.events}")
            await asyncio.sleep(0.01)


async def test_real_visit_records_arrive_exactly(fake):
    """Every line of the real visits, pushed as the robot would push it, comes out of the
    client as exactly the record the spike logged."""
    lines = real_lines()
    fake.robots = [real_raws()[0]]
    c = make_client(fake)
    await c.connect()
    with Live(c) as live:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        for raw in real_raws():
            await fake.push(raw)
        await live.until(lambda: len(live.records) == len(lines))
    assert live.records == [x["record"] for x in lines]
    statuses = [r["status"] for r in live.records]
    assert "CAT_DETECTED" in statuses and "CLEAN_CYCLE" in statuses


async def test_partial_updates_merge(fake):
    c = make_client(fake)
    await c.connect()
    with Live(c) as live:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        await fake.push({"robotStatus": "ROBOT_CAT_DETECT", "displayCode": "DC_CAT_DETECT"},
                         partial=True)
        await fake.push({"catWeight": 9.75}, partial=True)
        await live.until(lambda: len(live.records) == 2)
    assert live.records[1]["status"] == "CAT_DETECTED"
    assert live.records[1]["pet_weight"] == 9.75
    assert live.records[1]["cycle_count"] == 3478


async def test_visit_with_no_weight_push(fake):
    """Detected, then cleaning, then ready, and the weight never changes."""
    c = make_client(fake)
    await c.connect()
    with Live(c) as live:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        for status in ("ROBOT_CAT_DETECT", "ROBOT_CAT_DETECT_DELAY", "ROBOT_CLEAN",
                       "ROBOT_IDLE"):
            await fake.push({"robotStatus": status})
        await live.until(lambda: len(live.records) == 4)
    assert [r["status"] for r in live.records] == [
        "CAT_DETECTED", "CAT_SENSOR_TIMING", "CLEAN_CYCLE", "READY"]
    assert {r["pet_weight"] for r in live.records} == {4.67}


async def test_other_robots_and_garbage_are_ignored(fake):
    fake.robots.append(robot_raw(serial="LR4C000002", unit_id="UNIT-2"))
    c = make_client(fake, serial="LR4C000001")
    await c.connect()
    with Live(c) as live:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        await fake.push({"robotStatus": "ROBOT_CLEAN"}, index=1)
        await fake.push({}, raw_text="this is not json {")
        await fake.push({}, raw_text='{"type": "data", "payload": null}')
        await fake.push({"robotStatus": "ROBOT_CAT_DETECT"})
        await live.until(lambda: len(live.records) == 1)
    assert live.records[0]["status"] == "CAT_DETECTED"


async def test_subscription_messages(fake):
    c = make_client(fake)
    await c.connect()
    with Live(c):
        await asyncio.wait_for(fake.subscribed.wait(), 5)
    up = fake.ws_upgrades[0]
    assert up["header"]["host"] == "lr4.iothings.site"
    assert up["header"]["Authorization"].startswith("Bearer ")
    assert up["payload"] == "e30="
    assert up["headers"]["Sec-WebSocket-Protocol"] == "graphql-ws"
    assert up["headers"]["User-Agent"] == USER_AGENT
    start = fake.ws_messages[0]
    assert start["type"] == "start" and len(start["id"]) == 36


async def test_stale_socket_is_reopened(fake):
    fake.ws_script = ["silent"]
    c = make_client(fake, stale_s=0.3)
    await c.connect()
    with Live(c) as live:
        await live.until(lambda: fake.counts["ws"] >= 2)
        await live.until(lambda: fake.subscribed.is_set())
        await fake.push({"robotStatus": "ROBOT_CLEAN"})
        await live.until(lambda: live.records)
    assert any("stale" in m for k, m in live.events if k == "ws_closed")


async def test_dropped_socket_is_reopened(fake):
    c = make_client(fake)
    await c.connect()
    with Live(c) as live:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        await fake.drop_sockets()
        await live.until(lambda: fake.counts["ws"] >= 2 and fake.subscribed.is_set())
        await fake.push({"robotStatus": "ROBOT_CLEAN"})
        await live.until(lambda: live.records)


@pytest.mark.parametrize("script", [["error"], ["drop"], ["refuse500"], ["refuse"]])
async def test_failed_sessions_reconnect(fake, script):
    fake.ws_script = list(script)
    c = make_client(fake)
    await c.connect()
    with Live(c) as live:
        await live.until(lambda: fake.counts["ws"] >= 2 and fake.subscribed.is_set())
        await fake.push({"robotStatus": "ROBOT_CLEAN"})
        await live.until(lambda: live.records)
    assert live.events[0][0] in ("ws_closed", "ws_open")


async def test_refused_upgrade_renews_the_token(fake):
    c = make_client(fake)
    await c.connect()
    fake.revoke()
    with Live(c) as live:
        await live.until(lambda: fake.subscribed.is_set())
    assert fake.counts["refresh"] >= 1
    assert fake.counts["ws"] == 2


async def test_reconnect_backoff_doubles(fake):
    fake.ws_script = ["refuse500"] * 4
    c = make_client(fake, ws_backoff=(0.05, 0.1))
    await c.connect()
    with Live(c) as live:
        await live.until(lambda: fake.subscribed.is_set(), timeout=5)
    waits = [m.rsplit("in ", 1)[1] for k, m in live.events if k == "ws_closed"]
    assert waits == ["0.05s", "0.1s", "0.1s", "0.1s"]
    assert fake.counts["ws"] == 5


async def test_stop_is_sent_when_cancelled(fake):
    c = make_client(fake)
    await c.connect()
    with Live(c) as live:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
    await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.shield(asyncio.sleep(0.2)), 1)
    assert live.task.cancelled() or live.task.done()
    assert fake.ws_messages[-1]["type"] == "stop"


# -- TLS ---------------------------------------------------------------------------------------


async def tls_fake(tls_files):
    f = FakeWhisker(ka_s=0.2)
    await f.start(ssl_context=server_ssl(tls_files))
    return f


async def test_tls_verified(tls_files):
    f = await tls_fake(tls_files)
    try:
        c = make_client(f, tls=Tls(ca_file=str(tls_files["ca"])))
        assert (await c.connect())["status"] == "READY"
        with Live(c) as live:
            await asyncio.wait_for(f.subscribed.wait(), 5)
            await f.push({"robotStatus": "ROBOT_CLEAN"})
            await live.until(lambda: live.records)
    finally:
        await f.stop()


@pytest.mark.parametrize("case", ["wrong_ca", "wrong_name", "system_store"])
async def test_tls_refuses(tls_files, case):
    f = await tls_fake(tls_files)
    try:
        if case == "wrong_ca":
            tls = Tls(ca_file=str(tls_files["wrong_ca"]))
        elif case == "wrong_name":
            tls = Tls(ca_file=str(tls_files["ca"]), server_name="lr4.iothings.site")
        else:
            tls = Tls()
        c = make_client(f, tls=tls)
        with pytest.raises(errors.WireError) as info:
            await c.connect()
        assert info.value.kind == errors.API
        assert "CERTIFICATE" in str(info.value).upper()
    finally:
        await f.stop()


async def test_plain_http_is_refused_outside_tests(fake):
    c = make_client(fake, tls=Tls())
    with pytest.raises(errors.WireError) as info:
        await c.connect()
    assert "refusing a plain connection" in str(info.value)
