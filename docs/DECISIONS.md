# DECISIONS.md

Numbered and **append-only**. Never edit or delete an entry; supersede it with a new one that
says what changed and why. When an entry is superseded, one italic _"→ See D-NNNN"_ line may be
added under its heading.

Format: `D-NNNN — <title>` · date · the decision · why · what was rejected.

---

## D-0001 — Design for both the TC001 and the TC002 until Kyle picks one

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
