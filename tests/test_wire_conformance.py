"""pylitterbot 2025.8.0 and pycognito 2024.5.1 themselves, against the fake Whisker.

This shows the fake speaks what the real libraries speak: pycognito's SRP login (through
boto3) succeeds against the fake's Cognito, pylitterbot loads the robot, subscribes and
receives the real visits, and builds the same records our client builds. It also records
what pylitterbot really sends (headers, bodies), which ``docs/reference/whisker-wire.md``
cites. Everything is pointed at 127.0.0.1: boto3 through ``AWS_ENDPOINT_URL_...``, the JWKS
through ``COGNITO_JWKS``, pylitterbot through its ``LR4_ENDPOINT``; ``loopback_only`` fails
the test if anything looks up another host.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pylitterbot import Account, LitterRobot4
from pylitterbot.event import EVENT_UPDATE
from pylitterbot.robot import litterrobot4
from wirefake import activity_entry, jwks
from wirehelp import make_client, real_lines, real_raws

from arthur import spikelog
from arthur.wire.const import USER_AGENT

pytestmark = [
    pytest.mark.usefixtures("loopback_only"),
    # pycognito's own use of datetime.utcnow(), not ours
    pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow:DeprecationWarning"),
]

USER, PASSWORD = "kyle@example.com", "hunter2-secret"


@pytest.fixture
def pointed_at(fake, monkeypatch, tmp_path):
    monkeypatch.setattr(litterrobot4, "LR4_ENDPOINT", fake.url + "/graphql")
    monkeypatch.setenv("AWS_ENDPOINT_URL_COGNITO_IDENTITY_PROVIDER", fake.url)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("COGNITO_JWKS", json.dumps(jwks()))
    return fake


async def connect(account, **kw):
    await account.connect(load_robots=True, subscribe_for_updates=True, load_pets=False, **kw)
    return account.get_robots(LitterRobot4)[0]


async def test_pycognito_logs_in_against_the_fake(pointed_at):
    fake = pointed_at
    account = Account(robot_types=[LitterRobot4])
    try:
        robot = await connect(account, username=USER, password=PASSWORD)
        assert fake.counts["login"] == 1
        assert robot.status.name == "READY"
        cognito = [r for r in fake.requests if r["path"] == "/"]
        print("boto3 headers:", {k: v for k, v in cognito[0]["headers"].items()})
        targets = [r["headers"]["X-Amz-Target"] for r in cognito]
        assert targets == ["AWSCognitoIdentityProviderService.InitiateAuth",
                           "AWSCognitoIdentityProviderService.RespondToAuthChallenge"]
        first = cognito[0]
        assert first["headers"]["Content-Type"] == "application/x-amz-json-1.1"
        assert first["json"]["AuthFlow"] == "USER_SRP_AUTH"
        assert set(first["json"]["AuthParameters"]) == {"USERNAME", "SRP_A"}
        assert "Authorization" not in first["headers"]  # unsigned
        second = cognito[1]["json"]
        assert set(second["ChallengeResponses"]) == {
            "TIMESTAMP", "USERNAME", "PASSWORD_CLAIM_SECRET_BLOCK", "PASSWORD_CLAIM_SIGNATURE"}
    finally:
        await account.disconnect()


async def test_pycognito_with_a_wrong_password_is_refused(pointed_at):
    from pylitterbot.exceptions import LitterRobotLoginException

    account = Account(robot_types=[LitterRobot4])
    with pytest.raises(LitterRobotLoginException):
        await connect(account, username=USER, password="wrong")
    await account.disconnect()


async def test_our_login_against_the_same_fake(pointed_at):
    """Our SRP client is accepted by the same server check pycognito passes."""
    fake = pointed_at
    c = make_client(fake, tokens=None, credentials=(USER, PASSWORD))
    assert (await c.connect())["status"] == "READY"
    assert fake.counts["login"] == 1


async def test_pylitterbot_refreshes_an_expired_token_twice(pointed_at):
    """With a saved token whose access token has expired, pylitterbot's first refresh goes
    through ``get_user()``, whose ``check_token`` renews once, and then renews again."""
    fake = pointed_at
    account = Account(token=fake.issue_tokens(ttl=-60), robot_types=[LitterRobot4])
    try:
        await connect(account)
        assert fake.counts["refresh"] == 2
        flows = [r["json"]["AuthFlow"] for r in fake.requests if r["path"] == "/"]
        assert flows == ["REFRESH_TOKEN_AUTH", "REFRESH_TOKEN_AUTH"]
        assert set(fake.requests[0]["json"]["AuthParameters"]) == {"REFRESH_TOKEN"}
    finally:
        await account.disconnect()


async def test_pylitterbot_and_our_client_hand_on_the_same_records(pointed_at):
    fake = pointed_at
    fake.robots = [real_raws()[0]]
    account = Account(token=fake.issue_tokens(), robot_types=[LitterRobot4])
    theirs: list[dict] = []
    try:
        robot = await connect(account)
        robot.on(EVENT_UPDATE, lambda: theirs.append(spikelog.build_record(robot)))
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        for raw in real_raws():
            await fake.push(raw)
        for _ in range(300):
            if len(theirs) >= len(real_lines()):
                break
            await asyncio.sleep(0.01)
    finally:
        await account.disconnect()
    assert theirs == [x["record"] for x in real_lines()]

    # What pylitterbot sent, for the reference note.
    graphql = next(r for r in fake.requests if r["path"] == "/graphql")
    assert graphql["headers"]["User-Agent"] == "pylitterbot/2025.8.0"
    print("pylitterbot GraphQL headers:", {k: v for k, v in graphql["headers"].items()
                                           if k != "Authorization"})
    assert graphql["headers"]["Authorization"].startswith("Bearer ")
    upgrade = fake.ws_upgrades[0]
    print("pylitterbot WebSocket headers:", {k: v for k, v in upgrade["headers"].items()
                                              if k not in ("Sec-WebSocket-Key",)})
    assert upgrade["headers"]["Sec-WebSocket-Protocol"] == "graphql-ws"
    assert upgrade["headers"]["User-Agent"] == "pylitterbot/2025.8.0"
    assert upgrade["header"]["host"] == "lr4.iothings.site"
    assert [m["type"] for m in fake.ws_messages] == ["start", "stop"]

    # And ours, the same records from the same pushes.
    fake.robots = [real_raws()[0]]
    fake.subscribed.clear()
    c = make_client(fake)
    await c.connect()
    ours: list[dict] = []
    task = asyncio.ensure_future(c.live(lambda: ours.append(c.record())))
    try:
        await asyncio.wait_for(fake.subscribed.wait(), 5)
        for raw in real_raws():
            await fake.push(raw)
        for _ in range(300):
            if len(ours) >= len(theirs):
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert ours == theirs
    assert fake.requests[-1]["headers"]["User-Agent"] == USER_AGENT


async def test_pylitterbot_history_against_the_fake(pointed_at):
    fake = pointed_at
    import time

    t0 = float(int(time.time()))
    fake.activities = [activity_entry(t0 - 120, "robotStatusCatDetect"),
                       activity_entry(t0 - 60, "catWeight", 4.67),
                       activity_entry(t0 - 30, "robotCycleStatusDump")]
    account = Account(token=fake.issue_tokens(), robot_types=[LitterRobot4])
    try:
        robot = await connect(account)
        from datetime import UTC, datetime

        theirs = await robot.get_activity_history(limit=100, start=datetime.fromtimestamp(
            t0 - 3600, UTC))
    finally:
        await account.disconnect()
    variables = [r for r in fake.requests if r["path"] == "/graphql"][-1]["json"]["variables"]
    assert set(variables) == {"serial", "limit", "consumer", "startTimestamp"}
    c = make_client(fake)
    await c.connect()
    ours = await c.history(start=t0 - 3600, limit=100)
    assert [(a.timestamp, str(a.action.text)) for a in ours] == [
        (a.timestamp, getattr(a.action, "text", None) or a.action) for a in theirs]
