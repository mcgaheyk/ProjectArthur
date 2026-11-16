# PETLIBRO cloud API reference (read from source, not yet confirmed live)

Read on 2026-11-16 from the `dev` branch of `jjjonesjr33/petlibro` (release v1.4, 2026-11-05):
`custom_components/petlibro/api.py`, `devices/feeders/granary_smart_feeder.py`,
`devices/fountains/*.py` and `docs/api-documentation.md`. That project reverse-engineered the
API the PETLIBRO phone app uses. PETLIBRO publishes no API documentation.

**Nothing here has been confirmed against a live response.** `scripts/spike_petlibro.py` exists
to do that. When it has run, correct this file from its log and mark what was confirmed.

Naming: in this project "the feeder" is our Python service. The PETLIBRO PLAF103 is "the
Granary".

## Rules for our code
- Only `arthur/sources/` (and the spike) may call this API.
- **Read only.** The API also has calls that dispense food and change settings. We never call
  them. The spike's client refuses any path that is not on its read list.
- One login per run. An account can be logged in from one place at a time, so a login here logs
  that account out of the phone app. Use a second account with the devices shared to it.
- Poll no faster than once a minute; every five minutes is the default.
- Fail visibly. The integration this was read from keeps showing old values when a call fails
  (its issue #295). Ours must not.
- Never log the token, the password or its hash, the email, a device serial or a MAC address.

## Transport
- Host: `https://api.us.petlibro.com`. The integration knows no other region.
- Every call is a `POST` with a JSON body and `Content-Type: application/json`.
- Headers the integration sends on every call, copied from the Android app:
  `source: ANDROID`, `language: EN`, `timezone: <IANA zone>`, `version: 1.3.45`.
  Whether the server requires them is not known.
- After login, every call carries `token: <token>` as a header.
- Every response is `{"code": <int>, "msg": <str or null>, "data": <payload>}` with HTTP 200.
  `code` 0 is success. The integration retries HTTP 5xx twice, two seconds apart.

| Code | Name | Meaning |
|---|---|---|
| 0 | | Success |
| 1009 | `NOT_YET_LOGIN` | The token is not valid (expired, or the account logged in elsewhere). Log in again |
| 1101 | `MEMBER_NOT_EXIST` | No such account on this API: a PETLIBRO Lite account, one made on the website, or a non-US one |
| 1102 | `ILLEGAL_PASSWORD` | Password rejected. Also returned when the password is longer than 18 characters |

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

`POST /member/auth/logout` ends the session. We do not call it.

## Reading devices
`POST /device/device/list` with body `{}`. `data` is a list. Per device: `deviceSn`,
`productName`, `productIdentifier` (for example `PLAF103`, `PLWF105`), `name`, `mac`,
`softwareVersion`, `hardwareVersion`, `online`, `deviceShareState`, `shareId`.

The calls below take `{"id": "<deviceSn>", "deviceSn": "<deviceSn>"}`.

| Call | For | Fields we care about |
|---|---|---|
| `/device/device/baseInfo` | All | Name, versions, `online`; the Granary's `remainingDesiccantDays` is read from the merged device data and probably comes from here |
| `/device/device/realInfo` | All | `online`, `wifiRssi`, `batteryState`; Granary: `surplusGrain`, `grainOutletState`, `runningState`; fountain: `weight`, `weightPercent`, `remainingReplacementDays`, `remainingCleaningDays` |
| `/data/data/realInfo` | Fountains (Dockstream 2 at least) | `lowWater`, `waterStopSwitch`, `exceptionMessage`, `filterState` |
| `/device/data/grainStatus` | Granary | `todayFeedingTimes`, `todayFeedingQuantity`, `todayFeedingQuantities`, `surplusGrain` |
| `/data/deviceDrinkWater/todayDrinkData` | Fountains | `todayTotalMl`, `todayTotalTimes`, `avgDrinkDuration`, `yesterdayTotalMl`, `yesterdayTotalTimes` |

`POST /device/workRecord/list` (the Granary's feed log) takes a different body:

```json
{
  "deviceSn": "<deviceSn>",
  "startTime": <epoch ms>,
  "endTime": <epoch ms>,
  "size": 25,
  "type": ["GRAIN_OUTPUT_SUCCESS"]
}
```

`data` is a list of days, each with `workRecords`; a record has `type`, `recordTime` (epoch ms)
and `actualGrainNum` (portions).

## What the values mean
- `surplusGrain`: true means there is food; **false means low**. The integration's "food low"
  sensor is `not surplusGrain`.
- `weightPercent`: water level, 0 to 100. `weight`: water in grams.
- One user report (integration issue #307, PLWF305): `realInfo.weightState` is `NORMAL`,
  `LOW_WATER` (about 30%) or `LACK_WATER` (about 12%, pump stops); `waterBoxStatus` 0 means the
  tank is empty or lifted off; `weight` reads 0 in both cases.
- `grainOutletState`: the integration's "food dispenser state" is `not grainOutletState`. What
  it means in practice is not confirmed.
- `batteryState`: `low`, `normal` and possibly others.

## Not confirmed
- Every response shape above, for Kyle's devices.
- Whether a shared (non-owner) account sees the same fields.
- How quickly these values follow the device.
- Whether the copied headers are required, and whether PETLIBRO rate-limits.
