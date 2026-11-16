# PETLIBRO food and water spike (2026-11-16)

_Outcome (2026-11-16): Kyle's Granary is paired with the **PETLIBRO** app, so it is the
reachable version 2. A logging spike was built to confirm the rest against the real devices:
`scripts/spike_petlibro.py`, with the API notes in `docs/reference/petlibro.md`. The rest of
this file is the research as it stood before that answer._

The question: can the display also show Arthur's food and water levels, from the PETLIBRO
Granary he already has (model PLAF103) and a PETLIBRO fountain Kyle wants to buy?

This was desk research. **Nothing was run against PETLIBRO's servers or Kyle's account.** Field
names come from reading the source of the community Home Assistant integration, not from a live
response. Items marked _unverified_ had no source that could be read.

Naming: in this project "the feeder" is our Python service. The PETLIBRO device is called "the
Granary" here to keep the two apart.

## Conclusions
1. **Water: yes, a real level.** Every app-connected Dockstream fountain weighs its tank and
   reports a percentage (`weightPercent`) and grams (`weight`).
2. **Food: only "low" or "not low".** The PLAF103 has one low-food sensor. The cloud reports it
   as a single true/false (`surplusGrain`). There is no hopper percentage. A level can be
   estimated by counting dispensed portions since the last refill.
3. **One condition decides everything for the Granary: which app it is paired with.** The
   PLAF103 was sold in two hardware versions under the same model number. Version 2 pairs with
   the **PETLIBRO** app and works with everything below. Version 1 pairs with **PETLIBRO Lite**
   (built on Tuya) and does not. "5G WiFi" on the listing does not tell them apart.
4. **The route is PETLIBRO's private cloud API**, the one the phone app uses. No official API,
   no local API. It is a handful of JSON POSTs; we would write our own small client in
   `arthur/sources/`, with `aiohttp`, which is already a dependency.
5. **It needs a second PETLIBRO account.** An account can be logged in from one place at a
   time, so our service logging in would log Kyle's phone out. The known fix is a second
   account, created in the app, with the devices shared to it.
6. **Fountain to buy:** a plug-in model. PLWF105 is the best understood; PLWF106 is the newer
   product and also supported. See the table.

## How the data is reached

| | |
|---|---|
| Official API | None found |
| What exists | A reverse-engineered client inside `jjjonesjr33/petlibro`, a Home Assistant custom integration. 394 stars, v1.4 released 2026-11-05, GPL-3.0 |
| Reusable as a library | No. Its `api.py` imports Home Assistant. No PETLIBRO package was found on PyPI (one search, not exhaustive) |
| Host | `https://api.us.petlibro.com`. The integration knows only this US host |
| Login | `POST /member/auth/login` with the email, the **MD5 of the password**, and fixed `appId` and `appSn` values copied from the app. Returns a `token`, sent as a `token` header afterwards |
| Session | One per account. Code `1009` means the token is no longer valid; log in again |
| Polling | The integration polls every 60 seconds. Levels move slowly; every 5 minutes would do |

Calls a minimal client needs (all `POST`, JSON body `{"id": <serial>, "deviceSn": <serial>}`
except the list):

| Call | Gives |
|---|---|
| `/device/device/list` | Devices on the account: `deviceSn`, `productName`, `productIdentifier` (for example `PLAF103`), `online` |
| `/device/device/realInfo` | Current state: `online`, `surplusGrain`, `grainOutletState`, `batteryState`, `weight`, `weightPercent`, `wifiRssi` |
| `/device/data/grainStatus` | Granary: `todayFeedingTimes`, `todayFeedingQuantity` |
| `/device/workRecord/list` | Granary: feed log with time and portions (`GRAIN_OUTPUT_SUCCESS`, `actualGrainNum`) |
| `/data/deviceDrinkWater/todayDrinkData` | Fountain: `todayTotalMl`, `todayTotalTimes`, yesterday's totals |

Login errors seen by integration users:
- `1102 ILLEGAL_PASSWORD`: the password is longer than 18 characters.
- `1101 MEMBER_NOT_EXIST`: a PETLIBRO Lite account, an account created on the website instead
  of in the app, and some non-US accounts.

## What each device reports

### Granary (PLAF103, version 2 only)

| Wanted | Available | Field |
|---|---|---|
| Food level | Low or not low only | `realInfo.surplusGrain` (false means low) |
| Fed today | Count and total portions | `grainStatus.todayFeedingTimes`, `todayFeedingQuantity` |
| Last meal | Time and portions | newest `GRAIN_OUTPUT_SUCCESS` work record |
| Next meal | Derived from the plan | `/device/feedingPlan/list` |
| Outlet blocked | Outlet state; exact meaning to confirm | `realInfo.grainOutletState` |
| Reachable | Yes | `realInfo.online` |
| Backup batteries | `low` or `normal` | `realInfo.batteryState` |
| Desiccant due | Days left | `remainingDesiccantDays` |

On battery backup the Granary turns its Wi-Fi off and keeps feeding to schedule; the log
uploads when power returns (PETLIBRO's FAQ). So a power cut shows up as offline, not as data.

**Estimating a level.** Count portions dispensed since the low flag last cleared (a refill),
against the hopper's capacity. It is an estimate: it assumes the hopper was filled to the top
and that a portion is a fixed volume. The portion size is _unverified_.

### Fountains

All four app-connected models are read the same way by the integration.

| Wanted | Available | Field |
|---|---|---|
| Water level | Percent and grams | `realInfo.weightPercent`, `realInfo.weight` |
| Drunk today | Millilitres and visits | `todayDrinkData.todayTotalMl`, `todayTotalTimes` |
| Filter due, cleaning due | Days left | `remainingReplacementDays`, `remainingCleaningDays` |
| Reachable | Yes | `realInfo.online` |

One report (integration issue #307, on a PLWF305) adds `realInfo.weightState` with the values
`NORMAL`, `LOW_WATER` (about 30%) and `LACK_WATER` (about 12%, pump stops), and
`waterBoxStatus` for tank removed. It also says `weight` reads 0 both when the tank is empty
and when it is lifted off, so "0%" cannot tell those apart.

## Which fountain

From PETLIBRO's own comparison table. All are 5 V, about 9 to 10 inches wide.

| Model | Name | App | Wi-Fi | Tank | Power | Integration support | Note |
|---|---|---|---|---|---|---|---|
| PLWF105 | Dockstream Smart Fountain | Yes | 2.4 GHz | 2.5 L | Plug-in | Yes, the first fountain added | No low-water light on the device. Has a local-firmware option (below) |
| PLWF106 | Dockstream 2 Smart Fountain (plug-in) | Yes | 2.4 and 5 GHz | 3 L | Plug-in | Yes | Low-water light; also reports a low-water threshold |
| PLWF116 | Dockstream 2 Smart Cordless | Yes | 2.4 and 5 GHz | 3 L | Battery | Yes | Whether it keeps reporting on battery is _unverified_ |
| PLWF305 | Dockstream RFID Smart Fountain | Yes | 2.4 GHz | 3 L | Plug-in | Yes | Per-pet tracking with a collar tag. Pointless for one cat |
| PLWF115, PLWF005 | Cordless, Wireless Pump | **No app** | — | 2.5 L | — | — | Cannot be monitored |

Prices were not confirmed on the day: PETLIBRO's product pages refused automated reads. Search
results put PLWF105 and PLWF106 both at about $80 to $90.

**Recommendation.** PLWF105 if the only goal is monitoring: it has been supported longest, its
hardware is documented (an ESP32-C3 and a load cell), and it is the only one with a way off the
cloud. PLWF106 if Kyle wants the bigger tank or 5 GHz; it is read the same way. Skip the
cordless one until someone confirms it reports while on battery.

## Risks
- **Unofficial.** PETLIBRO can change the API whenever it likes. The integration's developer
  mentions one such breakage around December 2024. Its maintainers are active (a release 11
  days ago), so fixes can be copied from there.
- **Terms.** PETLIBRO's US terms of service prohibit using the service to "spider, crawl, or
  scrape" and circumventing security features, and allow termination. No clause on reverse
  engineering or third-party clients was found there. The app's own User Service Agreement was
  not read. No report of a blocked or banned account was found.
- **Second account.** One forum user shared a PLAF103 to a second account and still could not
  log in; unresolved. What a shared account cannot see is _unverified_.
- **Stale data.** The integration swallows API errors and keeps showing old values (its open
  issue #295). Our client must do the opposite, as the Whisker source will: any failure shows
  as no data.
- **Password handling.** The API takes an unsalted MD5 of the password. Another reason to use a
  throwaway second account with its own password.

## No-cloud routes
- **PLWF105:** `taylorfinnell/petlibro-esphome` replaces the fountain's firmware with ESPHome
  and exposes the scale locally. It needs the base opened and a USB serial adapter, probably no
  soldering. The PETLIBRO app stops working unless the factory firmware was backed up first.
  185 stars, last pushed 2026-10-13.
- **PLAF103 version 2:** nothing found. The same project covers the PLAF108 (Air), and
  `icex2/plaf203` redirects a PLAF203S to a local MQTT broker; neither is this model.
- **PLAF103 version 1 (Tuya):** forum users report it working with Tuya's local protocol. Not
  researched further.

## How it would fit this project
- A second source, `arthur/sources/petlibro.py`, the only code that knows PETLIBRO's API, on
  the same rules as the Whisker source: one login, poll every few minutes, fail visibly.
- Mapping gains two facts: food low (true/false) and water percent. How they appear on a 52x16
  screen is a design question for the `design-screen` skill, not settled here.
- Credentials: `PETLIBRO_EMAIL` and `PETLIBRO_PASSWORD` in `.env`, for the second account.

## To find out next
1. Which app the Granary is paired with. Kyle can answer by looking at his phone.
2. A logging spike like `scripts/spike_log.py`: log in with the second account and record the
   raw responses every few minutes for a few days. It would settle the items below.

## Could not verify
- Which PLAF103 version Kyle has.
- The real response shapes for his devices. Everything here is from source and issue reports.
- How quickly the cloud values follow the device: how soon `weightPercent` moves after a drink
  or a refill, and how soon `surplusGrain` flips.
- How much food is left when the low-food sensor trips. PETLIBRO's support page describes an
  infrared sensor at the base of the tank; no amount is given.
- Whether sharing to a second account works for both devices.
- Whether PETLIBRO rate-limits. None reported at a 60-second poll.
- Current prices.

## Main sources
- Integration: <https://github.com/jjjonesjr33/petlibro> (`custom_components/petlibro/api.py`,
  `devices/feeders/granary_smart_feeder.py`, `devices/fountains/*.py`,
  `docs/api-documentation.md`; issues #32, #77, #85, #120, #295, #307)
- Integration wiki, accounts: <https://github.com/jjjonesjr33/petlibro/wiki/PetLibro-Account-Management>
- Integration wiki, passwords: <https://github.com/jjjonesjr33/petlibro/wiki/PetLibro-Password-Limitation>
- Fountain comparison: <https://petlibro.com/pages/what-s-the-difference-between-wf115-wf105-wf106-wf116-and-wf305>
- Granary FAQ (Lite versus PETLIBRO app, battery behaviour): <https://au.petlibro.com/pages/pre-sale-inquiries-about-the-granary-smart-feeder-af103-plaf103>
- App Store listing (two apps, QR code note): <https://apps.apple.com/us/app/petlibro/id1586120661>
- Terms of service: <https://petlibro.com/pages/terms-of-service>
- Low-food notifications: <https://ca.petlibro.com/pages/why-am-i-not-getting-any-low-food-notifications-on-the-granary-smart-feeder-af103-plaf103>
- Home Assistant forum: <https://community.home-assistant.io/t/petlibro-cloud-integration-non-tuya-wip/759978>,
  <https://community.home-assistant.io/t/petlibro-cat-feeder/498637>
- ESPHome firmware: <https://github.com/taylorfinnell/petlibro-esphome>
- PLAF203S local MQTT: <https://github.com/icex2/plaf203>
