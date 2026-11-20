"""A fake Whisker for the wire client's tests: Cognito, the LR4 GraphQL endpoint and its
realtime WebSocket, on 127.0.0.1, optionally over TLS with a throwaway CA. No real service is
ever contacted.

It speaks what ``docs/reference/whisker-wire.md`` documents, closely enough that pylitterbot
2025.8.0 and pycognito 2024.5.1 themselves log in, load the robot and subscribe against it
(``tests/test_wire_conformance.py``). Scriptable: robots, history, pushes (full or partial),
and failures (``fail``, ``ws_script``, ``reject_refresh``, ``revoke``, ``silent``).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from aiohttp import WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from arthur.wire import srp as wsrp
from arthur.wire.const import CLIENT_ID, USER_POOL_ID

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/" + USER_POOL_ID
POOL_NAME = USER_POOL_ID.split("_")[1]
_KEY = None


def _key():
    global _KEY
    if _KEY is None:
        _KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _KEY


def jwks() -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_key().public_key()))
    jwk.update({"kid": "fake-kid", "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------------------------
# A throwaway CA and a server certificate for 127.0.0.1 / localhost
# ---------------------------------------------------------------------------------------------


def make_ca(directory: Path, name: str = "Fake Whisker Test CA"):
    """(ca_cert_path, ca_key) — a self-signed CA, written as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                       critical=False)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = directory / (name.replace(" ", "-").lower() + ".pem")
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path, cert, key


def make_server_cert(directory: Path, ca_cert, ca_key):
    """(cert_path, key_path) for localhost and 127.0.0.1, signed by the CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = directory / "server.pem"
    key_path = directory / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return cert_path, key_path


# ---------------------------------------------------------------------------------------------
# Robots and history
# ---------------------------------------------------------------------------------------------

SLEEP_OFF = {d: {"sleepTime": 0, "wakeTime": 0, "isEnabled": False} for d in (
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")}


def robot_raw(serial: str = "LR4C000001", unit_id: str = "UNIT-1", **overrides) -> dict:
    """A Litter-Robot 4 as ``getLitterRobot4ByUser`` returns it: every field of the model,
    with values of the shapes seen in the spike's logs. Synthetic."""
    data = {
        "unitId": unit_id, "name": "Test box", "serial": serial, "userId": "USER-9",
        "espFirmware": "1.1.72", "picFirmwareVersion": "10512.3072.2.92",
        "picFirmwareVersionHex": "abc", "laserBoardFirmwareVersion": "4.0.65.4",
        "laserBoardFirmwareVersionHex": "def", "wifiRssi": -41.0, "unitPowerType": "AC",
        "catWeight": 4.67, "displayCode": "DC_MODE_IDLE", "unitTimezone": "America/New_York",
        "unitTime": None, "cleanCycleWaitTime": 8, "isKeypadLockout": False,
        "nightLightMode": "AUTO", "nightLightBrightness": 50, "isPanelSleepMode": False,
        "panelSleepTime": 0, "panelWakeTime": 0,
        "weekdaySleepModeEnabled": json.loads(json.dumps(SLEEP_OFF)),
        "unitPowerStatus": "ON", "sleepStatus": "WAKE", "robotStatus": "ROBOT_IDLE",
        "globeMotorFaultStatus": "FAULT_CLEAR", "pinchStatus": "CLEAR",
        "catDetect": "CAT_DETECT_CLEAR", "isBonnetRemoved": False, "isNightLightLEDOn": True,
        "odometerPowerCycles": 12, "odometerCleanCycles": 3478, "panelBrightnessHigh": 100,
        "panelBrightnessLow": 25, "smartWeightEnabled": True, "odometerEmptyCycles": 3,
        "odometerFilterCycles": 0, "isDFIResetPending": False, "DFINumberOfCycles": 16,
        "DFILevelPercent": 67, "isDFIFull": False, "DFIFullCounter": 0, "DFITriggerCount": 9,
        "litterLevel": 459, "DFILevelMM": 120, "isCatDetectPending": False,
        "globeMotorRetractFaultStatus": "FAULT_CLEAR", "robotCycleStatus": "CYCLE_IDLE",
        "robotCycleState": "CYCLE_STATE_WAIT_ON", "weightSensor": 0.0, "isOnline": True,
        "isOnboarded": True, "isProvisioned": True, "isDebugModeActive": False,
        "lastSeen": "2026-11-13T13:31:05.702Z", "sessionId": "SESSION-1",
        "setupDateTime": "2024-01-02T03:04:05.000Z", "isFirmwareUpdateTriggered": False,
        "firmwareUpdateStatus": "NONE", "wifiModeStatus": "ROUTER_CONNECTED",
        "isUSBPowerOn": True, "USBFaultStatus": "CLEAR", "isDFIPartialFull": True,
        "isLaserDirty": False, "surfaceType": "TILE", "hopperStatus": None,
        "scoopsSavedCount": 120, "isHopperRemoved": True, "optimalLitterLevel": 450,
        "litterLevelPercentage": 0.82, "litterLevelState": "OPTIMAL",
    }
    data.update(overrides)
    return data


def raw_from_record(record: dict, base: dict | None = None) -> dict:
    """Raw robot data that makes ``record`` again: the logged raw keys as they are, and each
    derived property put back into the raw field it comes from. For playing logged records
    (which carry no full raw data) through the fake."""
    raw = dict(base or robot_raw())
    for key in ("robotStatus", "robotCycleState", "robotCycleStatus", "displayCode",
                "catDetect", "isCatDetectPending", "weightSensor", "pinchStatus", "isDFIFull",
                "isDFIPartialFull", "DFILevelPercent", "litterLevel", "litterLevelPercentage",
                "sleepStatus", "unitPowerStatus", "isBonnetRemoved", "wifiRssi",
                "globeMotorFaultStatus"):
        raw[key] = record[key]
    raw.update({
        "isOnline": record["is_online"], "catWeight": record["pet_weight"],
        "cleanCycleWaitTime": record["clean_cycle_wait_time_minutes"],
        "isLaserDirty": record["is_laser_dirty"], "unitPowerType": record["power_type"],
        "lastSeen": record["last_seen"], "odometerCleanCycles": record["cycle_count"],
        "DFINumberOfCycles": record["cycle_capacity"],
        "DFIFullCounter": record["cycles_after_drawer_full"],
        "hopperStatus": record["hopper_status"], "isHopperRemoved": record["is_hopper_removed"],
        "litterLevelState": record["litter_level_state"],
    })
    if record["sleep_mode_enabled"]:
        raw["weekdaySleepModeEnabled"] = dict(raw["weekdaySleepModeEnabled"])
        raw["weekdaySleepModeEnabled"]["Monday"] = {"sleepTime": 1380, "wakeTime": 420,
                                                    "isEnabled": True}
    return raw


def activity_entry(when: float, value: str, action_value: Any = None, serial="LR4C000001"):
    """One ``getLitterRobot4Activity`` entry. The timestamp format is a guess (ISO with
    milliseconds and Z); pylitterbot's ``to_timestamp`` accepts several."""
    stamp = dt.datetime.fromtimestamp(when, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"serial": serial, "measure": None, "timestamp": stamp, "value": value,
            "actionValue": action_value, "originalHex": None, "valueString": None,
            "stateString": None, "consumer": "app", "commandSource": None}


def _api_time(text: str) -> float:
    if not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", text):
        raise ValueError("Invalid Timestamp string")
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC).timestamp()


# ---------------------------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------------------------


class FakeWhisker:
    """The fake service. ``now`` is its clock (token expiry, the six-day history default)."""

    def __init__(self, *, robots=None, user_id="USER-9", username="kyle@example.com",
                 password="hunter2-secret", token_ttl=3600, now=time.time, order="desc",
                 ka_s=60.0, history_default_days=6):
        self.robots: list[dict] = robots if robots is not None else [robot_raw()]
        self.user_id = user_id
        self.username = username
        self.password = password
        self.sub = str(uuid.uuid5(uuid.NAMESPACE_DNS, username))
        self.salt = secrets.token_hex(16)
        self.token_ttl = token_ttl
        self.now = now
        self.order = order
        self.ka_s = ka_s
        self.history_default_days = history_default_days
        self.activities: list[dict] = []
        self.requests: list[dict] = []  # every request: path, headers, json
        self.ws_upgrades: list[dict] = []
        self.ws_messages: list[dict] = []  # what clients sent on the socket
        self.refresh_tokens: set[str] = set()
        self.generation = 0  # bumped by revoke(): older id tokens are refused
        self.reject_refresh = False
        self.fail: dict[str, list] = {"graphql": [], "cognito": [], "ws": []}
        self.ws_script: list[str] = []  # per new subscription: drop, silent, error, refuse
        self.silent = False
        self.subscribers: list[tuple[web.WebSocketResponse, str]] = []
        self.subscribed = asyncio.Event()
        self.counts = {"refresh": 0, "login": 0, "graphql": 0, "ws": 0}
        self._srp_sessions: dict[str, tuple[int, int, int]] = {}
        self.url = ""  # set by start()
        self._runner: web.AppRunner | None = None

    # -- tokens --------------------------------------------------------------------------

    def issue_tokens(self, ttl: float | None = None, with_refresh=True) -> dict:
        ttl = self.token_ttl if ttl is None else ttl
        now = int(self.now())
        exp = now + int(ttl)
        common = {"sub": self.sub, "iss": ISSUER, "exp": exp, "iat": now,
                  "auth_time": now, "gen": self.generation}
        idt = jwt.encode({**common, "aud": CLIENT_ID, "token_use": "id", "mid": self.user_id,
                          "email": self.username, "cognito:username": self.sub},
                         _key(), algorithm="RS256", headers={"kid": "fake-kid"})
        acc = jwt.encode({**common, "client_id": CLIENT_ID, "token_use": "access",
                          "username": self.sub, "scope": "aws.cognito.signin.user.admin"},
                         _key(), algorithm="RS256", headers={"kid": "fake-kid"})
        out = {"access_token": acc, "id_token": idt, "refresh_token": None}
        if with_refresh:
            rt = "fake-refresh-" + secrets.token_hex(24)
            self.refresh_tokens.add(rt)
            out["refresh_token"] = rt
        return out

    def revoke(self):
        """Refuse every id token issued so far (a 401 on the next query)."""
        self.generation += 1

    def _check_bearer(self, header: str | None) -> str | None:
        """None if the bearer is good, else why not."""
        if not header or not header.startswith("Bearer "):
            return "Valid authorization header not provided."
        try:
            claims = jwt.decode(header[7:], _key().public_key(), algorithms=["RS256"],
                                audience=CLIENT_ID, options={"verify_exp": False})
        except jwt.PyJWTError:
            return "Valid authorization header not provided."
        if claims.get("exp", 0) <= self.now():
            return "Token has expired."
        if claims.get("gen") != self.generation or claims.get("token_use") != "id":
            return "Valid authorization header not provided."
        return None

    # -- server --------------------------------------------------------------------------

    async def start(self, ssl_context=None) -> str:
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_post("/", self._cognito)
        app.router.add_get("/" + USER_POOL_ID + "/.well-known/jwks.json", self._jwks)
        app.router.add_post("/graphql", self._graphql)
        app.router.add_get("/graphql/realtime", self._realtime)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0, ssl_context=ssl_context)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = ("https" if ssl_context else "http") + f"://127.0.0.1:{port}"
        return self.url

    async def stop(self):
        for sock, _ in list(self.subscribers):
            await sock.close()
        if self._runner is not None:
            await self._runner.cleanup()

    def _log(self, request: web.Request, body: Any):
        self.requests.append({"path": request.path, "method": request.method,
                              "headers": dict(request.headers), "json": body})

    async def _fault(self, op: str) -> web.StreamResponse | None:
        """Apply the next scripted failure for ``op``, if any."""
        if not self.fail[op]:
            return None
        action = self.fail[op].pop(0)
        kind = action[0]
        if kind == "status":
            body = action[2] if len(action) > 2 else {"errors": [{"message": "fake failure"}]}
            return web.json_response(body, status=action[1])
        if kind == "garbage":
            return web.Response(text="<html>not json", content_type="application/json")
        if kind == "slow":
            await asyncio.sleep(action[1])
            return None
        if kind == "json":
            return web.json_response(action[1])
        if kind == "drop":
            raise ConnectionResetError("fake drop")
        raise ValueError(kind)

    # -- Cognito -------------------------------------------------------------------------

    def _cognito_error(self, code: str, message: str, status=400) -> web.Response:
        return web.Response(
            status=status, text=json.dumps({"__type": code, "message": message}),
            content_type="application/x-amz-json-1.1",
            headers={"x-amzn-ErrorType": code + ":http://internal.amazon.com/coral/",
                     "x-amzn-RequestId": str(uuid.uuid4())})

    def _cognito_ok(self, body: dict) -> web.Response:
        return web.Response(text=json.dumps(body), content_type="application/x-amz-json-1.1",
                            headers={"x-amzn-RequestId": str(uuid.uuid4())})

    def _auth_result(self, with_refresh: bool) -> dict:
        t = self.issue_tokens(with_refresh=with_refresh)
        result = {"AccessToken": t["access_token"], "ExpiresIn": self.token_ttl,
                  "TokenType": "Bearer", "IdToken": t["id_token"]}
        if with_refresh:
            result["RefreshToken"] = t["refresh_token"]
        return {"AuthenticationResult": result, "ChallengeParameters": {}}

    async def _cognito(self, request: web.Request) -> web.StreamResponse:
        body = json.loads(await request.read())
        self._log(request, body)
        if (fault := await self._fault("cognito")) is not None:
            return fault
        if request.content_type != "application/x-amz-json-1.1":
            return self._cognito_error("SerializationException", "bad content type")
        target = request.headers.get("X-Amz-Target", "")
        if body.get("ClientId") != CLIENT_ID:
            return self._cognito_error("ResourceNotFoundException", "User pool client does "
                                       "not exist.")
        if target == "AWSCognitoIdentityProviderService.InitiateAuth":
            flow = body.get("AuthFlow")
            params = body.get("AuthParameters") or {}
            if flow == "REFRESH_TOKEN_AUTH":
                self.counts["refresh"] += 1
                if self.reject_refresh or params.get("REFRESH_TOKEN") not in self.refresh_tokens:
                    return self._cognito_error("NotAuthorizedException",
                                               "Invalid Refresh Token")
                return self._cognito_ok(self._auth_result(with_refresh=False))
            if flow == "USER_SRP_AUTH":
                return self._srp_start(params)
            return self._cognito_error("InvalidParameterException", "unsupported flow")
        if target == "AWSCognitoIdentityProviderService.RespondToAuthChallenge":
            if body.get("ChallengeName") != "PASSWORD_VERIFIER":
                return self._cognito_error("InvalidParameterException", "bad challenge")
            return self._srp_verify(body.get("ChallengeResponses") or {})
        return self._cognito_error("UnknownOperationException", target)

    def _verifier(self) -> int:
        """v = g^x, x = H(salt | H(pool name | user id | ":" | password)) (Cognito SRP)."""
        up = _h((POOL_NAME + self.sub + ":" + self.password).encode())
        x = int(wsrp.hex_hash(wsrp.pad_hex(self.salt) + up), 16)
        return pow(wsrp.G, x, wsrp.N)

    def _srp_start(self, params: dict) -> web.Response:
        if params.get("USERNAME") != self.username:
            return self._cognito_error("NotAuthorizedException", "Incorrect username or "
                                       "password.")
        big_a = int(params.get("SRP_A", "0"), 16)
        if big_a % wsrp.N == 0:
            return self._cognito_error("InvalidParameterException", "SRP_A")
        v = self._verifier()
        b = secrets.randbits(1024) % wsrp.N
        big_b = (wsrp.K * v + pow(wsrp.G, b, wsrp.N)) % wsrp.N
        block = base64.b64encode(os.urandom(64)).decode()
        self._srp_sessions[block] = (big_a, b, big_b)
        return self._cognito_ok({"ChallengeName": "PASSWORD_VERIFIER", "ChallengeParameters": {
            "SALT": self.salt, "SECRET_BLOCK": block, "SRP_B": format(big_b, "x"),
            "USERNAME": self.sub, "USER_ID_FOR_SRP": self.sub}})

    def _srp_verify(self, resp: dict) -> web.Response:
        self.counts["login"] += 1
        block = resp.get("PASSWORD_CLAIM_SECRET_BLOCK", "")
        session = self._srp_sessions.pop(block, None)
        stamp = resp.get("TIMESTAMP", "")
        if session is None or resp.get("USERNAME") != self.sub:
            return self._cognito_error("NotAuthorizedException", "Incorrect username or "
                                       "password.")
        if not re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} [1-9]\d? "
                            r"\d\d:\d\d:\d\d UTC \d{4}", stamp):
            return self._cognito_error("InvalidParameterException", "TIMESTAMP format")
        when = dt.datetime.strptime(stamp, "%a %b %d %H:%M:%S UTC %Y").replace(tzinfo=dt.UTC)
        if abs(when.timestamp() - self.now()) > 300:
            return self._cognito_error("NotAuthorizedException", "TIMESTAMP skew")
        big_a, b, big_b = session
        v = self._verifier()
        u = wsrp.calculate_u(big_a, big_b)
        s = pow(big_a * pow(v, u, wsrp.N) % wsrp.N, b, wsrp.N)
        key = wsrp.hkdf(bytes.fromhex(wsrp.pad_hex(s)), bytes.fromhex(wsrp.pad_hex(format(u, "x"))))
        msg = (POOL_NAME.encode() + self.sub.encode() + base64.b64decode(block) + stamp.encode())
        expected = base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, resp.get("PASSWORD_CLAIM_SIGNATURE", "")):
            return self._cognito_error("NotAuthorizedException", "Incorrect username or "
                                       "password.")
        return self._cognito_ok(self._auth_result(with_refresh=True))

    async def _jwks(self, request: web.Request) -> web.Response:
        self._log(request, None)
        return web.json_response(jwks())

    # -- GraphQL -------------------------------------------------------------------------

    async def _graphql(self, request: web.Request) -> web.StreamResponse:
        raw = await request.read()
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        self._log(request, body)
        self.counts["graphql"] += 1
        if (fault := await self._fault("graphql")) is not None:
            return fault
        if (why := self._check_bearer(request.headers.get("Authorization"))) is not None:
            return web.json_response(
                {"errors": [{"errorType": "UnauthorizedException", "message": why}]},
                status=401)
        query = body.get("query", "") if isinstance(body, dict) else ""
        variables = body.get("variables") or {}
        if "getLitterRobot4ByUser" in query:
            if variables.get("userId") != self.user_id:
                return web.json_response({"data": {"getLitterRobot4ByUser": []}})
            return web.json_response({"data": {"getLitterRobot4ByUser": self.robots}})
        if "getLitterRobot4BySerial" in query:
            robot = next((r for r in self.robots if r["serial"] == variables.get("serial")),
                         None)
            return web.json_response({"data": {"getLitterRobot4BySerial": robot}})
        if "getLitterRobot4Activity" in query:
            return self._activity(variables)
        return web.json_response({"data": None, "errors": [
            {"errorType": "ValidationError", "message": "unknown query"}]})

    def _activity(self, variables: dict) -> web.Response:
        try:
            start = (_api_time(variables["startTimestamp"])
                     if variables.get("startTimestamp") else
                     self.now() - self.history_default_days * 86400)
            end = _api_time(variables["endTimestamp"]) if variables.get("endTimestamp") else None
        except ValueError as exc:
            return web.json_response({"data": {"getLitterRobot4Activity": None}, "errors": [
                {"errorType": "Lambda:Unhandled", "message": str(exc)}]})
        def when(e):
            return dt.datetime.strptime(e["timestamp"], "%Y-%m-%dT%H:%M:%S.000Z") \
                .replace(tzinfo=dt.UTC).timestamp()
        picked = [e for e in self.activities
                  if when(e) >= start and (end is None or when(e) <= end)]
        picked.sort(key=when, reverse=self.order == "desc")
        limit = variables.get("limit") or 100
        return web.json_response({"data": {"getLitterRobot4Activity": picked[:limit]}})

    # -- realtime ------------------------------------------------------------------------

    async def _realtime(self, request: web.Request) -> web.StreamResponse:
        header = json.loads(base64.b64decode(request.query.get("header", "e30=")))
        self.ws_upgrades.append({"headers": dict(request.headers), "header": header,
                                 "payload": request.query.get("payload")})
        self.counts["ws"] += 1
        action = self.ws_script.pop(0) if self.ws_script else None
        if action == "refuse" or self._check_bearer(header.get("Authorization")) is not None:
            return web.json_response({"errors": [{"errorType": "UnauthorizedException"}]},
                                     status=401)
        if action == "refuse500":
            return web.Response(status=503, text="unavailable")
        sock = web.WebSocketResponse(protocols=("graphql-ws",), autoping=True)
        await sock.prepare(request)
        ka_task = None
        sub_id = None
        try:
            async for msg in sock:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                self.ws_messages.append(data)
                if data.get("type") == "start":
                    sub_id = data.get("id")
                    ok = self._check_start(data)
                    if not ok:
                        await sock.send_json({"id": sub_id, "type": "error", "payload": {
                            "errors": [{"errorType": "UnauthorizedException",
                                        "message": "Permission denied"}]}})
                        continue
                    if action == "error":
                        await sock.send_json({"id": sub_id, "type": "error", "payload": {
                            "errors": [{"errorType": "InternalFailure",
                                        "message": "fake error frame"}]}})
                        continue
                    await sock.send_json({"id": sub_id, "type": "start_ack"})
                    if action == "drop":
                        await asyncio.sleep(0.05)
                        request.transport.close()
                        break
                    self.subscribers.append((sock, sub_id))
                    self.subscribed.set()
                    if action != "silent":
                        ka_task = asyncio.ensure_future(self._ka(sock))
                elif data.get("type") == "stop":
                    await sock.send_json({"id": data.get("id"), "type": "complete"})
        finally:
            if ka_task is not None:
                ka_task.cancel()
            self.subscribers = [(s, i) for s, i in self.subscribers if s is not sock]
            if not self.subscribers:
                self.subscribed.clear()
        return sock

    def _check_start(self, data: dict) -> bool:
        try:
            payload = data["payload"]
            auth = payload["extensions"]["authorization"]
            inner = json.loads(payload["data"])
        except (KeyError, TypeError, ValueError):
            return False
        return (self._check_bearer(auth.get("Authorization")) is None
                and auth.get("host") == "lr4.iothings.site"
                and "litterRobot4StateSubscriptionByUser" in inner.get("query", "")
                and inner.get("variables", {}).get("userId") == self.user_id)

    async def _ka(self, sock: web.WebSocketResponse):
        while not sock.closed:
            await asyncio.sleep(self.ka_s)
            if self.silent or sock.closed:
                continue
            try:
                await sock.send_json({"type": "ka"})
            except ConnectionError:
                return

    async def push(self, update: dict, *, partial=False, index=0, raw_text: str | None = None):
        """Change robot ``index`` and send it to every subscriber: the whole robot, or with
        ``partial`` only the changed keys (and ``unitId``)."""
        robot = self.robots[index]
        robot.update(update)
        item = dict(update, unitId=robot["unitId"]) if partial else dict(robot)
        for sock, sub_id in list(self.subscribers):
            text = raw_text if raw_text is not None else json.dumps({
                "id": sub_id, "type": "data", "payload": {"data": {
                    "litterRobot4StateSubscriptionByUser": {"robots": [item]}}}})
            await sock.send_str(text)

    async def drop_sockets(self):
        """Cut every live socket without a close frame."""
        for sock, _ in list(self.subscribers):
            sock._req.transport.close()
