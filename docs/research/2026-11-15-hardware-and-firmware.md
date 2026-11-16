# Hardware and firmware deep dive (2026-11-15)

_Outcome (2026-11-16): Kyle bought the Ulanzi TC002 and a Raspberry Pi 5. See D-0010. The rest
of this file is the research as it stood on the 15th._

The question: is a Ulanzi TC001 running AWTRIX 3, fed by a Raspberry Pi 5, the right way to
build this display? Three research passes covered display hardware, firmware, and the host and
data source. Prices are US street prices found that day. Items marked _unverified_ had no
source that could be fetched.

## Conclusions
1. **Display:** the TC001 holds up as the safe choice. Its 2026 successor, the TC002, is the
   one real alternative. Undecided; see D-0001.
2. **Firmware:** AWTRIX NG. AWTRIX 3 and its main alternatives have ended. See D-0002.
3. **Drive method:** the feeder renders frames and pushes a bitmap. See D-0003.
4. **Data source:** pylitterbot is still the only maintained real-time route. See D-0005.
5. **Host:** a Raspberry Pi on the LAN works and was chosen; a cloud host is a workable
   alternative. See D-0006.

## Display hardware

| Device | Pixels | Price | Driven by | For | Against |
|---|---|---|---|---|---|
| Ulanzi TC001 | 32x8 | $46–60 | AWTRIX NG, flashed from a browser | Mature; light sensor, 3 buttons, buzzer | Tight: icon plus about 6 characters. Built-in battery in a device that stays plugged in |
| Ulanzi TC002 | 52x16 | $100–190 (US price varied by source) | AWTRIX NG (beta from late Sept 2026), USB installer | Two lines, speaker, knob | Very new; flicker reported in a community port; reportedly no light sensor; chip and size unverified |
| LaMetric Time | 37x8 | $196 | Official documented local API | Polished; light sensor; speakers | Only the left 8x8 is colour; text is white |
| Pimoroni Galactic Unicorn | 53x11 | US price unverified | MicroPython you write | Full colour, finished-looking | You maintain all firmware; some distributors list it as discontinued |
| Divoom Pixoo-64 | 64x64 | $130–160 | Unofficial local HTTP API | Lots of pixels | 2026 firmware moved the endpoint and broke libraries; app needed for setup; reports of freezing after about 300 pushes |
| Tidbyt | 64x32 | Not sold new | Tronbyt (self-hosted) | Open server, active | Company folded into Modal in 2024; hardware unavailable |
| MatrixPortal S3 / Waveshare + HUB75 panel | 64x32 | $45–70 | Tronbyt or your own code | Cheap, flexible | Bare board and panel, no enclosure |
| GeekMagic SmallTV, M5Stack and similar LCDs | 240x240 and up | $15–70 | Varies | Cheap, sharp | Small screens, likely hard to read across a room (a judgement, not tested) |
| E-ink (TRMNL) | — | — | — | — | Refreshes every 5 minutes at best |

Not found: any sourced report of TC001 batteries swelling. Not researched: iDotMatrix, LaMetric
Sky, Pi-attached HATs beyond a note that HUB75 on a Pi 5 needs Adafruit's PioMatter library.

## Firmware

| Option | Status | Who renders | Stale signal with a dead host | Verdict |
|---|---|---|---|---|
| AWTRIX NG 1.2.3 | Active: 7 releases Aug–Nov 2026; one maintainer; PolyForm Noncommercial | Device, or host via bitmap or Art-Net | Yes: lifetime mark checked every frame; scripts can draw their own | **Chosen** |
| AWTRIX 3 0.98 | Discontinued; binaries still downloadable; no maintained fork | Device | Yes, but only checked at app transitions | Dead end |
| EspHoMaTriX | Ended Sept 2026; recommends NG | Device | Screens vanish when unrefreshed | Dead end |
| Plain ESPHome | Active | Device (C++ lambdas) | Needs YAML logic | Possible, poor simulator fidelity |
| WLED + DDP/UDP | Very active | Host | Yes: returns to its preset about 2.5 s after frames stop | Good alternative; loses TC001 buzzer and buttons |
| Custom firmware | Yours | Either | Whatever you build | Days of work |
| Tronbyt + Pixlet | Active | Server | Not documented | Needs 64x32 hardware |

AWTRIX NG facts that shaped the design:
- Pushed apps accept a full-frame bitmap in `draw`.
- `lifetimeMs` with `lifetimeExpiry: "mark"` draws a dark-red border, checked every tick.
- A single app stays on screen permanently.
- `GET /api/v1/display/screen` returns the pixels, so a simulator can be compared with the
  real device.
- Pushed apps are lost on reboot; scripts are not.
- `awtrix-linux` runs the same core on a computer at any size.
- 1.2.2 made breaking changes; 1.2.3 did not.
- Open TC001 issues: #105 (reboot loop), #110 (pixel blocks on an old unit), #145 (scripting
  disabled after battery-depletion shutdown).

## Data source
- No official Whisker API, webhook, Matter or HomeKit support for the LR4 was found. IFTTT is
  for the LR3 only.
- pylitterbot is the reference implementation; Home Assistant's official integration uses it.
  Ten releases from June to November 2026.
- April 2026 breakage: the library was fixed in days; Home Assistant users were broken for
  three to four weeks. Owning the dependency means upgrading the same day.
- Whisker's terms prohibit automated tools and reverse engineering and allow account
  termination. No reports of bans were found.
- **A no-cloud route exists:** `esphome-litter-robot` (Codeberg, Joseph-DiGiovanni) replaces
  the LR4's Wi-Fi firmware and exposes cat detection locally. It needs an ESP-Prog-2 board,
  soldering on units built before February 2026, and may void the warranty. One-person
  project; some faults unnamed.
- No published measurement of how fast "cat detected" arrives.
- Wait time defaults to 7 minutes; a clean cycle takes about 2 to 2.5 minutes.

## Host

| Option | Hardware | Monthly | Notes |
|---|---|---|---|
| Raspberry Pi 5 1GB | $45 plus power supply, card and case (about $75–85) | Under 3 W | **Chosen.** More than this needs. 1GB models were spared the 2026 price rises |
| Raspberry Pi Zero 2 W | $15 list | About 1 W | Sold out almost everywhere in late 2026 |
| Railway or Fly.io | None | $2–5 | Clock polls an HTTPS endpoint with an on-device script. Extra failure point; public URL |
| Existing always-on computer | None | — | Stale whenever it sleeps |
| Home Assistant OS | Pi with 2GB or more | — | Below minimum on a 1GB Pi; lags library fixes |

## Could not verify
- TC002: US retail price, chip, light sensor, whether NG support has left beta, physical size.
- Whether AWTRIX 3's web flasher still works.
- Whether re-pushing an NG app clears its stale mark; what NG shows with an empty rotation.
- Update latency over Wi-Fi for NG; brightness figures for any device.
- LR4 idle power draw (one anecdote puts cycling at about 11 W).

## Main sources
- AWTRIX 3: <https://github.com/Blueforcer/awtrix3>
- AWTRIX NG: <https://github.com/Blueforcer/awtrix-ng>, <https://blueforcer.github.io/awtrix-ng/>
- EspHoMaTriX: <https://github.com/lubeda/EspHoMaTriXv2>
- WLED realtime: <https://kno.wled.ge/interfaces/udp-realtime/>
- Tronbyt: <https://github.com/tronbyt/server>
- TC002: <https://www.ulanzi.com/products/tc002-pixbar-smart-pixel-clock-ii>
- LaMetric: <https://lametric.com/en/time/tech-specs>
- Divoom API note: <https://divoom.com/blogs/app-guide/pixoo-64-api-beginner-guide>
- pylitterbot: <https://github.com/natekspencer/pylitterbot>
- Home Assistant integration: <https://www.home-assistant.io/integrations/litterrobot>
- Whisker terms: <https://www.litter-robot.com/terms-and-conditions/>
- LR4 ESPHome firmware: <https://codeberg.org/Joseph-DiGiovanni/esphome-litter-robot>
- LR4 wait time: <https://www.litter-robot.com/support/article/litter-robot-4-wait-time-settings/>
- Raspberry Pi pricing: <https://www.raspberrypi.com/news/more-memory-driven-price-rises/>
