# What the display shows

This is the product spec. Each display state says what triggers it and what the screen must
get across. How it looks on the 52x16 clock is worked out in the studio, not here.

"Source" names are verified against pylitterbot 2025.8.0; see
[reference/pylitterbot.md](reference/pylitterbot.md). The ids in `code` are the names the
mapping, renderer and fixtures all use.

## How a state is chosen
The highest layer with something to say wins. There are no badges (D-0012): one state shows
at a time.

1. **Trust** — can I believe the screen?
2. **Needs you** — something only a person can fix.
3. **Live** — a visit in progress.
4. **Idle** — nothing happening.

## Layer 1: trust

| id | Trigger | Must convey |
|---|---|---|
| `stale` | Not a mapped state. The clock draws it when the pushed frame's lifetime runs out (feeder, Pi or LAN is down). | "What you see is old." Dark-red border over the last frame. |
| `no_data` | Feeder is alive but cannot get data: login failed, API errors, an empty robot list, or no successful contact for `no_data_after_s`. | "I can't reach Whisker." Not the same as the robot being offline. |
| `robot_offline` | `is_online` is false (status `OFFLINE`). | The robot lost Wi-Fi or power; how long ago it was last seen. |
| `robot_off` | `is_on` is false (status `OFF`). | The robot is switched off. |

## Layer 2: needs you

| id | Trigger | Must convey |
|---|---|---|
| `drawer_full` | Status `DRAWER_FULL` (the robot's own full flag). | Empty the drawer. Held until it clears. A red ring flashes on the outer edge once a second, with "FULL" and "EMPTY NOW". The poop emoji blinks and glances on its own rhythm. |
| `fault` | Status `PINCH_DETECT`, `OVER_TORQUE_FAULT`, `CAT_SENSOR_FAULT`, or `UNKNOWN`. | Something is wrong, with the short code (`PD`, `OTF`, `CSF`, or the raw code). |
| `bonnet_removed` | Status `BONNET_REMOVED`. | The bonnet is off. |

## Layer 3: live

| id | Trigger | Must convey |
|---|---|---|
| `in_use` | Status `CAT_DETECTED`. | Arthur is in there now. Two pages take turns: the poop emoji with a joke and "IN USE", then Arthur with how long he has been. |
| `waiting` | Status `CAT_SENSOR_TIMING`. | He has left; countdown to the clean cycle, from `clean_cycle_wait_time_minutes` (3, 7, 15, 25 or 30). If `is_sleeping`, the robot will not cycle yet, so show waiting without a countdown. |
| `cleaning` | Status `CLEAN_CYCLE`. | Cycling: soapy blue, a moving border, "CLEANING" and a countdown that is a timed estimate (`clean_cycle_estimate_s`). The library does not expose dump, level and home phases. |
| `interrupted` | Status `CAT_SENSOR_INTERRUPTED`. | He stepped back in mid-cycle and it stopped. Visibly different from `cleaning`. A little story that repeats, counted from when the interruption began: he strolls up to the globe, the view cuts to inside the box and he looks in, "INTERRUPTION", his face again, a second phrase, the globe alone (D-0016). |
| `paused` | Status `PAUSED`. | The cycle is paused. |
| `emptying` | Status `EMPTY_CYCLE`. | A person started an empty cycle. |
| `powering` | Status `POWER_UP` or `POWER_DOWN`. | Starting up or shutting down. |
| `just_cleaned` | Synthetic: status `READY` within `just_cleaned_s` (5 s) of leaving `CLEAN_CYCLE`. | A brief "done" before returning to idle: "FRESHLY CLEANED" in soapy blue (D-0015). |

A normal visit: `ready → in_use → waiting → cleaning → just_cleaned → ready`. An interruption
loops `cleaning → interrupted → waiting → cleaning`.

## Layer 4: idle

| id | Trigger | Must convey |
|---|---|---|
| `ready` | Status `READY` and nothing above applies. | Bin level (`waste_drawer_level`, 0–100) and litter level (`litter_level`, 0–100), plus one more thing: currently the time since the last visit. Visits today is a candidate. Both bars carry meaning by colour; see Level colours. |

## Level colours
The same rules apply wherever a level is drawn: the idle bars and digits, and the drawer bar on
the live screens (D-0011).

| Level | Rule |
|---|---|
| Litter | Follows the robot's own word, `litter_level_state`, which is what the phone app shows. `OPTIMAL` green; `REFILL` and `OVERFILL` yellow; `LOW` and `EMPTY` red; no word, sand. |
| Drawer | Orange below `drawer_warn_pct`; red from there, and on `drawer_full`. The robot reports only a percentage and a full flag, so "nearly full" is our own threshold. |

## Conditions with no screen of their own
Badges were removed (D-0012). What they used to mark:

| Condition | Now |
|---|---|
| Drawer nearly full, litter low | The bar colours above. |
| Laser sensors dirty (`is_laser_dirty`) | On idle, the bottom line says "LASER" in amber instead of the last-visit time. |
| On battery (`power_type == "DC"`) | Not shown. |
| Live connection down, polling only | Not shown. A real outage still becomes `no_data`, or the clock's stale border. |

## Not live data
- **Last visit and visits today** come from `get_activity_history()` or the pet's
  `weight_history`. They are fetched on the periodic refresh, not pushed.
- **Pet weight** (`pet_weight`, in pounds) updates after a visit.

## What the robot cannot tell us
- Whether Arthur is near the box but not in it.
- What he did in there.
- Anything faster than Whisker's cloud chooses to push it.
- Its own state while it is offline.
- "Drawer almost full" as a state: a Litter-Robot 4 never reports `DRAWER_FULL_1` or
  `DRAWER_FULL_2`, so the drawer bar turning red is our own threshold.

## Ideas for later
- **No visit in N hours.** A cat that stops going is a health warning worth a gentle alert.
- **Unusually many visits** in a short window.

## What the spike has to answer
- How long after Arthur steps in does `CAT_DETECTED` arrive?
- How long does each phase really last?
- Do the raw fields (`robotCycleStatus`, `robotCycleState`, `catDetect`, `displayCode`) reveal
  cycle phases or anything else worth showing?
- What does sleep mode do to the waiting state?
- Does `DRAWER_FULL` appear while cycles still run?
