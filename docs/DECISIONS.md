# DECISIONS.md

Numbered and **append-only**. Never edit or delete an entry; supersede it with a new one that
says what changed and why. When an entry is superseded, one italic _"→ See D-NNNN"_ line may be
added under its heading.

Format: `D-NNNN — <title>` · date · the decision · why · what was rejected.

---

## D-0001 — Design for both the TC001 and the TC002 until Kyle picks one

_→ See D-0010_

**2026-11-15.** The display is either a Ulanzi TC001 (32x8) or a TC002 (52x16). Every display
state gets a layout at both sizes, and the studio shows them side by side.

**Why.** The TC001's weakness is that state, countdown and drawer level must take turns. At
52x16 they fit together, with larger digits and 16x16 icons. But AWTRIX NG only reached the
TC002 as a beta in late September 2026, and its light sensor, chip and size are unverified.
Because the feeder draws the frames (D-0003), supporting both sizes is cheap.

**Rejected.** LaMetric Time (only the 8x8 icon area is colour). Divoom Pixoo-64 (unofficial
local API that a 2026 firmware update broke). Tidbyt (not sold new). Bare HUB75 panels (no
enclosure). E-ink (refreshes in minutes). Small LCDs (hard to read across a room).

## D-0002 — AWTRIX NG, pinned to 1.2.3, instead of AWTRIX 3

**2026-11-15.** Supersedes the handoff's "flashed with AWTRIX 3".

**Why.** AWTRIX 3 was discontinued in August–September 2026 and has no maintained fork. NG is
its from-scratch successor and supports both clocks. NG also fixes two AWTRIX 3 problems that
mattered here: it checks app expiry every frame (AWTRIX 3 only at app transitions), and a
single app stays on screen permanently.

**Risks accepted.** One maintainer; PolyForm Noncommercial licence (fine for personal use);
1.2.2 broke the API, so the version is pinned; open TC001 reports about reboot loops and
battery behaviour.

**Rejected.** EspHoMaTriX (ended September 2026 and recommends NG). WLED (loses the TC001
buzzer and buttons). Custom firmware (days of work for little gain).

## D-0003 — The feeder draws every pixel; the clock only shows a bitmap

**2026-11-15.** Supersedes the handoff's use of AWTRIX text, icon and progress keys. The
transport pushes one app whose payload is a full-frame bitmap in `draw`.

**Why.** The simulator runs the same renderer as the feeder, so the browser shows exactly what
the LEDs get. We depend on very little of a young API. The same frames work at either clock
size and on other firmware later.

**Cost.** Animation needs a push per frame, so screens are designed around about one update a
second. Device-side text effects are not used.

## D-0004 — Stale data is shown by the clock itself, using NG's lifetime mark

**2026-11-15.** Every push carries `lifetimeMs` (three heartbeats) and `lifetimeExpiry: "mark"`.
If the feeder, the Pi or the LAN dies, NG draws a dark-red border on its own.

**Why.** The signal must not depend on the thing that failed.

**Known gap.** Pushed apps live in RAM, so after a clock reboot with the feeder dead there is
nothing to mark. A small on-device watchdog script (scripts survive reboot) is planned as a
later hardening step.

## D-0005 — pylitterbot over Whisker's cloud, with our own periodic refresh

**2026-11-15.** Data comes from pylitterbot 2025.8.0: the WebSocket subscription plus a
`refresh()` every five minutes, because the library reconnects but never re-polls.

**Why.** It is the only maintained real-time route for an unmodified robot, and Home
Assistant's official integration is built on it. When Whisker broke the API in April 2026 the
library was fixed within days.

**Risks accepted.** The API is unofficial and can break. Whisker's terms prohibit automated
tools; no bans were found in research.

**Rejected for now.** The no-cloud ESPHome firmware for the robot (needs a programmer board,
possibly soldering, may void the warranty). Home Assistant as the middle layer (waits weeks
for library fixes and needs more RAM than the Pi has).

## D-0006 — The feeder runs on a Raspberry Pi on the LAN

**2026-11-15.** Local push to the clock, nothing exposed to the internet.

**Rejected.** A cloud host with the clock polling an HTTPS endpoint (a second failure point and
a public URL). An existing always-on computer (stale whenever it sleeps).

## D-0007 — uv, with Python pinned to 3.13

**2026-11-15.** `uv` manages Python and dependencies; `.python-version` is 3.13 to match
Raspberry Pi OS (trixie). Code stays compatible with 3.11, pylitterbot's minimum.

## D-0008 — Lightweight harness: two memory docs, a Fable-only routing rule, commit per step

**2026-11-15.** `docs/STATUS.md` (current state, imported by CLAUDE.md) and this file are the
project memory. On Fable, the main loop plans and reviews and delegates coding to Opus
subagents; on other models that rule is inert. One commit per finished step on `main`; nothing
is pushed.

**Why.** Claude Code has no setting for "plan on Fable, execute on Opus"; a written rule is the
only mechanism. An `@` import needs no hook script.

## D-0009 — Screens are playful, warm-coloured, and built around an orange Arthur

**2026-11-16.** Kyle's direction after seeing the first-draft screens: this is a fun project,
so screens should be engaging and funny. Arthur is an orange cat and is the mascot. The office
is reddish, so the palette is warm (oranges, golds, warm white), with one cool accent (mint)
kept for the robot cleaning. "In use" is the funny moment: Arthur with a poop emoji. Idle shows
bin level, litter level and one more thing. Rare error states share a template with a line of
text saying what is going on; "no data" is red-family, not purple. Captions are jokes kept in
`config/display.toml`.

**Why.** The first draft was clear but clinical: a blue cat, cyan timers, purple errors.

**Constraints kept.** Clear first, funny second: states must still differ by shape and colour
at a glance. Idle stays nearly still, because every changed frame is a push to the clock. We
cannot tell pee from poop, so nothing claims either.

**Status.** A direction, not a finished design. Kyle expects to iterate over several days.

## D-0010 — The display is a Ulanzi TC002 (52x16); the 32x8 size is removed

**2026-11-16.** Supersedes D-0001. Kyle bought a Ulanzi TC002 and a Raspberry Pi 5. Screens are
designed at 52x16 only. The 32x8 layouts, icons, golden images and the second fake clock were
deleted; commit 91e4960 is the last one that has them.

**Why.** The choice D-0001 was waiting for is made. A second layout family doubles the work of
every design change, and the 32x8 screens were clearly the weaker ones.

**Kept.** D-0003 stands: the feeder draws every pixel. `Frame` and `render()` still take a
size, so a different display later would be a new layout family, not a rewrite.

**Host.** The Pi is a Raspberry Pi 5, which settles D-0006's "a Raspberry Pi". Its RAM size is
not recorded.

**What the TC002 changes** (verified 2026-11-16; details in `docs/reference/awtrix-ng.md`):
- NG draws pushed apps at double size on a 26x8 grid unless the `enlargeApps` setting is off.
  The transport turns it off before pushing, and the fake clock starts with it on, as a real
  clock does.
- It has no light sensor and NG has no night schedule, so night dimming is the feeder's job.
- It is a small Linux board, not an ESP32. The request body limit is 2 MiB, so frame size is no
  longer a constraint.
- NG support is a normal release (first in 1.2.2, current in 1.2.3), not a beta as the earlier
  research said. The 1.2.3 pin stands (D-0002).

## D-0011 — Level bars follow the robot's states; drawer-full gets a flashing edge ring

**2026-11-16.** Kyle's direction after watching the studio follow his real robot.
- **Litter** is coloured by the robot's own state word, not by a percentage: green `OPTIMAL`,
  yellow `REFILL` and `OVERFILL`, red `LOW` and `EMPTY`. The screen therefore always agrees
  with the phone app.
- **The drawer** bar is orange and turns red at `drawer_warn_pct` (80). The green and amber
  steps are gone.
- On idle, the two badges those bars replace are not drawn.
- **`drawer_full`:** the bin under the poop is gone, a 1-pixel red ring flashes on the outermost
  pixels once a second, and the words are "FULL" and "EMPTY NOW".

**Why.** On his robot both bars came out orange and said nothing. Whisker does not publish the
cut-offs behind the litter word, and the drawer percentage is rough (it read 68, 63 and 60
across three cycles), so the word is the better signal for litter and one threshold is enough
for the drawer.

**Exception accepted.** The ring uses the outer row and column, which every other screen
leaves to the clock's dark-red stale border (D-0004). If the feeder dies while the drawer is
full, the clock paints its steady dark-red border over the ring. The two differ in brightness
and in flashing. If they prove confusable on the LEDs, move the ring one pixel in, which costs
text room.

**Costs.** "EMPTY NOW" only fits with narrower M and W letters, drawn for that line. The ring
means a push every second for as long as the drawer stays full.

**Rejected.** Our own percentage cut-offs for litter (they would disagree with the app). Orange
for `REFILL`, which Kyle suggested: orange is the drawer's colour, so yellow was used. Green
for a drawer with room (Kyle prefers orange throughout).

## D-0012 — One layout per live state, soapy blue for cleaning, and no badges

**2026-11-16.** Kyle's direction after reviewing the screens in the studio.
- **In use, waiting and cleaning each have one layout.** The alternatives were removed (in
  use's door and timer versions, waiting's big countdown, cleaning's gauge). Idle keeps its
  three versions until he chooses.
- **In use** takes turns between two pages: the poop emoji, with the same face as drawer-full,
  beside a joke and "IN USE"; then a smug Arthur, without the litter box, with the timer and
  the drawer bar. A new joke shows each time the joke page comes round, from six he kept.
- **Waiting** is the hourglass, "CLEAN IN" and the countdown.
- **Cleaning and just-cleaned are a soapy light blue**, replacing mint; D-0009's "one cool
  accent" is now this blue. Cleaning says "CLEANING" and has a blue border with bubbles moving
  round it, on the outer edge like drawer-full's ring (the exception accepted in D-0011).
- **Badges are removed.** The bar colours cover drawer and litter (D-0011). A dirty laser
  shows as an amber line on idle. On-battery and connection-down are not shown.
- **Idle** moves its bars and text right into the freed columns and Arthur gets a longer tail.

**Why.** He wants settled screens with more life, and the badges never explained themselves.

**Accepted.** A robot on battery, or a feeder that has fallen back to polling, is invisible on
the display. A real outage still shows `no_data` or the clock's stale border (D-0004).

**Costs.** The cleaning border means a push every second during a cycle, which the countdown
already caused. The laser notice only has room for the word "LASER".

## D-0013 — Idle Arthur is alive: a one-minute script of gestures

**2026-11-16.** Changes D-0009's "idle stays nearly still". Kyle asked for the idle cat to have
life: blinks, looks, tail wags, the tail passing through the text.
- Idle runs a 60-second script, one pose a second, about two thirds of it at rest: uneven
  blinks, a look left or right, an ear twitch, a squint, and a four-second tail wag twice a
  minute. A yawn replaces one rest about every five minutes.
- On the wag the tail swings across the bottom line. Letter pixels it crosses turn a light
  orange (`fur_tint`); the gaps between letters are left dark, because orange there made the
  letters unreadable.

**Why the old rule could go.** It was there to spare the clock pushes. The TC002 is a small
Linux board on the LAN, and every live state already pushes once a second.

**Limit kept.** Alive, not busy: idle must be still for at least half of every minute (tested),
since it is on screen most of the day in the corner of the eye.

**Cost.** About 27 pushes a minute while idle, up from two.
