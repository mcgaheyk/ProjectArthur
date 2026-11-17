# AWTRIX NG reference

Target version: **1.2.3** (2026-11-03). Collected 2026-11-15 from the official docs
(<https://blueforcer.github.io/awtrix-ng/esp32/>) and, where marked **[src]**, from the source
(<https://github.com/Blueforcer/awtrix-ng>). The API spec is `docs/api/openapi.yaml` in that
repo. **Confirm each item against the spec before code depends on it**, mark it confirmed
here, and add what you learn.

**Checked 2026-11-15** against `docs/api/openapi.yaml` at tag `v1.2.3` (byte-identical to `main`
on that date) and, for the flat pushed-app keys, `docs/reference/payload.md` at `v1.2.3`. The
OpenAPI file declares only the structured pushed-app fields and says the flat ones (`draw`,
`lifetimeMs`, `lifetimeExpiry`, …) "are tabulated in docs/reference/payload.md", so those
items are marked **confirmed against payload.md**.

AWTRIX 3's API (`/api/custom`, `/api/notify`) does not exist in NG. There is no compatibility
layer.

## General rules
- JSON only. **Corrected against openapi.yaml:** `415 unsupportedMediaType` is returned only when
  a *non-JSON* `Content-Type` is supplied on `PUT`/`PATCH`; omitting the header bypasses the
  check and `POST` is not gated. Send `Content-Type: application/json` anyway.
- An unknown top-level key → **422** `validationFailed` naming the `field`. Validation is
  all-or-nothing: on error nothing is saved. (Confirmed against payload.md, "Unknown top-level
  key".)
- Body limit on the TC002: **2 MiB** for any request body (413 `payloadTooLarge`; confirmed
  against `docs/reference/limits.md`). ESP32 builds take 8192 bytes. JSON nesting limit 16 (400).
- Keys are camelCase; durations are milliseconds with an `Ms` suffix.
- Colours: `"#RRGGBB"`, `"RGB"`, `[r,g,b]`, or an integer. Read back as `#RRGGBB`.
- HTTP Basic auth is off by default. CORS is open.

## Pushed apps (what the transport uses)
- `PUT /api/v1/apps/pushed/{name}` creates or replaces. Name: `[A-Za-z0-9_-]{1,32}`; a bad
  name is `400 invalidName`. **Confirmed against openapi.yaml.**
- `DELETE /api/v1/apps/{name}` removes (any app kind; idempotent, unknown name is 200). An empty
  body or `{}` on PUT is 422, not a delete. **Confirmed against openapi.yaml.**
- Pushed apps live **in RAM**: gone after a clock reboot. **Confirmed** ("A pushed app is not
  saved on the device"). At most 50 pushed apps (507 beyond that).
- With one app in the rotation, the clock stays on it permanently.

Keys we send:

| Key | Meaning |
|---|---|
| `draw` | Array of drawing commands, as arrays. We send one: `["bitmap", x, y, w, h, data]` where `data` is **base64 RGB888** (`w*h*3` bytes: red, green, blue per pixel, row by row) or an array of colours. **Confirmed against payload.md** (Draw commands). Plain base64, no `data:` prefix. Too few colours leave the rest empty; pixels outside the display are clipped. A wrong argument count is `422`, `field` = `draw[<i>]` |
| `lifetimeMs` | 0 (or less) = forever. Counts from when the clock received the push. Pushed apps only (notifications ignore it). **Confirmed against payload.md** |
| `lifetimeExpiry` | `"remove"` (default) or `"mark"`: the app stays in the rotation with a 1-pixel dark-red `#6E0700` frame. **Confirmed against payload.md** |
| `durationMs` | How long the app shows per turn; irrelevant with a single app |

- Frame size: 52x16 → 2496 bytes → 3328 base64 characters.
- Other `draw` commands exist (`pixel`, `pixels`, `line`, `rect`, `rectFill`, `circle`,
  `circleFill`, `text`) but we do not use them (D-0003).
- Keys that exist but we do not send: `text`, `textColor`, `icon`, `progress`,
  `progressColor`, `backgroundColor`, `effect`, `overlay`, `barChart`, `lineChart`, `scroll`,
  `font`, and others. The simulator should refuse them loudly rather than pretend.

### Double size on the TC002: `enlargeApps` must be off
**Found 2026-11-16; our first notes missed it.** On a TC002, a pushed app or notification is
drawn **at double size on a 26x8 grid** by default: each of its pixels lights a 2x2 square, its
bottom-right corner is `(25, 7)`, and "a larger position lies outside the grid and shows
nothing". So a 52x16 bitmap pushed to a freshly installed clock shows only its top-left
quarter, doubled.

The setting `enlargeApps` (boolean, default `true`) controls it. With it off, a pushed app
"uses all 52 × 16 pixels". Scripts, layouts and built-in apps are never enlarged. **Confirmed
against** `docs/guides/display.md`, `docs/guides/graphics.md` and `docs/reference/settings.md`
at `v1.2.3`. Not stated: whether the stale border is also doubled while the setting is on.

The transport therefore sets `enlargeApps: false` before it pushes; see Settings below.

### Render order **[docs]**
Background → text and drawings → **stale border** → icon → extra icons → overlay. So the border
overwrites the outermost row and column of our bitmap. **Confirmed against payload.md**
("Render order").

### When expiry is checked **[src]**
`CoreEngine::tick()` runs once per main loop before rendering and checks every pushed app's
lifetime. `SpecRenderer` then draws `drawRect(0,0,w,h,0x6e0700)`. The mark appears within one
frame of expiry, even on the app that is showing.

Not confirmed: that re-pushing clears the mark (inferred from the code's structure).

## Indicators
- `PUT /api/v1/indicators/{1|2|3}` with `{color, blinkMs, fadeMs}`; `DELETE` to clear. Small
  lights on the right edge: 1 top, 2 middle, 3 bottom. **Confirmed against openapi.yaml**, with
  details: `blinkMs`/`fadeMs` are integers 0–65535 and default to 0 (steady); only `color`
  changes on/off (a `color` of 0 or `null` turns it off and keeps the stored colour; a body
  without `color` leaves on/off untouched); `{}` is 422; an id outside 1..3 is 404. "Drawn above
  apps" is not stated in the spec.
- Exact pixels on the 52x16 matrix are not documented. NG's Home Assistant guide describes 1 and
  3 as corners and 2 as "a short bar in the middle" of the right edge. (AWTRIX 3 on a 32x8 clock
  used a 3-pixel corner, a 2-pixel bar and a 3-pixel corner.)

## Reading the screen back
`GET /api/v1/display/screen` → `{width, height, pixels[]}`, row-major from the top left,
decimal `0xRRGGBB`, **before** brightness, gamma and colour correction. This is how the
simulator will be compared with the real clock. **Partly confirmed against openapi.yaml:** the
shape `{width: int, height: int, pixels: [int]}` with each item "packed 0xRRGGBB" is confirmed.
Row-major order and "before brightness/gamma" are **not stated** in the spec.

## Settings (the transport uses one key)
**Confirmed against** `docs/reference/settings.md` at `v1.2.3`.
- `GET /api/v1/settings` returns every settings key. `PATCH /api/v1/settings` takes any subset;
  keys left out stay as they are. A successful `PATCH` answers `200` with all settings, already
  updated, and takes effect at once.
- Validation is all-or-nothing. A bad value is `422 validationFailed` naming the `field`; an
  unknown key is `422` with `"message": "unknown field"`.
- **Settings survive a restart.** `POST /api/v1/settings/reset` deletes them and restarts the
  clock, which would turn `enlargeApps` back on.
- Keys we use or will use: `enlargeApps` (see above); `brightness` 0–255.
- **Brightness on the TC002:** it has no light sensor, so `autoBrightness` "is accepted and
  saved" but changes nothing (`docs/guides/brightness.md`). There is **no built-in night
  schedule** (requested in issue #98 and declined; the maintainer points to scripts). Night
  dimming is therefore ours to do: the feeder sets `brightness` on a timer.

## For later features
- **Notifications:** `POST /api/v1/notifications` (`hold`, `stack`, `wakeup`, `sound`, `name`);
  `DELETE /api/v1/notifications/active` dismisses the one showing.
- **Rotation and built-in apps:** `PUT /api/v1/apps/order {"order":[…],"disabled":[…]}`
  persists; `PUT /api/v1/apps/active {"name","fast":true}` shows an app now.
- **Buttons and knob** (confirmed against `docs/guides/device-controls.md`,
  `docs/reference/system.md#buttons` and `docs/reference/settings.md` at `v1.2.3`):
  - The TC002 has three buttons on top (left, select, right) and a knob. Out of the box: left and
    right show the previous or next app in the rotation; select once dismisses the notification
    shown; select twice quickly switches the display off or on; select held 0.5 s opens the
    on-clock menu (scripts, radio). The knob turns brightness or volume, 5 % a click (a short
    press switches between them); held 0.5 s it starts the voice assistant, if one is set up.
  - `buttonCallback` is a key in `PUT /api/v1/system` (not settings). AWTRIX `POST`s JSON to it
    on every press **and** release: `{"button":"left|middle|right","state":true|false,"uid":…}`.
    The knob sends `"button":"knob"` with `state` when pushed and `"turn":n` (clicks, positive
    clockwise, summed while the listener is slow) when turned. Plain `http://` only, no login;
    the answer is ignored and never retried. On the TC002 the call runs in the background, so a
    slow listener never stalls the display; 16 calls queue, then presses are dropped. Long
    press is ours to time from `true` to `false`. Debounce and double-press times are fixed.
  - `blockNavigation` (setting, "Block buttons" in the web UI) stops the buttons switching apps
    or the display and the menu opening, and stops the knob setting brightness or volume or
    starting voice. Presses and turns still reach `buttonCallback`, MQTT and scripts.
  - A shown Berry script gets each press first and can keep it by returning `true` from
    `on_button()`.
- **Scripts (Berry):** run on the clock, survive reboot, can draw at about 40 fps, make
  HTTP(S) requests and read `now_ms()`. The official `Doorstate` recipe turns the screen grey
  when its data is old; that is the pattern for our watchdog.
- **Art-Net:** the clock can display frames streamed over UDP 6454 and returns to its app
  rotation about 5 s after the last frame. An alternative transport.
- **`awtrix-linux`:** the same core as a host program, any size from 8–128 x 8–32, with the
  HTTP API. Could serve as a conformance check. Needs CMake and PlatformIO.

## Fonts
The built-in `small` font has 5-pixel capitals on rows 1–5, 4 pixels per character, 2 for a
space. `large` has 7-pixel capitals.
(Checked: `small` is built from `assets/fonts/awtrix.bdf` filled from `MatrixChunky6.bdf`;
`large` from `MatrixChunky8.bdf`. ProjectArthur vendors `awtrix.bdf` and
`matrix-fonts/8-series/MatrixChunky8.bdf` in `arthur/render/fonts/` with their notices.) Sources in the NG repo: `assets/fonts/awtrix.bdf`,
`assets/fonts/matrix-fonts/`. The docs give the licence as **SIL OFL 1.1**. **Confirmed from the repo at v1.2.3**:
`LICENSE.md` "Font data exception" licenses `assets/fonts/awtrix.bdf` and the supplements under
OFL-1.1; `THIRD-PARTY-NOTICES.md` lists the Matrix-Fonts as OFL-1.1 with notices in
`LICENSES/MIT-Matrix-Fonts.txt` (the file name says MIT; it holds Trip5's MIT notice plus the
full OFL-1.1 text, and the combined fonts are OFL-1.1). Keep the notice with any vendored font.

## Hardware
Our clock is a **Ulanzi TC002** (D-0010). Checked 2026-11-16 against the NG repo at `v1.2.3` and
Ulanzi's product page. Earlier notes here called NG on the TC002 "a beta from late September
2026"; nothing in today's sources supports that.

- **Firmware status.** 1.2.3 (2026-11-03) is the latest release and ships TC002 files
  (`usb-awtrix-ng-tc002.zip`, `awtrix-ng-tc002.awup`). The release index gives 1.2.2
  (2026-10-06) as the TC002's first release. No beta or experimental label was found; only the
  "Hey AWTRIX" wake word is marked experimental.
- **Not an ESP32.** NG runs as a Linux program (`awtrix-linux --board tc002`) on the
  manufacturer's SigmaStar kernel, ARMv7 (`docs/developers/tc002/`). The exact chip is not
  named. HTTP is always on port 80.
- **Display and controls:** 52x16 (832 LEDs), three buttons and a knob, speaker and microphone.
  **No light sensor.** Timed sleep is not supported.
- **Power and size (Ulanzi):** USB-C 5 V 3 A or the magnetic dock; 3600 mAh battery;
  20.5 x 3.3 x 8.5 cm; 380 g.
- **Network:** 2.4 GHz Wi-Fi only.
- **Scripts (Berry)** are available on it.

### Installing NG on the TC002 (`docs/getting-started/tc002.md`)
- A desktop **TC002 USB installer** (graphical or terminal) for Windows, macOS 13.5+ and Linux.
  There is no browser flasher. Installers downloaded before 1.2.3 refuse the 1.2.3 firmware.
- Needs a USB-C **data** cable and only one clock connected. Open the installer, wait for
  "Ready when you are.", then switch the clock off and on once: it accepts the connection only
  briefly after power-on.
- Installing removes the Ulanzi app's saved Wi-Fi and cloud login. Do not unplug, close the
  installer or press reset while it writes.
- **Getting back** (`docs/guides/reset-recovery.md`): holding the reset button for 5 s erases NG
  and reinstalls the Ulanzi firmware kept on the clock, which may be older than the one it
  shipped with. A damaged install shows "USB RECOVERY" and can be reinstalled.

### What the real install showed (2026-11-17, Kyle's TC002, from a Mac)
- Installer: `installer/tc002/AWTRIX-NG-TC002-Installer-macos-arm64.dmg` on `main` (app version
  0.1.0). It is ad-hoc signed and not notarized, and no checksum is published. It downloads the
  firmware itself.
- On USB the clock is an ADB device named "Zkswe" (vendor 0x18D1, product 0xD002). A running
  `adb` server on the Mac takes it and the installer says "USB is in use"; `adb kill-server`
  first. Argent and Android tools restart that server.
- macOS asks "Allow accessory to connect?" the first time. A poor cable shows as "USB did not
  become stable" (the device dropped off USB every few seconds).
- The clock joined Wi-Fi from the installer's Home Wi-Fi fields and announces itself as
  `awtrixng-77a767` (`.local` by mDNS; the router also names it `awtrixng-77a767.lan`).
- **A new install has two built-in apps in the rotation, Time and Status**, 7 s each, so a
  pushed app only shows a third of the time. `PUT /api/v1/apps/order
  {"order":["litter"],"disabled":["Time","Status"]}` answered `200 {"ok":true}` and left only
  ours. `GET` on that route is 405. Not yet checked: whether this survives a reboot (#141, #142).
- **First frames (commit 7f3279a, `scripts/push_test.py`):** the transport's `enlargeApps:
  false` and bitmap push were accepted unchanged. Read-back of 12 animated frames matched what
  was sent on all 832 pixels, with `brightness` at 120 and `gamma` 1.9. So
  `GET /api/v1/display/screen` **is row-major from the top left and is before brightness and
  gamma: confirmed on hardware.**

### Open reports that mention the TC002 (2026-11-16)
- #141: the order of apps changes after a reboot. #142: a disabled Status app turns itself
  back on after a reboot. Neither matters with a single pushed app, but check that the built-in
  apps stay out of the rotation after a reboot.
- Closed: #86 the clock never rejoined Wi-Fi after a router restart (fixed in 1.2.3); #85 the
  battery would not charge past about 80 %.
- No open report of flicker, crashes or pushed bitmaps misbehaving was found.

## Where our fake clock guesses (`arthur/sim/clock.py`)
The simulator follows this file, but these points are assumptions about the real firmware.
Check each one once NG is on the TC002, by comparing `GET /api/v1/display/screen` on both.
- Re-pushing an app clears the stale mark and restarts its lifetime.
- With no app pushed, the screen is black.
- Indicator pixels: AWTRIX 3's corner, bar and corner shapes moved to the right edge of the
  52x16 matrix, drawn above everything including the stale mark. Blink is on for the first half
  of the period; fade is a cosine over one period.
- Settings: only `enlargeApps` is simulated, and `GET /api/v1/settings` returns only that key
  where a real clock returns them all. It starts on, as documented. While it is on, the fake
  clock doubles the top-left 26x8 of what was pushed and still draws the stale border and the
  indicators at normal size; NG does not say what it does with the border. An empty `PATCH`
  body is accepted and changes nothing, which the docs do not cover. Every other settings key is
  refused as not simulated, including real ones such as `brightness`.
- A base64 bitmap of the wrong length is refused with 422. NG's behaviour is not documented.
- The fake clock answers "not simulated" (422 for keys, 501 for routes) for anything real in NG
  that it does not render. A real clock would accept those.
- The most recently pushed app shows; app rotation is not simulated.

Error bodies use NG's shape: `{"error": {"code", "message", "field"?}}`.
