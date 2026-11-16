# pylitterbot reference (verified)

Verified 2026-11-15 against the source of release **2025.8.0** (git tag `2025.8.0`), which is
the version in `uv.lock`. Paths are relative to the package
(`.venv/lib/python3.13/site-packages/pylitterbot/`). Re-check anything here against the
installed source before relying on it in new code, and add what you learn.

Re-checked 2026-11-15 against the **installed** copy (`_version.py` says `2025.8.0`) while
building the spike logger. Corrections and additions from that pass are marked
"(installed source)".

## Package
- `requires-python >= 3.11`. Runtime deps: `aiohttp`, `pyjwt`, `pycognito`. `pycognito` pulls
  in `boto3`/`botocore`, and `account.py`/`session.py` import `botocore.exceptions` directly.
  One outside measurement puts `import boto3` at about 25 MB; measure on the Pi.
- Skip the optional extras (`camera`, `mcp`).

## Logging in (`account.py`, `session.py`, `exceptions.py`)
- `Account(token=None, websession=None, token_update_callback=None, robot_types=None)`
- `await account.connect(username=None, password=None, load_robots=False,
  subscribe_for_updates=False, load_pets=False, robot_types=None)`
- Pass `robot_types=[LitterRobot4]` to skip the queries for other products.
- `token_update_callback` receives a dict with `access_token`, `id_token`, `refresh_token`.
  Save it and pass it back as `Account(token=...)` to avoid a password login every start.
  (installed source) Its type is `Callable[[dict | None], None]`: it is called as
  `callback(session.tokens)`, which is **`None`** when no tokens are held, so ignore `None`.
  It fires on the session's `EVENT_UPDATE`, which `session.login()` and
  `session.refresh_tokens()` emit, i.e. after a password login and after every token refresh
  (including one triggered inside a later request or a WebSocket reconnect).
- (installed source) `connect()` with a saved token: if the id token is expired (30 s
  leeway) and a refresh token is held, it refreshes; if the token is not expired it is used
  as-is with no server check. A token the server rejects shows up as a 401 on the first
  request: the robot fetch swallows it (see below), but `load_pets=True` runs next and its
  401 is raised as `LitterRobotLoginException`. A failed refresh raises botocore
  `ClientError`, which `connect()` turns into `LitterRobotLoginException`; that handler
  reads `err.response['message']`, so an error without that key would surface as a
  `KeyError` chained to the `ClientError` (not seen, but classify by the exception chain).
- (installed source) `Account(token=..., ...)` decodes the id token's `mid` claim as the
  user id at construction (no network).
- `await account.disconnect()` unsubscribes and closes the session.
- Members: `robots`, `pets`, `get_robots(cls)`, `refresh_robots()`, `load_robots()`,
  `load_pets()`; also `user_id`, `session`, `get_robot(robot_id)`, `get_pet(pet_id)`.
- (installed source) The signature is `get_robots(robot_class, *, ignore_removed=False)`, and
  by default it returns only robots whose raw `isOnboarded` is `True`.

### Errors
- Bad credentials or HTTP 401 in `connect()` → `LitterRobotLoginException` (a subclass of
  `LitterRobotException`). (installed source) For a wrong password the Cognito
  `NotAuthorizedException` arrives as botocore `ClientError` and is wrapped the same way.
  pycognito's own `WarrantException` family (`ForceChangePasswordException`,
  `MFAChallengeException`, `TokenVerificationException`, in `pycognito/exceptions.py`) is
  **not** wrapped and escapes `connect()` raw. Treat it as a login failure too, never retry.
- Other HTTP errors or no connection in `connect()` → `LitterRobotException`.
- Timeouts and some `ClientConnectionError` subtypes are **not** wrapped.
- `load_robots()` and `refresh_robots()` **swallow** errors and only log them; a failed fetch
  keeps the old robots. A 404 can leave an empty robot list. Treat "no robots" and "no update
  in N minutes" as errors ourselves. (installed source) Each robot class is fetched with
  `asyncio.gather(..., return_exceptions=True)`; a failed class keeps its previously known
  robots, so on the **first** load it leaves none. `LitterRobot4.fetch_for_account` also
  returns `[]` for any response that is not the expected shape.
- `robot.refresh()` raises raw aiohttp errors. A failed token refresh raises a raw botocore
  `ClientError`. (installed source) An HTTP 500 is raised as `InvalidCommandException`. If the
  API answers with `getLitterRobot4BySerial: null`, `refresh()` calls `_update_data(None)` and
  raises `TypeError`, so catch broadly around it.

## Live updates (`robot/__init__.py`, `transport.py`, `event.py`)
- `await robot.subscribe()` / `await robot.unsubscribe()`, or `subscribe_for_updates=True`.
- `robot.on(EVENT_UPDATE, callback)` returns an unsubscribe callable.
  `from pylitterbot.event import EVENT_UPDATE` (value `"update"`).
- The callback is **synchronous**, gets **no arguments**, and its exceptions are **silently
  swallowed**. Wrap it and log. (installed source, `event.py`) `Event.emit` calls each
  listener inside a bare `try/except: pass`.
- (installed source) It is emitted at the end of `Robot._update_data()`, synchronously, in
  whichever task supplied the data: inside our own `await robot.refresh()` for a poll, or in
  the WebSocket monitor's task for a push. Checking `asyncio.current_task()` inside the
  callback therefore tells a poll from a push reliably; a flag alone can mislabel a push that
  lands while `refresh()` is awaiting. `load_robots()`/`refresh_robots()` emit it too for
  robots that already exist.
- It fires on every push message and every `refresh()`, even when nothing changed. Diff
  ourselves.
- One shared WebSocket per account per robot class, graphql-ws protocol. Each message carries
  the full robot model.
- Reconnects on its own: backoff from 1 s doubling to 300 s; a 180 s stale watchdog closes and
  reconnects a silent socket. (installed source, `transport.py`: `reconnect_base=1.0`,
  `BACKOFF_SECONDS_MAX = 300.0`, `WEBSOCKET_STALE_TIMEOUT_SECONDS = 180.0`.) Push messages are
  matched to the robot by `unitId`.
- (installed source) **The WebSocket URL carries the bearer token**, base64-encoded in its
  `header=` query parameter (`LitterRobot4._ws_config_factory`). The monitor logs connection
  errors at WARNING as `WebSocket error (%s)`, and aiohttp errors can include the URL. Scrub
  library log output before it reaches a console or file.
- It does **not** poll after reconnecting. Run our own `await robot.refresh()` (one request)
  every five minutes, which is also what Home Assistant does.

## LitterRobot4 (`robot/litterrobot4.py`, bases in `robot/litterrobot.py`, `robot/__init__.py`)

| Property | Type | Notes |
|---|---|---|
| `status` | `LitterBoxStatus` | Derived; see below |
| `status_code` | `str \| None` | e.g. `"RDY"`; the raw `robotStatus` when status is `UNKNOWN` |
| `status_text` | `str` | Human label |
| `is_online`, `is_on` | `bool` | `isOnline`; `unitPowerStatus == "ON"` |
| `waste_drawer_level` | `float` 0–100 | `DFILevelPercent` |
| `is_waste_drawer_full` | `bool` | `isDFIFull` |
| `is_drawer_full_indicator_triggered` | `bool` | (installed source) also `isDFIFull` |
| `litter_level` | `float` 0–100 | (installed source) `litterLevelPercentage * 100` |
| `litter_level_state` | `LitterLevelState \| None` | `OVERFILL`, `OPTIMAL`, `REFILL`, `LOW`, `EMPTY` |
| `pet_weight` | `float`, **pounds** | `catWeight` |
| `is_sleeping` | `bool` | `sleepStatus != "WAKE"` |
| `sleep_mode_enabled`, `sleep_mode_start_time`, `sleep_mode_end_time` | | |
| `clean_cycle_wait_time_minutes` | `int` | 3, 7, 15, 25 or 30; default 7 |
| `is_laser_dirty` | `bool` | |
| `power_type` | `"AC"`/`"DC"`/`"NC"` | `power_status` is a deprecated alias |
| `hopper_status`, `is_hopper_removed` | | Litter hopper accessory |
| `last_seen` | UTC `datetime \| None` | |
| `cycle_count` | `int` | **Lifetime odometer** on the LR4 (`odometerCleanCycles`), not cycles since the drawer was emptied |
| `cycle_capacity`, `cycles_after_drawer_full` | `int` | (installed source) `DFINumberOfCycles` (default 30), `DFIFullCounter` |
| `globe_motor_fault_status`, `globe_motor_retract_fault_status` | `GlobeMotorFaultStatus` | (installed source) |
| `firmware_update_status` | `str` | (installed source) default `"UNKNOWN"` |
| `scoops_saved_count`, `surface_type`, `usb_fault_status`, `wifi_mode_status` | | (installed source) |
| `name`, `serial`, `id`, `model`, `firmware`, `timezone` | | (installed source) raw keys `name`, `serial`, `unitId`, `unitTimezone`; `firmware` is a combined `"ESP: … / PIC: … / TOF: …"` string |

(installed source) Two further notes:
- `litter_level_calculated` is **stateful**: each read feeds raw `litterLevel` (a distance in
  mm to the top ToF sensor, about 441 full to 471 very low) into a smoother stored on the
  robot. Do not read it just to log it.
- `status` indexes `self._data["robotCycleState"]` and `self._data["robotStatus"]` directly,
  so it raises `KeyError` if a payload ever lacks them.

- `robot.to_dict()` returns the raw data. Useful raw keys with no property: `robotStatus`,
  `robotCycleState`, `robotCycleStatus`, `displayCode`, `catDetect`, `isCatDetectPending`,
  `weightSensor`, `pinchStatus`, `isDFIPartialFull`, `wifiRssi`, `isBonnetRemoved`,
  `globeMotorFaultStatus`. The full list is `LITTER_ROBOT_4_MODEL` in `robot/models.py`.
  (installed source) All of those, plus `DFILevelPercent`, `isDFIFull`, `litterLevel`,
  `litterLevelPercentage`, `sleepStatus` and `unitPowerStatus`, are present in
  `LITTER_ROBOT_4_MODEL`; the spike logger's test asserts it. Identifying keys in the same
  payload: `unitId`, `serial`, `userId`, `name`, `sessionId`. The library's own key list for
  redaction is `REDACT_FIELDS` in `utils.py` (`utils.redact()`).
- Methods: `refresh()`, `get_activity_history(limit=100, start=None)`, `get_insight(days=30)`.

## How `status` is derived
In order: not online → `OFFLINE`; not on → `OFF`; `displayCode` in the fault map; then
`robotCycleState` in the cycle-state map; then `robotStatus` in the status map (default
`UNKNOWN`); if that gives `READY`, a `DC_CAT_DETECT` display code makes it `CAT_DETECTED`, and
a full drawer makes it `DRAWER_FULL`.

- `robotStatus`: `ROBOT_IDLE`→READY, `ROBOT_CAT_DETECT`→CAT_DETECTED,
  `ROBOT_CAT_DETECT_DELAY`→CAT_SENSOR_TIMING, `ROBOT_CLEAN` and `ROBOT_FIND_DUMP`→CLEAN_CYCLE,
  `ROBOT_EMPTY`→EMPTY_CYCLE, `ROBOT_BONNET`→BONNET_REMOVED, `ROBOT_POWER_UP`/`_DOWN`/`_OFF`.
- `robotCycleState`: `CYCLE_STATE_CAT_DETECT`→CAT_SENSOR_INTERRUPTED,
  `CYCLE_STATE_PAUSE`→PAUSED.
- Fault `displayCode`: `DC_CAT_DETECT_30M`→CAT_SENSOR_FAULT, `DC_MTR_FAULT_OT_AMP` and
  `DC_MTR_FAULT_OT_SLP`→OVER_TORQUE_FAULT, `DC_MTR_FAULT_PINCH`→PINCH_DETECT.

**Statuses an LR4 can report:** OFFLINE, OFF, READY, CAT_DETECTED, CAT_SENSOR_TIMING,
CAT_SENSOR_INTERRUPTED, PAUSED, CLEAN_CYCLE, EMPTY_CYCLE, DRAWER_FULL, BONNET_REMOVED,
PINCH_DETECT, CAT_SENSOR_FAULT, OVER_TORQUE_FAULT, POWER_UP, POWER_DOWN, UNKNOWN.

**Never reported live by an LR4:** CLEAN_CYCLE_COMPLETE (history only), DRAWER_FULL_1,
DRAWER_FULL_2, the position faults, the three STARTUP_* members.

`LitterBoxStatus` (`enums.py`) codes: RDY, CD, CST, CSI, P, CCP, CCC, EC, DFS, DF1, DF2, BR, PD,
OTF, CSF, PWRU, PWRD, OFF, OFFLINE, and more. There is **no fault-classifier helper**; keep our
own fault set.

## History and pets (`activity.py`, `pet.py`)
- `get_activity_history()` returns `Activity(timestamp, action)` where `action` is a
  `LitterBoxStatus` or a string such as `"Pet Weight Recorded: 9.5 lbs"`. Without `start` the
  API returns about six days. No pet id on entries. (installed source) Signature
  `get_activity_history(limit=100, start=None)`: `limit < 1` raises
  `InvalidCommandException`; `start` is sent as whole UTC seconds rounded up, so entries at
  exactly `start` come back again (de-duplicate). Other strings: `"Clean Cycles: N"`,
  `"Litter Dispensed: x"`, `"Battery Backup"`, or the raw value when unmapped. A null answer
  raises `LitterRobotException`. The mapping is `ACTIVITY_STATUS_MAP` in
  `robot/litterrobot4.py` (e.g. `robotCycleStatusIdle` → `CLEAN_CYCLE_COMPLETE`).
- `get_insight()` counts cycles, not visits.
- `account.pets` (with `load_pets=True`): `Pet.weight`, `weight_history`
  (`WeightMeasurement(timestamp, weight)`), `get_visits_since(start)`,
  `fetch_weight_history(limit=50)`.
- (installed source) `weight_history` is filled from the `weightHistory` field of the pets
  query that `load_pets()` already makes, so reading it costs no request;
  `fetch_weight_history()` is one more request per pet. `Pet.weight` is
  `lastWeightReading` or else the profile's estimated `weight`.

## Timings (from Whisker support and reviews, not measured by us)
- Wait before cleaning: 7 minutes by default, counted from when the cat leaves.
- Clean cycle: about 2 to 2.5 minutes.
- Cat-detected latency over the WebSocket: no published measurement. The spike measures it.

## History of breakage (so we know what to expect)
- April 2026: Whisker blocked the default User-Agent (403), then the LR4 endpoint returned 404.
  Library fixed within days; Home Assistant users waited three to four weeks.
- April–June 2026: `catWeight` stopped updating over the WebSocket for about two months.
- Whisker's terms prohibit automated tools. No reports of bans were found.
