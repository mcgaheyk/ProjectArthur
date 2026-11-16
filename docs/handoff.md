# Original handoff (2026-11-15)

Kept verbatim as the record of what was first intended. **It has been superseded in places:**
the firmware is AWTRIX NG, not AWTRIX 3 (D-0002); the feeder draws the pixels instead of
sending AWTRIX text and icons (D-0003); the display may be a TC002 (D-0001); several pylitterbot
details were corrected in [reference/pylitterbot.md](reference/pylitterbot.md). The AWTRIX 3
API section below does not apply to NG.

---

# Litter-Robot status display

## Goal
Build a small always-on status display for my Litter-Robot 4. The robot sits
in a closet; the display sits outside it in my office. It should show:
- Waste drawer fill level (always visible, with a bar gauge)
- Near-real-time "in use" when my cat Arthur steps in, then the
  waiting / cleaning states that follow a visit
- Takeover alerts for drawer full and faults
- A clear signal when the robot is offline or the data is stale

## Hardware (decided, not yet delivered)
- Display: Ulanzi TC001 pixel clock (32x8 RGB LED matrix, ESP32),
  flashed with AWTRIX 3 firmware.
- Host: Raspberry Pi 5 1GB running Raspberry Pi OS Lite (64-bit),
  headless over SSH. Only 1GB of RAM, so keep dependencies light.
- Both on the same home Wi-Fi (2.4 GHz).

## Architecture
A single Python service ("feeder") on the Pi:
1. Logs in to Whisker's cloud with pylitterbot (unofficial,
   reverse-engineered API; requires Python >= 3.11).
2. Holds the Litter-Robot 4 WebSocket subscription for push updates,
   AND polls every few minutes as a safety net if the socket drops.
3. Maps robot state to a display state (pure function, unit tested).
4. Pushes to the clock over local HTTP.

Keep the transport behind an interface. A fallback design (hosted worker,
clock pulls over HTTPS using AWTRIX NG on-device scripts) may be needed
later, so state mapping must not know about AWTRIX.

## What I know about pylitterbot (verify against the installed source)
I have only read summaries, not the code. Before using any name below,
open the installed package and confirm it.
- LitterRobot4 properties: waste_drawer_level, litter_level, is_online,
  status, pet_weight, is_waste_drawer_full
- Status values include: READY, CAT_DETECTED, CAT_SENSOR_TIMING (waiting
  to cycle after a visit), CAT_SENSOR_INTERRUPTED, CLEAN_CYCLE,
  DRAWER_FULL, OFFLINE, OFF, plus several fault states
- Real-time updates use a graphql-ws WebSocket. I saw internal method
  names only; find the public subscribe API yourself.

## AWTRIX 3 HTTP API (confirmed from the docs)
Base: http://<clock-ip>
- POST /api/custom?name=<app>   create/update a persistent app
  keys: text, icon, color, progress (0-100), progressC, progressBC,
        lifetime (seconds), lifetimeMode (1 = mark stale with red frame)
  text may be an array of {"t": "...", "c": "RRGGBB"} fragments
  an empty body deletes the app
- POST /api/notify              takeover notification
  keys: text, color, icon, hold (stay until dismissed), wakeup, stack
- POST /api/notify/dismiss
- POST /api/indicator1|2|3      corner dots on the right edge
  (1 upper right, 2 right side, 3 lower right)
  body: {"color": [r,g,b] or "#hex", "blink": ms, "fade": ms}; "0" clears
Docs: https://blueforcer.github.io/awtrix3/#/api

## Display constraints
- 32x8 pixels total. An 8x8 icon on the left leaves room for roughly
  six characters without scrolling.
- The progress bar takes the bottom pixel row.
- Colours and dim shades will look different on real LEDs; keep the
  palette in one config file so it is easy to tune later.

## States to design and map
READY (show fill %), IN USE, WAITING TO CLEAN, CLEANING, DRAWER FULL,
FAULT (generic, with the status code), ROBOT OFFLINE, DATA STALE
(feeder or internet down; use lifetime/lifetimeMode so this works even
if the feeder is dead).

## Work to do before the hardware arrives
1. Feeder spike: connect to my real robot from my laptop and log every
   state change with timestamps to a file. I want to see the actual
   sequence and timing of a visit before finalising the screens.
2. Simulator: a browser page that renders a 32x8 LED matrix and exposes
   a local stub server implementing the AWTRIX endpoints above, so the
   feeder can drive it unchanged. Switching to the real clock should be
   a one-line config change (CLOCK_URL).
3. State mapping with tests, driven by recorded logs from step 1.
4. Pi deployment: systemd unit, reconnect with backoff, log rotation.

## Rules
- Credentials come from environment variables or a gitignored .env.
  Never commit them, never print them in logs.
- The Whisker API is unofficial and can break. Fail visibly: on auth or
  API errors, set the offline/stale display state and keep retrying.
- Be polite to Whisker: one shared connection, poll no faster than
  every few minutes.
- Do not assume library or API details beyond what is written here.
  Read the source or docs, and tell me when something above is wrong.

## Known unknowns
- How quickly Whisker emits the cat-detected event (unmeasured).
- How the colours read on the physical matrix.

## Start here
Propose a repo structure and a short plan for steps 1 and 2, then wait
for my go-ahead before writing code.
