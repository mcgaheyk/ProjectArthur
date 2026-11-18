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
| `robot_offline` | `is_online` is false (status `OFFLINE`). | The robot lost Wi-Fi or power; how long ago we saw it go offline. The robot's own `last_seen` is not used: it sat frozen on one date for the whole spike. No age if it was already offline when the feeder started. |
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
| `in_use` | Status `CAT_DETECTED` or `CAT_SENSOR_TIMING`, and no weight reading yet this visit. | Arthur is in there now. Two pages take turns: the poop emoji with a joke and "IN USE", then Arthur with how long he has been. |
| `waiting` | Status `CAT_DETECTED` or `CAT_SENSOR_TIMING`, and the weight reading has arrived. | He has left; countdown to the clean cycle, `clean_cycle_wait_time_minutes` after the weight arrived (the robot reports 8). Waiting without a countdown if `is_sleeping` (the robot will not cycle yet), or if no weight has arrived `cat_left_fallback_s` after detection. |
| `cleaning` | Status `CLEAN_CYCLE`. | Cycling: soapy blue, a moving border, "CLEANING" and a countdown that is a timed estimate (`clean_cycle_estimate_s`, 130 s; real cycles took 123 to 134 s). The raw phases are not used; see below. |
| `interrupted` | Status `CAT_SENSOR_INTERRUPTED`. | He stepped back in mid-cycle and it stopped. Visibly different from `cleaning`. A little story that repeats, counted from when the interruption began: he strolls up to the globe, the view cuts to inside the box and he looks in, "INTERRUPTION", his face again, a second phrase, the globe alone (D-0016). |
| `paused` | Status `PAUSED`. | The cycle is paused. |
| `emptying` | Status `EMPTY_CYCLE`. | A person started an empty cycle. |
| `powering` | Status `POWER_UP` or `POWER_DOWN`. | Starting up or shutting down. |
| `just_cleaned` | Synthetic: status `READY` within `just_cleaned_s` (5 s) of leaving `CLEAN_CYCLE`. | A brief "done" before returning to idle: "FRESHLY CLEANED" in soapy blue (D-0015). |

A normal visit: `ready → in_use → waiting → cleaning → just_cleaned → ready`. An interruption
goes `cleaning → interrupted → cleaning`: in the two interruptions in the robot's history the
cycle resumed and finished without a new wait. No interruption has been logged live yet.

### How a visit is read (D-0017)
Measured on seven real visits, 2026-11-15 to 17. The two statuses do not say where the cat is:
`CAT_SENSOR_TIMING` arrived 6 to 10 seconds after detection in four visits and never in the
other three, which stayed `CAT_DETECTED` until the clean cycle. What does track him:

- **He has left when the weight reading arrives.** The push that changes `pet_weight` came 48
  to 86 seconds after detection, and the clean cycle started 476 to 482 seconds after it every
  time, against a wait setting of 8 minutes. The robot counts its wait from that moment.
- A second weight reading in the same visit restarts the countdown (he went back in).
- **A detection only counts as a visit** if a weight arrived or it lasted `visit_min_s` (30 s).
  Detections of 2 to 14 seconds with no weight were a person at the box or a manual clean.
- The feeder observes these: `visit_started_at` and `cat_left_at` are dated from when it saw
  the change, like `status_since` (D-0015).

## Layer 4: idle

| id | Trigger | Must convey |
|---|---|---|
| `ready` | Status `READY` and nothing above applies. | Bin level (`waste_drawer_level`, 0–100) and litter level (`litter_level`, 0–100), plus one more thing: currently the time since the last visit. Visits today is a candidate. Both bars carry meaning by colour; see Level colours. |

### The idle show (D-0019)
Idle is still one state, `ready`. While it lasts, the screen takes turns in a round lined up
with the wall clock (three minutes by default):

| Part | Share | What it is |
|---|---|---|
| Home page | the rest (50 %) | The screen described above. It opens every round and comes back between the other two. |
| Page | `page_pct` (30 %) | Boston's weather, the temperature and the date. With no fresh weather reading it shows the date alone, never an old temperature. |
| Scene | `scene_pct` (20 %) | A short story with Arthur: a rat chase, or a leap at a bird. It carries no information. |

- **The round is decided in mapping** (`arthur/schedule.py`) and carried on the display state
  as a cue: which part, when its slot began and how long it is. The renderer plays the cue.
- **Nothing starts until idle has lasted `settle_s`**, and a slot that began before then is
  skipped whole, so a page or scene never starts part-way through.
- **Any state above idle cuts in at once.** The cue belongs to `ready` only; a visit, a fault
  or drawer-full replaces the screen in the middle of a scene, with no transition.
- **At night** (`quiet_from_h` to `quiet_until_h`, local time) there are no scenes and Arthur
  sleeps on the home page. The page carries on.
- With both shares at 0 the show is off and idle is the home page alone.
- Weather is not robot data. It comes from Open-Meteo through its own source and reaches the
  screen only on `ready`; a reading older than `max_age_s` is dropped in mapping.

## Level colours
The same rules apply wherever a level is drawn: the idle bars and digits, and the drawer bar on
the live screens (D-0011).

| Level | Rule |
|---|---|
| Litter | Follows the robot's own word, `litter_level_state`, which is what the phone app shows. `OPTIMAL` green; `REFILL` and `OVERFILL` yellow; `LOW` and `EMPTY` red; no word, sand. |
| Litter bar length | Scaled to the usable range: full at 100 %, down to a 2-pixel stub at `litter_empty_pct` (60) and below, so it always shows a little. The robot's percentage is litter depth and it calls 72 % `LOW`; on a plain 0–100 scale a low reading looked mostly full. The 60 is a guess: no `EMPTY` reading has been seen. |
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

## What the spike answered, and what is still open
- **Answered:** how a visit unfolds and how long each part lasts (above). Raw
  `robotCycleStatus` does show the clean cycle's phases, always `CYCLE_DUMP`, `CYCLE_DFI`,
  `CYCLE_LEVEL`, `CYCLE_HOME`, but each varies as much as the whole (home alone 3 to 18 s), so
  they would not end the countdown more accurately. `catDetect` and `robotCycleState` carry
  nothing usable.
- **Open:** what a live interruption looks like and how long it lasts; what sleep mode does to
  the waiting state; whether `DRAWER_FULL` appears while cycles still run; how long after
  Arthur steps in `CAT_DETECTED` arrives (the push trails the robot's own clock by up to 5 s).
