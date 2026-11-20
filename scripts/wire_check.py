"""The supervised live check of our own Whisker client (arthur/wire, D-0029). Read-only.

Answers what only the real service can (docs/reference/whisker-wire.md, "Unknown until the
supervised live step"): does Whisker accept our User-Agent, how long do tokens live, does a
renewal return a new refresh token, what the socket sends and how often, whether each update
carries every field, the history's order and timestamp format, whether ``endTimestamp`` works.

**Stop the feeder first** (``pkill -f arthur.feeder``): Whisker allows one connection per
account. Then:

    uv run --env-file .env python scripts/wire_check.py [--minutes 5] [--login]

It renews the saved token in ``.whisker-token.json`` (read, never written; ``--token-file``
to use another) and never logs in unless the token is dead or ``--login`` is given. It prints
no token, user id or serial. Nothing here sends a command to the robot.

``--base-url`` and ``--plain`` point it at the fake Whisker for its own test.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arthur.wire import cognito, errors, http, lr4, util, ws
from arthur.wire.client import WhiskerClient, _uuid4
from arthur.wire.const import COGNITO_URL, LR4_URL, USER_AGENT
from arthur.wire.net import Tls


def say(what: str, **kw) -> None:
    print(json.dumps({"check": what, **kw}), flush=True)


def shape(text: str) -> str:
    """A value's shape with every digit as 9 (timestamps without the moment)."""
    return re.sub(r"\d", "9", str(text))


def model_keys() -> set[str]:
    inner = re.sub(r"\{[^{}]*\}", "", lr4.MODEL[1:-1])
    return set(re.sub(r"\{[^{}]*\}", "", inner).split())


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minutes", type=float, default=5.0, help="hold the socket (default 5)")
    p.add_argument("--login", action="store_true", help="also try one password login")
    p.add_argument("--token-file", type=Path, default=Path(".whisker-token.json"))
    p.add_argument("--base-url", help="the fake Whisker, for this script's own test")
    p.add_argument("--plain", action="store_true", help="allow http (the fake only)")
    p.add_argument("--ca-file", help="CA bundle (default: the system's)")
    a = p.parse_args()

    lr4_url = (a.base_url + "/graphql") if a.base_url else LR4_URL
    cognito_url = (a.base_url + "/") if a.base_url else COGNITO_URL
    tls = Tls(ca_file=a.ca_file, allow_plain=a.plain)
    creds = None
    if os.environ.get("WHISKER_USERNAME") and os.environ.get("WHISKER_PASSWORD"):
        creds = (os.environ["WHISKER_USERNAME"].strip(), os.environ["WHISKER_PASSWORD"])
    tokens = None
    try:
        data = json.loads(a.token_file.read_text())
        if isinstance(data, dict) and data.get("refresh_token"):
            tokens = data
    except (OSError, ValueError):
        pass
    say("start", user_agent=USER_AGENT, have_saved_token=tokens is not None,
        have_password=creds is not None)

    # 1. Tokens: one renewal, through Cognito directly so the answer's keys show.
    if tokens:
        try:
            answer = await cognito.call("InitiateAuth", {
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "AuthParameters": {"REFRESH_TOKEN": tokens["refresh_token"]},
                "ClientId": cognito.CLIENT_ID}, tls, cognito_url)
            result = answer.get("AuthenticationResult") or {}
            say("renewal", ok=True, keys=sorted(result),
                new_refresh_token="RefreshToken" in result, expires_in=result.get("ExpiresIn"))
            tokens = {"access_token": result["AccessToken"], "id_token": result["IdToken"],
                      "refresh_token": result.get("RefreshToken") or tokens["refresh_token"]}
        except errors.WireError as exc:
            say("renewal", ok=False, kind=exc.kind, code=exc.code, status=exc.status)
            tokens = None
    if a.login or not tokens:
        if not creds:
            say("login", ok=False, why="no saved token and no WHISKER_USERNAME/PASSWORD")
            return 2
        t0 = time.monotonic()
        try:
            tokens = await cognito.login(creds[0], creds[1], tls, cognito_url)
            say("login", ok=True, seconds=round(time.monotonic() - t0, 2))
        except errors.WireError as exc:
            say("login", ok=False, kind=exc.kind, code=exc.code, message=str(exc)[:200])
            return 3
    claims = util.jwt_claims(tokens["id_token"])
    access = util.jwt_claims(tokens["access_token"])
    say("tokens", id_lifetime_s=claims.get("exp", 0) - claims.get("iat", 0),
        access_lifetime_s=access.get("exp", 0) - access.get("iat", 0),
        id_claims=sorted(claims), has_mid="mid" in claims)

    client = WhiskerClient(tls, tokens=tokens, credentials=creds, lr4_url=lr4_url,
                           cognito_url=cognito_url)

    # 2. The robots query, raw, so the status and headers show (a 403 would be the agent).
    body = json.dumps(lr4.robots_query(client.user_id()))
    resp = await http.request("POST", lr4_url, tls, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + tokens["id_token"]},
        body=body)
    say("robots_query", status=resp.status, content_type=resp.headers.get("content-type"),
        transfer_encoding=resp.headers.get("transfer-encoding"),
        content_encoding=resp.headers.get("content-encoding"), bytes=len(resp.body))
    if resp.status != 200:
        say("stop", why="the robots query failed", body=resp.body[:300].decode("utf-8", "replace"))
        return 4
    try:
        record = await client.connect()
    except errors.WireError as exc:
        say("connect", ok=False, kind=exc.kind, message=str(exc)[:200])
        return 5
    missing = sorted(model_keys() - set(client.data))
    say("robot", record=record, keys=len(client.data), missing_model_keys=missing)
    say("refresh", record=await client.refresh_robot())

    # 3. History: order, timestamp shape, and endTimestamp.
    now = util.now()
    for label, variables in (
            ("history_start", {"start": now - 86400}),
            ("history_start_end", {"start": now - 7 * 86400, "end": now - 86400})):
        payload = lr4.activity_query(client.serial, 5, variables.get("start"),
                                     variables.get("end"))
        try:
            entries = await client.graphql(payload, "getLitterRobot4Activity")
            stamps = [e.get("timestamp") for e in entries or []]
            parsed = [util.parse_iso(util.timestamp_iso(s)) for s in stamps if s]
            order = ("newest first" if parsed == sorted(parsed, reverse=True) else
                     "oldest first" if parsed == sorted(parsed) else "neither")
            say(label, ok=True, entries=len(stamps), order=order,
                timestamp_shapes=sorted({shape(s) for s in stamps}),
                action_value_types=sorted({type(e.get("actionValue")).__name__
                                           for e in entries or []}))
        except errors.WireError as exc:
            say(label, ok=False, kind=exc.kind, message=str(exc)[:300])

    # 4. The socket: message types, keep-alive spacing, fields per update.
    bearer = "Bearer " + client.tokens["id_token"]
    sock = await ws.WebSocket.connect(lr4.ws_url(bearer, lr4_url), tls,
                                      subprotocol="graphql-ws")
    say("socket", protocol=sock.protocol)
    sub_id = _uuid4()
    await sock.send_text(lr4.start_message(sub_id, bearer, client.user_id()))
    kinds: Counter[str] = Counter()
    ka_at: list[float] = []
    end = time.monotonic() + a.minutes * 60
    try:
        while time.monotonic() < end:
            try:
                op, payload = await asyncio.wait_for(sock.recv(), end - time.monotonic())
            except TimeoutError:
                break
            except ws.Closed as exc:
                say("socket_closed", code=exc.code)
                break
            if op != ws.TEXT:
                kinds["binary"] += 1
                continue
            try:
                message = json.loads(payload)
            except ValueError:
                kinds["not json"] += 1
                continue
            kind = str(message.get("type"))
            kinds[kind] += 1
            if kind == "ka":
                ka_at.append(time.monotonic())
            elif kind == "data":
                for item in lr4.robots_in_message(message) or ():
                    if str(item.get("unitId")) == client.robot_id:
                        client.merge(item)
                        say("update", keys=len(item),
                            missing_model_keys=sorted(model_keys() - set(item)),
                            status=client.record()["status"],
                            pet_weight=client.record()["pet_weight"])
            elif kind != "start_ack":
                say("message", type=kind, payload=json.dumps(message.get("payload"))[:300])
    finally:
        await sock.send_text(lr4.stop_message(sub_id))
        await sock.close()
    gaps = [round(b - a_, 1) for a_, b in itertools.pairwise(ka_at)]
    say("socket_summary", types=dict(kinds), ka_gaps_s=gaps[:20])
    say("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
