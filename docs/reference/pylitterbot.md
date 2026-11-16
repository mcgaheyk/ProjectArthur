# pylitterbot reference (verified)

Verified 2026-11-15 against the source of release **2025.8.0** (git tag `2025.8.0`), which is
the version in `uv.lock`. Paths are relative to the package
(`.venv/lib/python3.13/site-packages/pylitterbot/`). Re-check anything here against the
installed source before relying on it in new code, and add what you learn.

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
- `await account.disconnect()` unsubscribes and closes the session.
- Members: `robots`, `pets`, `get_robots(cls)`, `refresh_robots()`, `load_robots()`,
  `load_pets()`.

### Errors
- Bad credentials or HTTP 401 in `connect()` → `LitterRobotLoginException`.
- Other HTTP errors or no connection in `connect()` → `LitterRobotException`.
- Timeouts and some `ClientConnectionError` subtypes are **not** wrapped.
- `load_robots()` and `refresh_robots()` **swallow** errors and only log them; a failed fetch
  keeps the old robots. A 404 can leave an empty robot list. Treat "no robots" and "no update
  in N minutes" as errors ourselves.
- `robot.refresh()` raises raw aiohttp errors. A failed token refresh raises a raw botocore
  `ClientError`.

## Live updates (`robot/__init__.py`, `transport.py`, `event.py`)
- `await robot.subscribe()` / `await robot.unsubscribe()`, or `subscribe_for_updates=True`.
- `robot.on(EVENT_UPDATE, callback)` returns an unsubscribe callable.
  `from pylitterbot.event import EVENT_UPDATE` (value `"update"`).
- The callback is **synchronous**, gets **no arguments**, and its exceptions are **silently
  swallowed**. Wrap it and log.
- It fires on every push message and every `refresh()`, even when nothing changed. Diff
  ourselves.
- One shared WebSocket per account per robot class, graphql-ws protocol. Each message carries
  the full robot model.
- Reconnects on its own: backoff from 1 s doubling to 300 s; a 180 s stale watchdog closes and
  reconnects a silent socket.
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
| `litter_level` | `float` 0–100 | |
| `litter_level_state` | `LitterLevelState \| None` | `OVERFILL`, `OPTIMAL`, `REFILL`, `LOW`, `EMPTY` |
| `pet_weight` | `float`, **pounds** | `catWeight` |
| `is_sleeping` | `bool` | `sleepStatus != "WAKE"` |
| `sleep_mode_enabled`, `sleep_mode_start_time`, `sleep_mode_end_time` | | |
| `clean_cycle_wait_time_minutes` | `int` | 3, 7, 15, 25 or 30; default 7 |
| `is_laser_dirty` | `bool` | |
| `power_type` | `"AC"`/`"DC"`/`"NC"` | `power_status` is a deprecated alias |
| `hopper_status`, `is_hopper_removed` | | Litter hopper accessory |
| `last_seen` | UTC `datetime \| None` | |
| `cycle_count` | `int` | **Lifetime odometer** on the LR4, not cycles since the drawer was emptied |
| `cycle_capacity`, `cycles_after_drawer_full` | `int` | |
| `name`, `serial`, `id`, `model`, `firmware`, `timezone` | | |

- `robot.to_dict()` returns the raw data. Useful raw keys with no property: `robotStatus`,
  `robotCycleState`, `robotCycleStatus`, `displayCode`, `catDetect`, `isCatDetectPending`,
  `weightSensor`, `pinchStatus`, `isDFIPartialFull`, `wifiRssi`, `isBonnetRemoved`,
  `globeMotorFaultStatus`. The full list is `LITTER_ROBOT_4_MODEL` in `robot/models.py`.
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
  API returns about six days. No pet id on entries.
- `get_insight()` counts cycles, not visits.
- `account.pets` (with `load_pets=True`): `Pet.weight`, `weight_history`
  (`WeightMeasurement(timestamp, weight)`), `get_visits_since(start)`,
  `fetch_weight_history(limit=50)`.

## Timings (from Whisker support and reviews, not measured by us)
- Wait before cleaning: 7 minutes by default, counted from when the cat leaves.
- Clean cycle: about 2 to 2.5 minutes.
- Cat-detected latency over the WebSocket: no published measurement. The spike measures it.

## History of breakage (so we know what to expect)
- April 2026: Whisker blocked the default User-Agent (403), then the LR4 endpoint returned 404.
  Library fixed within days; Home Assistant users waited three to four weeks.
- April–June 2026: `catWeight` stopped updating over the WebSocket for about two months.
- Whisker's terms prohibit automated tools. No reports of bans were found.
