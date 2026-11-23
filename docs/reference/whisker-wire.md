# Whisker's wire protocol, as pylitterbot speaks it (verified against source)

What goes over the wire between pylitterbot 2025.8.0 (with pycognito 2024.5.1 and boto3
1.44.3) and Whisker's cloud, for the Litter-Robot 4 only, read on 2026-11-20 in the installed
source (`.venv/lib/python3.13/site-packages/`). Written so `arthur/wire/` (our own client, for
the clock, D-0029) can speak it without pylitterbot. Paths below are relative to
`site-packages/`; `pl/` is `pylitterbot/`.

How each statement is known:

- **(source)**: read in the file and function named.
- **(ran)**: also seen by running pylitterbot, pycognito and boto3 themselves against our fake
  Whisker on 127.0.0.1 (`tests/test_wire_conformance.py`); the fake recorded the headers and
  bodies. This shows what the libraries send, not what Whisker answers.
- **(inferred)**: not in the source; reasoned, usually from the endpoint being AWS AppSync and
  Cognito. Check at the supervised live step.

**Nothing here has been checked against the real service by this work.** No request was
sent to Whisker or Amazon. The spike and the feeder (D-0005, D-0018) are the only evidence
that the real service answers as pylitterbot expects.

## Endpoints and ids (source)

| What | Value | Where |
|---|---|---|
| Cognito user pool | `us-east-1_rjhNnZVAm` | `pl/session.py`, `LitterRobotSession.USER_POOL_ID` (base64) |
| Cognito app client | `4552ujeu3aic90nf8qn53levmn`, no client secret | `LitterRobotSession.CLIENT_ID` (base64); `Cognito(...)` gets no `client_secret` |
| Cognito endpoint | `https://cognito-idp.us-east-1.amazonaws.com/` | boto3, region from the pool id (`pycognito/__init__.py`, `Cognito.__init__`) |
| JWKS | `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_rjhNnZVAm/.well-known/jwks.json` | `pycognito/__init__.py`, `Cognito.get_keys` (fetched with `requests`) |
| LR4 GraphQL | `https://lr4.iothings.site/graphql` | `pl/robot/litterrobot4.py`, `LR4_ENDPOINT` (line 43) |
| LR4 realtime | `wss://lr4.iothings.site/graphql/realtime` | `LitterRobot4._ws_config_factory` (`f"{LR4_ENDPOINT}/realtime"`; aiohttp turns https into wss) |
| Pets (not used by us) | `https://pet-profile.iothings.site/graphql/` | `pl/pet.py`, `PET_PROFILE_ENDPOINT` |
| v2 API (not used for LR4) | `https://v2.api.whisker.iothings.site`, with an `x-api-key` | `pl/const.py`, `pl/account.py` (`Account.__init__`) |

## Logging in with a password (source, ran)

`Account.connect` (`pl/account.py`) calls `LitterRobotSession.login` (`pl/session.py`), which
runs pycognito's `Cognito.authenticate` in a thread; that is `AWSSRP.authenticate_user`
(`pycognito/aws_srp.py`), two boto3 calls:

Both go as `POST /` to the Cognito endpoint with these headers (ran; boto3 adds them):
`Content-Type: application/x-amz-json-1.1`,
`X-Amz-Target: AWSCognitoIdentityProviderService.<Operation>`, `Accept-Encoding: identity`,
`User-Agent: Boto3/1.44.3 md/Botocore#1.44.3 ua/2.1 os/… lang/python#3.13… Botocore/1.44.3`,
`amz-sdk-invocation-id: <uuid>`, `amz-sdk-request: attempt=1; max=3`. **No AWS signature**:
botocore's model marks both operations `"auth": ["smithy.api#noAuth"]`
(`botocore/data/cognito-idp/2016-04-18/service-2.json.gz`), and the fake saw no
`Authorization` header. boto3 retries up to 3 attempts on throttling and 5xx.

1. **`InitiateAuth`**, body
   `{"AuthFlow": "USER_SRP_AUTH", "AuthParameters": {"USERNAME": <email>, "SRP_A": <hex>},
   "ClientId": …}`. `SRP_A` is `A = g^a mod N` in lower-case hex, unpadded
   (`AWSSRP.get_auth_params`); `a` is 128 random bytes mod N (`generate_random_small_a`).
   N is the 3072-bit group `N_HEX`, g = 2, k = H(`00`‖N_HEX‖`02`) (`AWSSRP.__init__`).
2. The answer must be `{"ChallengeName": "PASSWORD_VERIFIER", "ChallengeParameters":
   {"USER_ID_FOR_SRP", "SALT", "SRP_B", "SECRET_BLOCK", "USERNAME"}}`; any other challenge
   name raises `NotImplementedError` (`authenticate_user`).
3. The client computes (`get_password_authentication_key`, `compute_hkdf`, `process_challenge`):
   u = H(pad(A)‖pad(B)); x = H(pad(salt)‖H(`<pool name after "_">` ‖ USER_ID_FOR_SRP ‖ `:` ‖
   password)); S = (B − k·g^x)^(a + u·x) mod N; key = first 16 bytes of
   HMAC(HMAC(pad(u), pad(S)), `"Caldera Derived Key"`‖0x01); signature = base64
   HMAC-SHA256(key, pool name ‖ USER_ID_FOR_SRP ‖ base64-decoded SECRET_BLOCK ‖ timestamp).
   H is hex SHA-256 of the hex string's bytes, `pad` makes the hex even-length and adds `00`
   when the top bit is set. The timestamp is `"Fri Nov 20 12:05:09 UTC 2026"`, UTC, day of the
   month **not** zero-padded (`get_cognito_formatted_timestamp`).
4. **`RespondToAuthChallenge`**, body `{"ClientId", "ChallengeName": "PASSWORD_VERIFIER",
   "ChallengeResponses": {"TIMESTAMP", "USERNAME": <USERNAME from the challenge, else the
   email>, "PASSWORD_CLAIM_SECRET_BLOCK", "PASSWORD_CLAIM_SIGNATURE"}}`.
5. The answer is `{"AuthenticationResult": {"AccessToken", "IdToken", "RefreshToken",
   "ExpiresIn", "TokenType": "Bearer"}, "ChallengeParameters": {}}`, or a further challenge:
   `NEW_PASSWORD_REQUIRED` (raises `ForceChangePasswordException`), `SMS_MFA`,
   `SOFTWARE_TOKEN_MFA` (MFA exceptions), `DEVICE_SRP_AUTH` (answered only if device keys
   were given; pylitterbot gives none). Cognito's list also has `EMAIL_OTP`, `SELECT_MFA_TYPE`,
   `MFA_SETUP`, `CUSTOM_CHALLENGE` and others (botocore model, `ChallengeNameType`).
6. pycognito then **verifies both tokens' RS256 signatures** against the pool's JWKS
   (`Cognito._set_tokens` → `verify_token`), which is one more `GET` of the JWKS URL, and
   checks `aud` (the client id, id token only), `iss`, `exp` and `token_use`.

Errors (source: botocore's JSON protocol; ran against the fake): HTTP 400 with body
`{"__type": "NotAuthorizedException", "message": "Incorrect username or password."}` and the
code also in the `x-amzn-ErrorType` header. pylitterbot wraps botocore's `ClientError` as
`LitterRobotLoginException` (`Account.connect`); pycognito's challenge exceptions escape raw.

## Renewing tokens (source, ran)

`Cognito.renew_access_token` (`pycognito/__init__.py`): `InitiateAuth` with
`{"AuthFlow": "REFRESH_TOKEN_AUTH", "AuthParameters": {"REFRESH_TOKEN": …}, "ClientId": …}`.
The answer has new `AccessToken` and `IdToken` and **no `RefreshToken`** unless the pool
rotates them (inferred from Cognito's documentation; the source keeps the old one when the
key is missing, `_set_tokens`). A dead refresh token is `NotAuthorizedException`.

When pylitterbot renews (`pl/session.py`):

- **Before every request** (`LitterRobotSession.request`) and before opening the WebSocket
  (`Account.get_bearer_authorization`), if the **id token** expires within 30 s:
  `is_token_valid` decodes it with `leeway=-30`, so it counts as expired when
  `exp <= now + 30`.
- **Never on a 401.** `Session.request` logs the token's expiry and raises
  `ClientResponseError` (`resp.raise_for_status()`); nothing retries.
- At `Account.connect` with a saved token: renew if the id token is expired and a refresh
  token is held; else, with a password, log in; a valid saved token is used unchecked.
- **The first renewal in a process can be two renewals** (ran: the fake counted two). The
  first `get_user()` builds the `Cognito` object and, because tokens were given, calls
  `check_token()`, which renews if the **access** token's `exp` has passed (local naive time),
  then `verify_tokens()` (the JWKS `GET`); `_refresh_tokens` then renews again.
- After every login and renewal the session emits `EVENT_UPDATE`, and the account calls
  `token_update_callback(session.tokens)` with `{"access_token", "id_token",
  "refresh_token"}` (`Account.__init__`).

The bearer sent to Whisker is the **id token**, not the access token
(`Session.get_bearer_authorization` → `async_get_id_token`).

## The user id (source)

`LitterRobotSession.get_user_id`: the id token's payload, unverified, claim **`mid`**. It is
the `userId` in the robots query and the subscription. (Our client reads it the same way:
`arthur/wire/util.py`, `jwt_claims`.)

## Headers (ran, except where marked)

GraphQL `POST` from pylitterbot (aiohttp): `Host`, `User-Agent: pylitterbot/2025.8.0`,
`Accept: */*`, `Accept-Encoding: gzip, deflate`, `Content-Type: application/json`,
`Content-Length`, `authorization: Bearer <id token>`. The User-Agent is set in
`LitterRobotSession.generate_args` from `DEFAULT_USER_AGENT = f"pylitterbot/{__version__}"`
(`pl/session.py`, line 27); this is the fix for Whisker's April 2026 block of aiohttp's default
`Python/3.x aiohttp/3.x` agent (pylitterbot.md, "History of breakage").

WebSocket upgrade from pylitterbot: `Host`, `Upgrade: websocket`, `Connection: Upgrade`,
`Sec-WebSocket-Version: 13`, `Sec-WebSocket-Key`, `Sec-WebSocket-Protocol: graphql-ws`,
`User-Agent: pylitterbot/2025.8.0` (set in `WebSocketMonitor._connect`, `pl/transport.py`
line 187), `Accept: */*`, `Accept-Encoding: gzip, deflate`. **No
`Sec-WebSocket-Extensions`** (no compression offered). The subprotocol goes in as a plain
header (`_ws_config_factory`), so aiohttp does not check which one the server picks.

What our client sends instead (`arthur/wire/http.py`, `ws.py`; `const.USER_AGENT`):
`User-Agent: ProjectArthur/0.1 (+https://github.com/mcgaheyk/ProjectArthur)`,
`Accept: application/json`, `Accept-Encoding: identity`, `Connection: close`, and
`Authorization: Bearer <id token>`. **Whether Whisker accepts our User-Agent is unknown** until
the supervised live step.

## GraphQL queries (source)

Every query is `POST` with body `{"query": <text>, "variables": {…}}` and the bearer.
pylitterbot sets **no timeout** (aiohttp's default total is 300 s). An HTTP 500 whose JSON has
`"type": "InvalidCommandException"` becomes `InvalidCommandException`; other errors raise
`ClientResponseError` (`Session.request`).

- **The account's robots** (`LitterRobot4.fetch_for_account`):
  `query GetLR4($userId: String!) { getLitterRobot4ByUser(userId: $userId) <MODEL> }`,
  variables `{"userId": <mid>}`. Answer `{"data": {"getLitterRobot4ByUser": [robot, …]}}`;
  anything else is taken as no robots. `Account.load_robots` skips a robot with no `serial`,
  and `get_robots()` keeps only `isOnboarded: true` ones.
- **One robot** (`LitterRobot4.refresh`):
  `query GetLR4($serial: String!) { getLitterRobot4BySerial(serial: $serial) <MODEL> }`.
  A `null` robot makes `_update_data(None)` raise `TypeError`.
- `<MODEL>` is `LITTER_ROBOT_4_MODEL` (`pl/robot/models.py`): 72 fields, the seven days of
  `weekdaySleepModeEnabled` each `{sleepTime wakeTime isEnabled}`. `arthur/wire/lr4.py`
  `MODEL` has the same fields (a test compares them).

**Errors and an expired token** (inferred: AppSync's documented behaviour, not in the source):
an expired or bad token is HTTP 401 with `{"errors": [{"errorType": "UnauthorizedException",
"message": "Token has expired."}]}`; a GraphQL error is HTTP 200 with `"errors": [...]` and
`"data"` null or partial. pylitterbot ignores `errors` on queries and reads `data`.

## The live subscription (source, ran)

The robot class's `WebSocketMonitor` (`pl/transport.py`) holds one socket per account and
robot class.

- **URL** (`LitterRobot4._ws_config_factory`):
  `wss://lr4.iothings.site/graphql/realtime?header=<b64>&payload=<b64>` where `header` is
  standard base64 of `json.dumps({"Authorization": "Bearer <id token>", "host":
  "lr4.iothings.site"})` and `payload` is base64 of `{}` (`e30=`). **The token is in the URL.**
- **Client → server** (ran: the fake saw exactly `start`, then `stop` at disconnect):
  - No `connection_init`. The transport would send one if the config had it; the LR4 config
    does not. (So the real service does not require it: the spike received pushes for days.)
  - `start` (`_ws_subscribe`): `{"id": <uuid4>, "type": "start", "payload": {"data":
    <JSON string of {"query": "subscription GetLR4ByUser($userId: String!) {
    litterRobot4StateSubscriptionByUser(userId: $userId) { robots <MODEL> } }",
    "variables": {"userId": <mid>}}>, "extensions": {"authorization": {"Authorization":
    "Bearer <id token>", "host": "lr4.iothings.site"}}}}`. The bearer is fetched again, so it
    is renewed first if near expiry.
  - `stop` (`_ws_unsubscribe`): `{"id": <same>, "type": "stop"}`, on `account.disconnect()`.
- **Server → client** (`parse_websocket_message`): `data` with
  `payload.data.litterRobot4StateSubscriptionByUser.robots` a list of robot dicts; `error`
  (logged, nothing else happens); `start_ack`, `ka`, `complete` (ignored). A text message that
  is not JSON is skipped. aiohttp answers pings itself. (Inferred, AppSync: `connection_ack`
  carrying `connectionTimeoutMs`, and `ka` at an interval the service chooses.)
- **Applying an update** (`_ws_message_handler`): the first robot whose `str(unitId)` equals the
  robot's id goes to `Robot._update_data`, which does **`self._data.update(item)`**
  (`pl/robot/__init__.py`, line 160): keys in the message replace ours, keys missing from it
  keep their old value. The subscription asks for the whole model, so each message should
  carry every field; if Whisker ever sends a partial one, the old values stay, including an
  old `catWeight`. Whether every message is whole is not known (inferred; D-0017 rests on the
  weight arriving in the message that changes it).
- **Staleness**: no message for `WEBSOCKET_STALE_TIMEOUT_SECONDS` = 180 s closes the socket
  and reconnects (`_connect`). Any message counts, `ka` included.
- **Reconnects** (`_run`): after an exception, wait `delay` (1 s, doubling to
  `BACKOFF_SECONDS_MAX` = 300 s). **After a clean end (a stale close or the server closing)
  the delay resets to 1 s and it reconnects at once, with no wait.**
- No poll after reconnecting; Home Assistant and our feeder poll every 5 minutes themselves.

## Activity history (source, ran)

`LitterRobot4.get_activity_history(limit=100, start=None)`:
`query GetLR4Activity($serial: String!, $startTimestamp: String, $endTimestamp: String,
$limit: Int, $consumer: String) { getLitterRobot4Activity(serial: …, startTimestamp: …,
endTimestamp: …, limit: …, consumer: …) { serial measure timestamp value actionValue
originalHex valueString stateString consumer commandSource } }`, variables
`{"serial", "limit", "consumer": "app"}` and, if `start` is given, `"startTimestamp"`.

- `start` (since 2025.6.6): naive means UTC; a fraction rounds **up** to the next second; sent
  as `"YYYY-MM-DD HH:MM:SS"` UTC. The source comment says the API rejects ISO 8601 with
  `"Invalid Timestamp string"` and that **without a start it returns only about the last six
  days, whatever the limit**. `endTimestamp` is in the query but pylitterbot never sends it.
- `limit < 1` raises `InvalidCommandException`; a `null` answer raises
  `LitterRobotException`.
- Each entry becomes `Activity(to_timestamp(entry["timestamp"]), _parse_activity(entry))`;
  entries with a falsy timestamp are dropped. `_parse_activity` maps `value` through
  `ACTIVITY_STATUS_MAP`: statuses (`robotStatusCatDetect` → CAT_DETECTED,
  `robotCycleStatusDump` → CLEAN_CYCLE, `robotCycleStatusIdle` → CLEAN_CYCLE_COMPLETE,
  `robotCycleStateCatDetect` → CAT_SENSOR_INTERRUPTED, `DFIFullFlagOn` → DRAWER_FULL,
  `bonnetRemovedYes`, `catDetectStuckLaser`), or words: `catWeight` →
  `"Pet Weight Recorded: <actionValue> lbs"`, `odometerCleanCycles` →
  `"Clean Cycles: <actionValue>"`, `litterHopperDispensed` → `"Litter Dispensed: <…>"`,
  `powerTypeDC` → `"Battery Backup"`; anything else is the raw value (Kyle's history has
  `DFILevelPercent`).
- `to_timestamp` (`pl/utils.py`): drops a `Z`, appends `+00:00` if absent, pads or cuts a
  fraction to six digits, `datetime.fromisoformat`. The raw timestamp's format is not in the
  source or our logs (they store the parsed value).
- **Order is not documented.** Our logs cannot tell it either: the spike's first fetch (no
  start) returned exactly 100 entries covering 2026-11-09 to 11-15. Our client pages either
  way (below).

## How the record's fields are derived (source, ran)

`arthur.spikelog.build_record` reads these properties (`TRACKED_PROPERTIES`), then copies the
raw keys in `TRACKED_RAW_KEYS` as they are. `arthur/wire/record.py` derives each from the raw
data the same way; pylitterbot's own record and ours are equal for every combination in
`tests/test_wire_record.py` and for all 88 raw lines in Kyle's local logs (run with
`ARTHUR_REAL_LOGS`).

| Field | From raw (`pl/robot/litterrobot4.py` unless noted) |
|---|---|
| `status` | `isOnline is not True` → OFFLINE; `unitPowerStatus != "ON"` → OFF; `displayCode` in `FAULT_DISPLAY_CODE_STATUS_MAP` (`DC_CAT_DETECT_30M` → CAT_SENSOR_FAULT, `DC_MTR_FAULT_OT_AMP`/`_OT_SLP` → OVER_TORQUE_FAULT, `DC_MTR_FAULT_PINCH` → PINCH_DETECT; new in 2025.8.0, checked first); `robotCycleState` in `CYCLE_STATE_STATUS_MAP`; `robotStatus` in `LR4_STATUS_MAP`, else UNKNOWN; READY becomes CAT_DETECTED for `displayCode == "DC_CAT_DETECT"`, else DRAWER_FULL if `isDFIFull is True`. `robotCycleState` and `robotStatus` are indexed, not `.get` |
| `status_code`, `status_text` | the status's code and text (`pl/enums.py`); for UNKNOWN the code is raw `robotStatus` |
| `is_online` | `isOnline is True` |
| `is_on` | `unitPowerStatus == "ON"` (missing counts as "") |
| `is_sleeping` | `sleepStatus != "WAKE"` (missing counts as "WAKE") |
| `sleep_mode_enabled` | `pl/robot/litterrobot.py` via `pl/sleep_schedule.py`: any day of `weekdaySleepModeEnabled` (or `{}`) with `isEnabled` true; parsed in `_update_data` |
| `waste_drawer_level` | `DFILevelPercent`, default 0 |
| `is_waste_drawer_full`, `is_drawer_full_indicator_triggered` | `isDFIFull is True` |
| `litter_level` | `litterLevelPercentage * 100` (missing: 0; null: TypeError) |
| `litter_level_state` | `litterLevelState` through `to_enum` (exact, then case-insensitive; unknown → None) |
| `pet_weight` | `catWeight`, default 0, pounds |
| `clean_cycle_wait_time_minutes` | `cleanCycleWaitTime`, default 7 |
| `is_laser_dirty` | `isLaserDirty is True` |
| `power_type` | `unitPowerType`, default `"NC"` |
| `last_seen` | `pl/robot/litterrobot.py`: `to_timestamp(lastSeen)`, falsy → None |
| `cycle_count` | `int(odometerCleanCycles)`, default 0 (the lifetime odometer) |
| `cycle_capacity` | `int(DFINumberOfCycles)`, default 30 |
| `cycles_after_drawer_full` | `int(DFIFullCounter)`, default 0 |
| `globe_motor_fault_status` | `GlobeMotorFaultStatus.from_raw(globeMotorFaultStatus)` (`pl/enums.py`): None or blank → NONE; exact member; else PascalCase to SNAKE, strip `MTR_`/`MOTOR_`, add `FAULT_`, unknown → FAULT_UNKNOWN |
| `hopper_status` | `hopperStatus` through `to_enum` |
| `is_hopper_removed` | `isHopperRemoved is True` |

The feeder's live source also needs `cycle_count` (D-0025) and the activities as
`(timestamp, action)` with `action.text` (`arthur/sources/whisker.py`,
`seeds_from_activity`, `latest_weight_since`); `arthur/wire/lr4.py` `Activity` gives the same
(equal seeds in a test).

## Where our client differs from pylitterbot, on purpose

- Our User-Agent; `Accept-Encoding: identity` (we carry no decompressor); a new connection
  per request (`Connection: close`; requests are minutes apart).
- It does not fetch the JWKS or check token signatures: tokens arrive straight from Cognito
  over verified TLS, and we read only `exp` and `mid`. One renewal, not two.
- A 401 (or an `UnauthorizedException` GraphQL error) renews the token once and retries the
  request once; a second is `auth`. pylitterbot does neither.
- A 403 is `api`, not `auth`, with a note: the April 2026 User-Agent block was a 403.
- History in pages of 25 (`const.HISTORY_PAGE`). When a page is full, the next uses
  `endTimestamp` (if the server sent newest first) or a later `startTimestamp` (oldest first),
  bounds inclusive, duplicates dropped. **`endTimestamp`'s format is a guess** (the start's).
- After a socket ends cleanly it waits before reconnecting (pylitterbot does not), and an
  `error` message closes and reopens the socket (pylitterbot logs it and keeps a subscription
  that will never deliver). A refused upgrade or an Unauthorized error forces a token renewal.
- No pets, no commands, no insight, no firmware queries.
- Robot data with no `robotStatus` or `robotCycleState` at all: pylitterbot's `_update_data`
  raises (it reads `status` for `_update_minimum_cycles_left`), so no update event; ours
  keeps the record with `"<error: KeyError>"` in the three status fields. A *partial*
  message that leaves them out is not that case: both merge it into the data held, which
  still has them, and both apply it (ran: `partial_update` in `tests/test_wire_source.py`).
- The activity log's `code`: pylitterbot's source wrote it only for an `Enum` action; the
  shared line builder (`arthur/sources/spikeformat.py`, `activity_entries`) now writes it
  from our `Action.value` too, so both sources write the same history lines.

## The live source on our client, against pylitterbot's (ran, against the fake)

`arthur/sources/wire_source.py` (`WireSource`) is the feeder's source on this client; the
Mac runs it with `python -m arthur.feeder --source wire`. `tests/test_wire_source.py` plays
21 scripted scenarios through it and through the pylitterbot source (pylitterbot 2025.8.0 and
pycognito themselves), each against a fresh fake with the same made-up time, and compares
every `view()` sampled, every log line and every console line. They agree in 19: the real
visits of `tests/data/real-cycles.jsonl`, a visit with no weight pushed (with and without the
weight in the history), a missed visit found after a gap, a gap ending mid-visit, a clean with
no visit seen, two watched visits and the six-hourly read, drawer full, a pinch fault and a
pause, sleeping, offline and off, a dropped socket, a stale socket, a 500 on a poll, a
refused renewal (one password login), a refused password (retried after 10 and 20 minutes),
no robot, a partial message, a clean cycle the cat sensor interrupts (D-0032), and an hour
of traffic. Allowed everywhere: an error line's
`error` and `message` and the console text after "failed [kind]:" (the libraries' own
exceptions), and two console lines only ours writes ("Whisker live updates subscribed",
"Whisker live connection closed: …").

Where they differ, on purpose:

- **A 401 while polling.** pylitterbot never renews on a 401, so its source drops the token
  and logs in with the password (an error line, a fresh `connected`, a history read, and its
  heartbeat restarts). Ours renews the id token from the refresh token and retries once,
  which succeeds: no error and no password login. The screen is the same.
- **Two robots on the account.** pylitterbot's source shows the first one listed. Ours shows
  NO DATA (`no_robots`, the serials' last four digits in the message) until `WHISKER_SERIAL`
  chooses one; then it agrees with pylitterbot's choice.
- **After a stale socket** ours waits its back-off (1 s) before reconnecting; pylitterbot
  reconnects at once. Nothing on the screen or in the log shows it.
- **Pets**: our client reads none, so the log has no `pet_weight` history lines (the
  studio's last-visit fallback for a weight with no detection near it).
- **Stopping**: a login or request still running when the feeder stops gets 5 s to finish
  before it is cancelled; pylitterbot's source cancels it at once.
- **A poll held back, on the clock only (D-0033).** `Monitor` takes an optional `poll_ok`
  hook. While it says no, a routine poll that is due waits, up to 90 s
  (`monitor.POLL_HOLD_S`); the clock's program says no while a page or scene is on screen.
  A poll is never held while the live subscription is down (`Monitor.live_up`, set on
  `start_ack` and cleared when the socket closes) or when it is a retry after a failed
  poll. The Mac's feeder passes no hook, so there every poll runs when it is due and the
  comparisons above are unaffected.

## MicroPython 1.29 notes (ran, on the Mac's Homebrew build and the static ARMv7 build)

- `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` starts with `verify_mode = CERT_NONE`; set
  `CERT_REQUIRED` and load a CA file (`arthur/wire/net.py` refuses to run without one).
  `asyncio.open_connection(host, port, ssl=ctx, server_hostname=name)` is the same call on both
  Pythons (MicroPython `extmod/asyncio/stream.py`); the handshake happens on the first write,
  so a certificate error surfaces there. Wrong CA, wrong name and no CA are all refused
  (tests, and the clock spike).
- `Exception.__init__(self, …)` raises `AttributeError`; use `super().__init__`.
- Floats parse to the same bits as CPython (8,002 values checked) but **print differently**:
  `repr(0.07)` is `0.07000000000000001` (43 of 8,002 short decimals). Anything that writes
  floats as text on the clock must use `arthur/wire/util.py` `float_text`.
- No `datetime`, `hmac`, `base64`, `uuid`, `contextlib`, `str.ljust`; all replaced in
  `arthur/wire/util.py` and `client.py`.
- The ARMv7 build has double-precision floats and a 31-bit small int; epoch seconds keep
  microseconds.

## Seen from the real service (2026-11-20, 13:55 to 14:00 Boston time)

`scripts/wire_check.py --minutes 5` ran once from Kyle's Mac with the feeder stopped, under
CPython, from the saved token. No password login was tried and nothing was written back.

- **Our User-Agent is accepted.** `getLitterRobot4ByUser` answered 200 with
  `application/json; charset=UTF-8`, 2,395 bytes, neither chunked nor compressed. The record
  our client built from it read as the feeder's did that day (`READY`, drawer 15, litter 90).
- **Renewal** (`REFRESH_TOKEN_AUTH`) returned `AccessToken`, `IdToken`, `ExpiresIn` and
  `TokenType`: **no new refresh token**, so the saved one stays in use. `ExpiresIn` is 3600 and
  both the id and the access token live 3,600 s. The id token carries `mid`.
- **History** comes **newest first**. Its `timestamp` has the shape
  `YYYY-MM-DD HH:MM:SS.nnnnnnnnn`: a space, and nine decimal places (the check printed the
  shape only, with every digit replaced). A request with an
  `endTimestamp` was answered without error and returned entries; that the bound was honoured
  was not checked.
- **The socket** accepted the `graphql-ws` subprotocol and sent `start_ack` once, then `ka`
  every 60 s (gaps of 60.1, 59.9 and 60.0 s). It sent **no `connection_ack`**. The robot was
  idle for the five minutes, so no `data` message was seen.

### Live updates, through the Mac's feeder on `WireSource` (2026-11-20, from 15:10)

The feeder ran with `--source wire` against the real robot. From `logs/spike-20261120.jsonl`
and `logs/feeder-console.log`; nobody watched the clock.

- **`data` messages arrive and are applied.** A visit at 17:18 came through as live updates:
  the display code, then `CAT_DETECTED` 3 s later, then the clean cycle 8 min 24 s after
  detection with the cycle count, the drawer level, the litter level and `READY` 130 s after
  that. The robot's own history has no weight for this visit, so the 120-second fallback fired
  (D-0017), as it has for some visits under pylitterbot.
- **The weight arrives over the live connection.** At the 17:58 visit `pet_weight` changed
  (4.49 to 4.43) in a live update 60 s after detection, inside the 48 to 111 s seen under
  pylitterbot, and the feeder went from in use to waiting at that moment.
- Three hours with no warning or error: polls, heartbeats every 15 minutes, and the hourly
  token renewals passed silently.
- **Still not known:** whether a message carries every field or only the changed ones (the log
  records what changed in the record, not the message's keys; either way the merge gave the
  right states).

## Unknown until the supervised live step

Each with the smallest check that settles it (with the Mac's feeder stopped).
`scripts/wire_check.py` makes all but the last two in one run, read-only, from the saved
token (`uv run --env-file .env python scripts/wire_check.py --minutes 5`); it prints no token,
user id or serial. It ran once against the real service on 2026-11-20 (section above); the rows
that run settled are marked.

| Unknown | Check |
|---|---|
| ~~Whisker accepts our User-Agent~~ | **Settled: accepted** |
| The refresh token's lifetime | Renewal returns no new refresh token (settled). The lifetime is a pool setting we cannot read; it shows only when a renewal fails (Cognito's default is 30 days) |
| ~~The id token's lifetime~~ | **Settled: 3,600 s** |
| ~~Whether the server sends `connection_ack`, and `ka`'s interval~~ | **Settled: no `connection_ack`; `ka` every 60 s** |
| Whether every `data` message carries every field | Two visits came through correctly either way (above). To settle it, log each message's key count |
| What an expired token looks like on GraphQL and on the open socket | Keep a socket open past the id token's expiry; make one query with an expired token |
| Whether `endTimestamp` is honoured | Order and format are settled (above). Compare the entries returned with and without the bound |
| ~~Chunked or compressed answers~~ | **Not seen** on the robots query; other answers were not inspected |
| Whether a password login by our SRP is accepted | One login. Only if the refresh token is already dead; it costs a login on the account |
| Cognito's answer to an SRP login from the clock's slow arithmetic | The challenge's timestamp must be close to Cognito's clock; the clock's time was exact on 2026-11-20 |

If a `data` message leaves fields out, both sources merge it into what they hold, so the old
values stay. For D-0017 that means: a message without `catWeight` is not a weight reading,
the visit stays in use, and 120 s after detection (`cat_left_fallback_s`) the source asks
the history once for the visit's weight (waiting with a countdown if it is there, without
one if not); a message without the status keeps the old status until one carries it. The
`--source wire` run on the Mac is where this gets seen: each state line's `changed` and the
`raw` of a status change show what arrived.
