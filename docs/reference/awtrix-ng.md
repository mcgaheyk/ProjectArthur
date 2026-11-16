# AWTRIX NG reference

Target version: **1.2.3** (2026-11-03). Collected 2026-11-15 from the official docs
(<https://blueforcer.github.io/awtrix-ng/esp32/>) and, where marked **[src]**, from the source
(<https://github.com/Blueforcer/awtrix-ng>). The API spec is `docs/api/openapi.yaml` in that
repo. **Confirm each item against the spec before code depends on it**, mark it confirmed
here, and add what you learn.

AWTRIX 3's API (`/api/custom`, `/api/notify`) does not exist in NG. There is no compatibility
layer.

## General rules
- JSON only; `Content-Type: application/json` is required (415 otherwise).
- An unknown top-level key → **422** `validationFailed` naming the `field`. Validation is
  all-or-nothing: on error nothing is saved.
- Body limit 8192 bytes (413). JSON nesting limit 16 (400).
- Keys are camelCase; durations are milliseconds with an `Ms` suffix.
- Colours: `"#RRGGBB"`, `"RGB"`, `[r,g,b]`, or an integer. Read back as `#RRGGBB`.
- HTTP Basic auth is off by default. CORS is open.

## Pushed apps (what the transport uses)
- `PUT /api/v1/apps/pushed/{name}` creates or replaces. Name: `[A-Za-z0-9_-]{1,32}`.
- `DELETE /api/v1/apps/{name}` removes. An empty body or `{}` on PUT is 422, not a delete.
- Pushed apps live **in RAM**: gone after a clock reboot.
- With one app in the rotation, the clock stays on it permanently.

Keys we send:

| Key | Meaning |
|---|---|
| `draw` | Array of drawing commands, as arrays. We send one: `["bitmap", x, y, w, h, data]` where `data` is **base64 RGB888** (`w*h*3` bytes, row-major from the top left) or an array of colours |
| `lifetimeMs` | 0 = forever. Counts from when the clock received the push |
| `lifetimeExpiry` | `"remove"` (default) or `"mark"`: the app stays, with a 1-pixel dark-red `#6E0700` border |
| `durationMs` | How long the app shows per turn; irrelevant with a single app |

- Frame sizes: 32x8 → 768 bytes → 1024 base64 characters. 52x16 → 2496 bytes → 3328 characters.
- Other `draw` commands exist (`pixel`, `pixels`, `line`, `rect`, `rectFill`, `circle`,
  `circleFill`, `text`) but we do not use them (D-0003).
- Keys that exist but we do not send: `text`, `textColor`, `icon`, `progress`,
  `progressColor`, `backgroundColor`, `effect`, `overlay`, `barChart`, `lineChart`, `scroll`,
  `font`, and others. The simulator should refuse them loudly rather than pretend.

### Render order **[docs]**
Background → text and drawings → **stale border** → icon → extra icons → overlay. So the border
overwrites the outermost row and column of our bitmap.

### When expiry is checked **[src]**
`CoreEngine::tick()` runs once per main loop before rendering and checks every pushed app's
lifetime. `SpecRenderer` then draws `drawRect(0,0,w,h,0x6e0700)`. The mark appears within one
frame of expiry, even on the app that is showing.

Not confirmed: that re-pushing clears the mark (inferred from the code's structure).

## Indicators
- `PUT /api/v1/indicators/{1|2|3}` with `{color, blinkMs, fadeMs}`; `DELETE` to clear. Small
  lights on the right edge: 1 top, 2 middle, 3 bottom. Drawn above apps.
- Exact pixels at each clock size are not confirmed for NG. (On AWTRIX 3 at 32x8 they were
  (31,0),(30,0),(31,1) / (31,3),(31,4) / (31,7),(31,6),(30,7).)

## Reading the screen back
`GET /api/v1/display/screen` → `{width, height, pixels[]}`, row-major from the top left,
decimal `0xRRGGBB`, **before** brightness, gamma and colour correction. This is how the
simulator will be compared with the real clock.

## For later features
- **Notifications:** `POST /api/v1/notifications` (`hold`, `stack`, `wakeup`, `sound`, `name`);
  `DELETE /api/v1/notifications/active` dismisses the one showing.
- **Rotation and built-in apps:** `PUT /api/v1/apps/order {"order":[…],"disabled":[…]}`
  persists; `PUT /api/v1/apps/active {"name","fast":true}` shows an app now.
- **Settings:** `GET`/`PATCH /api/v1/settings`. `brightness` 0–255, `autoBrightness`,
  `minBrightness`, `maxBrightness`. There is **no built-in night schedule**; the maintainer
  points to scripts, or the feeder can set brightness on a timer.
- **Buttons:** a `buttonCallback` URL receives `{"button","state","uid"}` over plain HTTP.
- **Scripts (Berry):** run on the clock, survive reboot, can draw at about 40 fps, make
  HTTP(S) requests and read `now_ms()`. The official `Doorstate` recipe turns the screen grey
  when its data is old; that is the pattern for our watchdog.
- **Art-Net:** the clock can display frames streamed over UDP 6454 and returns to its app
  rotation about 5 s after the last frame. An alternative transport.
- **`awtrix-linux`:** the same core as a host program, any size from 8–128 x 8–32, with the
  HTTP API. Could serve as a conformance check. Needs CMake and PlatformIO.

## Fonts
The built-in `small` font has 5-pixel capitals on rows 1–5, 4 pixels per character, 2 for a
space. `large` has 7-pixel capitals. Sources in the NG repo: `assets/fonts/awtrix.bdf`,
`assets/fonts/matrix-fonts/`. The docs give the licence as **SIL OFL 1.1**. Check the repo's
`LICENSES/` and `THIRD-PARTY-NOTICES.md` before vendoring a font, and keep the notice with it.

## Hardware
- **TC001:** 32x8, three buttons, buzzer, light sensor, battery. Flashed from a desktop Chrome
  or Edge browser. Open reports: reboot loop on some units (#105), scripting disabled after a
  battery-depletion shutdown (#145).
- **TC002:** 52x16, knob, speaker. NG arrived as a beta in late September 2026 and uses a USB
  installer. Light sensor, chip and current status are unverified.
- 2.4 GHz Wi-Fi only.
