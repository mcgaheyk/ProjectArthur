# PETLIBRO cloud API reference (Granary confirmed live; fountain not)

Read on 2026-11-16 from the `dev` branch of `jjjonesjr33/petlibro` (release v1.4, 2026-11-05):
`custom_components/petlibro/api.py`, `devices/feeders/granary_smart_feeder.py`,
`devices/fountains/*.py` and `docs/api-documentation.md`. That project reverse-engineered the
API the PETLIBRO phone app uses. PETLIBRO publishes no API documentation.

**Confirmed live on 2026-11-16** by `scripts/spike_petlibro.py` against Kyle's Granary
(PLAF103, software 1.1.15, hardware 1.0.0), through a second account the Granary is shared to.
Items marked **(live)** were seen in that run's log (`logs/petlibro-20261116.jsonl`).
Everything about fountains is still from source only: Kyle does not own one yet.

Naming: in this project "the feeder" is our Python service. The PETLIBRO PLAF103 is "the
Granary".

## Rules for our code
- Only `arthur/sources/` (and the spike) may call this API.
- **Read only.** The API also has calls that dispense food and change settings. We never call
  them. The spike's client refuses any path that is not on its read list.
- One login per run. An account can be logged in from one place at a time, so a login here logs
  that account out of the phone app. Use a second account with the devices shared to it. Never
  run two copies of our code on the same account.
- Poll no faster than once a minute; every five minutes is the default.
- Fail visibly. The integration this was read from keeps showing old values when a call fails
  (its issue #295). Ours must not.
- Never log the token, the password or its hash, the email, a device serial or a MAC address.
- **Choose fields by product.** Every device answers with the same wide shape, so a Granary
  reports `weightPercent: 0` and a fountain would report grain fields. Read water fields only
  from `PLWF*` devices and food fields only from `PLAF*` devices. (live)

## Transport
- Host: `https://api.us.petlibro.com`. The integration knows no other region. (live)
- Every call is a `POST` with a JSON body and `Content-Type: application/json`.
- Headers the integration sends on every call, copied from the Android app:
  `source: ANDROID`, `language: EN`, `timezone: <IANA zone>`, `version: 1.3.45`.
  They work (live). Whether the server requires them is not known.
- After login, every call carries `token: <token>` as a header. (live)
- Every response is `{"code": <int>, "msg": <str or null>, "data": <payload>}` with HTTP 200.
  `code` 0 is success. (live) The integration retries HTTP 5xx twice, two seconds apart.

| Code | Name | Meaning |
|---|---|---|
| 0 | | Success |
| 1009 | `NOT_YET_LOGIN` | The token is not valid (expired, or the account logged in elsewhere). Log in again |
| 1101 | `MEMBER_NOT_EXIST` | No such account on this API: a PETLIBRO Lite account, one made on the website, or a non-US one |
| 1102 | `ILLEGAL_PASSWORD` | Password rejected |

The integration's wiki says a password longer than 18 characters gets 1102. **That did not
hold here:** Kyle's second account has a longer one and logged in. (live)

## Login
`POST /member/auth/login`

```json
{
  "appId": 1,
  "appSn": "c35772530d1041699c87fe62348507a8",
  "country": "US",
  "email": "<email>",
  "password": "<md5 hex digest of the UTF-8 password>",
  "phoneBrand": "",
  "phoneSystemVersion": "",
  "timezone": "<IANA zone>",
  "thirdId": null,
  "type": null
}
```

`data.token` is the token. `appId` and `appSn` are constants from the app, not secrets of ours.
Whether the `timezone` sent here changes anything on the account is not known, so send the real
local zone, never a placeholder.

**A shared, non-owner account can log in and read everything below.** (live)

`POST /member/auth/logout` ends the session. We do not call it.

## Reading devices
`POST /device/device/list` with body `{}`. `data` is a list. Per device: `deviceSn`,
`productName` (`Granary Smart Feeder`), `productIdentifier` (`PLAF103`), `name`, `mac`,
`softwareVersion`, `hardwareVersion`, `online`, `deviceShareState` (1 for a device shared to
this account), `shareId`. (live)

The calls below take `{"id": "<deviceSn>", "deviceSn": "<deviceSn>"}`.

| Call | Works on the Granary | What it returns |
|---|---|---|
| `/device/device/realInfo` | Yes (live) | The current state. About 60 fields; the useful ones are below |
| `/data/data/realInfo` | Yes (live) | Nearly the same payload, plus `desiccantState`, `feedingMode`, `exceptionMessage`, `batterySupply8Hours` and the `remainingGrain*` fields. The integration only uses it for fountains and the Granary 2 |
| `/device/data/grainStatus` | Yes (live) | Only `todayFeedingTimes`, `todayFeedingQuantity`, `todayFeedingQuantities`. No `surplusGrain`, whatever the integration's notes say |
| `/device/device/baseInfo` | Yes (live) | Settings: notification switches, `enableFeedingPlan`, `unitType`, `timezone`, `productName`. `name` came back empty for the shared account. No state worth polling |
| `/data/deviceDrinkWater/todayDrinkData` | Not called | Fountains: `todayTotalMl`, `todayTotalTimes`, `avgDrinkDuration`, `yesterdayTotalMl`, `yesterdayTotalTimes` |

### Granary state fields (live, from `realInfo`)

| Field | Seen | Meaning |
|---|---|---|
| `online` | `true` | The cloud can reach it |
| `lastOnlineTime` | epoch ms | Six minutes before the first poll. Whether it is the last reconnect or a heartbeat is not known yet |
| `surplusGrain` | `true` | There is food. **False means low.** Not yet seen false |
| `warehouseSurplusGrain` | `"GOOD"` | A named food state. Its other values are not known yet; it may say more than the true/false flag |
| `leftWarehouseSurplusGrain`, `rightWarehouseSurplusGrain` | `true` | Two-hopper models; both simply true here |
| `grainOutletState` | `true` | The outlet is clear, by the integration's reading. Not yet seen false |
| `runningState` | `"IDLE"` | `RUNNING` while dispensing, per the integration |
| `errorState`, `deviceStoppedWorking`, `barnDoorError` | `false` | Fault flags |
| `exceptionMessage` | `""` | Fault text (`/data/data/realInfo` only) |
| `batteryState` | `"low"` | Backup batteries. With `electricQuantity: 0` and `powerMode: 1` this most likely means no batteries are fitted and it is on mains. Not confirmed |
| `electricQuantity` | `0` | Backup battery percent |
| `powerMode`, `powerType` | `1`, `1` | On mains, by the integration's notes for `powerMode` |
| `remainingDesiccantDays` | `10` | Days until the desiccant is due. It is here, not in `baseInfo` |
| `desiccantState` | `"GOOD"` | `/data/data/realInfo` only |
| `vacuumState`, `pumpAirState` | `true` | The freshness seal |
| `wifiRssi`, `wifiRssiLevel` | `-38`, `4` | Signal |
| `timezone` | IANA zone | The device's zone |

Present but meaningless on a Granary: `weight` (0.0), `weightPercent` (0), `weightState`
(`NORMAL`), `waterBoxStatus` (-1), `todayTotalMl` (0), `remainingGrain` and its left/right
variants (0; they belong to the Granary 2's bowl sensor), `cameraAuthInfo`.

### Feed log
`POST /device/workRecord/list` takes a different body:

```json
{
  "deviceSn": "<deviceSn>",
  "startTime": <epoch ms>,
  "endTime": <epoch ms>,
  "size": 25,
  "type": ["GRAIN_OUTPUT_SUCCESS"]
}
```

`data` is a list of days, newest first, each `{"recordTime": "2026/11/16", "dayOfWeek": 1,
"workRecords": [...]}`. (live) A record has:

| Field | Example | |
|---|---|---|
| `type` | `GRAIN_OUTPUT_SUCCESS` | Also saw `OFFLINE`, so the `type` filter in the request is not strict |
| `eventType` | `FEEDING_PLAN_SUCCESS` | A scheduled meal. A manual feed presumably differs; not seen |
| `recordTime` | epoch ms | When it happened |
| `formatRecordTime` | `2026-11-16 05:45` | The same, in the device's zone |
| `expectGrainNum`, `actualGrainNum` | `1`, `1` | Portions planned and dispensed |
| `alarmTag` | `false` | True on the `OFFLINE` record |
| `content` | text | A message template for the app |

## Fountains (from source only)
- `weightPercent`: water level, 0 to 100. `weight`: water in grams.
- One user report (integration issue #307, PLWF305): `realInfo.weightState` is `NORMAL`,
  `LOW_WATER` (about 30%) or `LACK_WATER` (about 12%, pump stops); `waterBoxStatus` 0 means the
  tank is empty or lifted off; `weight` reads 0 in both cases.
- Dockstream 2 models add `lowWater` and `waterStopSwitch` on `/data/data/realInfo`.

## Not confirmed
- Anything about a fountain.
- What `surplusGrain`, `warehouseSurplusGrain` and `grainOutletState` read when food is low or
  the outlet jams, and how quickly they follow the device. The spike is running to catch this.
- What `batteryState: low` means on a Granary with no batteries fitted.
- How long a token lasts.
- Whether the copied headers are required, and whether PETLIBRO rate-limits.
