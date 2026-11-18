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

_→ See D-0019 for the frame rate._

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

_→ See D-0014 for the tail, and D-0019 for the stillness limit._

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

## D-0014 — The idle tail stays in the gap and never changes length

**2026-11-16.** Supersedes the tail part of D-0013. Kyle did not like the tail getting longer
or passing through the text. The wag is now a small lift in the space between Arthur's body and
the bars and text (columns 15–18), and every tail pose has the same number of pixels, so it
reads as one tail bending. The tint colour and the text-check exception that the through-text
effect needed are removed; nothing draws on or against a label.

## D-0015 — Just cleaned is a five-second "FRESHLY CLEANED"

**2026-11-16.** Kyle felt the just-cleaned screen stayed up too long (60 s). `just_cleaned_s`
is now 5, and the screen reads "FRESHLY" small over "CLEANED" big, in soapy blue beside the
sparkle; the rotating jokes and the drawer bar are gone from it. The idle screen, which shows
the drawer, follows within seconds.

**Consequence for the feeder.** A five-second window is only shown if the feeder sees the
change to `READY` promptly and dates it from when it saw it. `status_since` must be the time
the feeder learned of the change, not a robot timestamp that may arrive late.

**Studio.** Fixtures are now mapped once, at the fixture's own time, while the render clock
runs (gallery tiles and fixture mode). Otherwise the just-cleaned tile turned into idle after a
few seconds of its loop.

## D-0016 — Interrupted is a little story, not a shocked cat

**2026-11-17.** Kyle said the interruption happens often and wanted something good for it. The
old screen was a wide-eyed Arthur, "HEY!" and "STOPPED". Over four rounds in the studio he
compared twelve candidates and chose a combination of two of them.

- One 21-second episode, counted from when the interruption began, then again from the top:
  Arthur strolls up to the globe and puts his head in (4 s); the view cuts to inside the box,
  his paws hook over the bottom edge and his face appears, eyes opening, looking left and
  right (6 s); "INTERRUPTION" across the full width (2 s); his face again (4 s); a second
  phrase, a new one each episode, from `interrupted_look` in the config (3 s); the globe
  alone (2 s).
- The wording is Kyle's: "INTERRUPTION", "TAKIN' A LOOK", "watching it spin". The story is
  that Arthur comes to have a look, not that he was caught. The paws-on-the-edge view from
  inside the box was his idea too.
- The title, phrase and rest lengths are in `[timings]`; the walk and the face's script are
  in the renderer, like idle's script.

**Given up.** Words are on screen for 5 seconds in every 21, and nothing says "stopped". A
glance usually lands on a picture. Kyle preferred the story; the walking cat and the face in
the box appear on no other screen, so they identify the state by themselves.

**Not known.** How long a real interruption lasts. One shorter than ten seconds ends before
the title shows.

**Rejected along the way** (commits eaf7b09, f3990d2 and 4c352a2 have them): a stop-paw pun
("PAWSED"), the cleaning screen frozen inside a hazard border, a deadpan robot, and single
screens of the walk or the close-up with a caption.

## D-0017 — A visit is read from the weight reading, not from the status

**2026-11-17.** The spike logged seven real visits and three short detections. They showed
that the two visit statuses do not follow the cat: `CAT_SENSOR_TIMING` arrived 6 to 10 seconds
after detection in four visits and never in the other three. Mapped by status, in-use either
flashed by or ran for nine minutes, and the countdown ended up to 81 seconds early.

- **He has left when the weight reading arrives.** The push that changes `pet_weight` came 48
  to 86 s after detection, and the clean cycle started 476 to 482 s after it in every visit,
  with a wait setting of 8 minutes. So `CAT_DETECTED` and `CAT_SENSOR_TIMING` are treated
  alike: in use until the weight arrives, then waiting, counting `wait_time_minutes` from that
  moment. A second weight in the same visit restarts the count.
- **No weight after `cat_left_fallback_s` (120 s):** waiting without a countdown. That is
  honest about two visits weighing the same, and about Whisker's weight pushes breaking again
  as they did for two months in 2026.
- **A detection is a visit** only if a weight arrived or it lasted `visit_min_s` (30 s).
- **Cleaning stays a timed estimate, now 130 s** (real cycles: 123 to 134 s). The raw phases
  are real but each varies as much as the whole, and resyncing at each one would make the
  digits jump.
- **The offline age is ours.** It counts from when the feeder saw the robot go offline. The
  robot's `last_seen` was frozen at one date for the whole spike.
- The observed facts (`status_since`, `visit_started_at`, `cat_left_at`, `last_visit_at`) are
  kept by one tracker, `arthur/sources/tracker.py`, used by both the log reader and the live
  source, so replaying the real log in the studio runs the same code as the feeder.

**Not known.** What a live interruption looks like; the two in the robot's history resumed
without a new wait, so the scenario now does too. Whether the weight push ever arrives after
the robot has already started its count by some other rule: seven visits is a small sample.

## D-0018 — The feeder is also the logger, and it never gives up

**2026-11-17.** The feeder was built and started on Kyle's Mac against the real robot and the
real clock. Two things it does differently from the spike it grew out of:

- **It replaces the spike.** Whisker allows us one connection (D-0005), so the feeder and
  `scripts/spike_log.py` cannot both run. The feeder writes the same `logs/spike-*.jsonl`
  files in the same format, so the studio's Robot log source still follows the real robot and
  the data keeps accumulating. The spike script stays for logging without a clock.
- **A rejected login is retried, slowly:** after 10 minutes, doubling to an hour. The spike
  exits instead, to avoid locking the account; the feeder has to keep the screen honest, so it
  shows NO DATA and tries again at a pace that cannot lock anything. Missing credentials are
  not retried: NO DATA until it is restarted.
- **The activity history fills in what the feeder did not watch.** At start it reads the last
  72 hours for the last visit and, if a visit is under way, when it began and whether he has
  left. If a visit has no weight push after `cat_left_fallback_s`, it asks the history once
  for that visit. These are the robot's own timestamps, the one exception to D-0015.
- **When the feeder stops, it does not clear the clock.** The last frame stays and the clock
  marks it stale after `ttl_s` (D-0004). On the Mac that also happens whenever the Mac sleeps.
- The transport keeps ours the only app in the clock's rotation, rechecked every five minutes,
  because a new install rotates Time and Status with it and reports say the rotation can
  change after a reboot.

**Not tested, because it has not happened yet:** a real login failure, Whisker's connection
dropping and pylitterbot reconnecting, a poll that hangs, a visit with no weight push, an
interruption, and a clock reboot under a running feeder.

## D-0019 — Idle is a show: half home, the rest weather and scenes

**2026-11-18.** Kyle's direction: the clock sits on idle most of the day, so idle should rotate
through more than one screen. This changes D-0013's stillness limit and D-0003's frame rate.

- **The split is his: 50 % home, 30 % weather and date, 20 % scenes.** One three-minute round,
  lined up with the wall clock: home 45 s, weather 54 s, home 45 s, a scene 36 s. The shares
  and the round length are in `[idle]` in the config.
- **Scenes are long and mostly quiet.** He chose 36-second scenes about every three minutes
  over 10-second chases every minute. In each, Arthur stays seated and watches a rat or a bird
  for about 22 s, then about 8 s of chase or leap, then he settles back in his spot. The first
  scenes: a rat scurrying across the empty screen with Arthur after it; the same with the
  tables turned, one time in four; Arthur leaping at a bird with his arms spread.
- **At night Arthur sleeps.** From 22:00 to 07:00 Boston time the home page shows him curled
  up and there are no scenes; their time goes to the home page. The weather page carries on.
- **Every change inside idle has a transition, and none is a brightness fade.** Home and
  weather swap by a dissolve in place. A scene opens with the bars and text wiping away beside
  seated Arthur and closes with them wiping back in.
- **A real robot event cuts in at once**, mid-scene if need be, with no transition. Nothing in
  the show may delay a visit, a fault or drawer-full.
- **The schedule is decided in mapping** (`arthur/schedule.py`, a pure function) and carried
  on `DisplayState` as a cue; the renderer only plays it. `just_cleaned` is the precedent.
- **Scenes and transitions run at 8 frames a second**; the home and weather pages stay at one.
  The feeder's loop moves from a fixed sleep to a steady 1/8 s beat.
- **A decoration must never stop the display.** If the weather source or a scene fails, the
  feeder shows the home page and logs it.

**Why.** He wants the screen to be fun and useful while nothing is happening. D-0013 kept idle
"still for at least half of every minute" because it is in the corner of the eye all day; he
has now chosen a livelier display, and that rule holds only for the home page itself. The
research (`docs/research/2026-11-18-led-animation-and-idle-cadence.md`) says motion that
travels across the screen is the most distracting kind and small movement in one place the
least, which is why a scene is mostly watching: fast cross-screen motion comes to about 4 % of
idle time. The same research rules out fades, because a dimmed orange turns red on these LEDs.
Eight frames a second is the measured comfortable rate: the slowest push took 64 ms of a
125 ms budget (`docs/reference/awtrix-ng.md`). Deciding the schedule in mapping keeps the home
page's tests and snapshots true whatever the shares are set to.

**Cost.** Pushes to the clock roughly triple, from about 1,600 an hour to about 4,500. With
two characters, each plays about ten times an hour; varied middles and endings carry that at
first, and more scenes are the lasting cure.

**Rejected.** A first plan with the weather for 12 s every five minutes and a 10-second scene
about every 20 minutes: Kyle wanted far more of both. Quick chases every 50 seconds. Deciding
the schedule in the renderer, which would have tied the idle tests to the cadence values. A
laser dot, a mug pushed off a desk and a page of visits and weight were offered and not chosen.

**Not known.** How 8 pushes a second look on the LEDs, whether the dissolve reads as glitter
or as a glitch, the rat's and bird's colours, and how the split feels over a working day. When
this was written only the bench (`scripts/push_bench.py`) was built.

## D-0020 — Weather comes from Open-Meteo, and it is a decoration
_→ See D-0021 for the layout: the page no longer shows the high and low._

**2026-11-18.** The idle show's page (D-0019) shows Boston's temperature, a weather picture and
today's high and low, with the date as month and day. Kyle does not want the day of the week.

- **Source: Open-Meteo's forecast API**, one request every 15 minutes for the city-centre
  coordinates in `[weather]`. No account and no key. The request carries nothing that
  identifies Kyle. Confirmed with one live request (`docs/reference/open-meteo.md`).
- **It is not robot data and is held to a lower standard of duty.** The reading reaches the
  screen only on idle. One older than `max_age_s` (45 minutes) is dropped in mapping and the
  page shows the date alone; an old temperature is never shown as current.
- **It can never hurt the display.** The weather source runs as a second service beside the
  Whisker source. If it fails, or cannot be read, the feeder carries on without it.
- **Licence:** CC BY 4.0, attribution "Weather data by Open-Meteo.com".

**Why.** One call returns everything the page needs, with a numeric weather code that maps
straight to a picture. The US National Weather Service gives real station readings but takes
several calls, returns free text, and its observations can lag or come back empty.

**Accepted.** The values are from weather models, not a thermometer at the airport, so the
temperature can differ a little from other apps. After the Mac sleeps, the page shows the date
alone until the next fetch.

**Not known.** How often the "current" values really change, and what a rate-limit refusal
looks like. The free terms allow 10,000 calls a day; we make 96.

## D-0021 — The weather page is the big-temperature layout

**2026-11-18.** Kyle compared the two layouts built for the idle show's page (D-0019) and chose
the big one, `weather_big`: the weather picture, the temperature at double size, and the date
stacked at the right (`NOV` over `18`). It is the page a page cue plays.

**Why.** His pick, made from a side-by-side picture and the studio. The temperature fills the
height of the screen, so it reads from across the room.

**Rejected.** The layout with the temperature at normal size, today's high and low beside it
and the date on one line. D-0020 says the page shows the high and low; it no longer does. The
weather source still fetches them.

**Accepted.** The rejected layout stays in the code as `weather`, never played, with one tile
(`idle_page_detail`), until Kyle has seen the page on the LEDs. Remove it then.

**Not known.** How the double-size digits look on real LEDs.

## D-0022 — The bird leap is seen from the side, and a third scene: the stretch

**2026-11-18.** Two directions from Kyle after seeing the first cut of the scenes (D-0019).

- **The leap at the bird is side-on.** His words: "arthur jumping left or right at the bird,
  not jumping at the camera". Arthur now crouches well short of the bird and leaps across the
  stage at it. At the top his forelegs fly open in front of it, clap shut on nothing as it
  hops away, and he hangs there for a moment before he drops. The feather and the perched
  ending are unchanged.
- **A third scene, the stretch.** His words: he "walks into the center of the screen and then
  does the classic cat stretching pose". It is drawn from his side-view photo: forelegs flat
  out in front, chest down, the back sloping up to the rump, tail hooked over. A real stretch
  comes in two halves, so his does: the bow, held about 3 s with his eyes shut after a yawn,
  then each hind leg stretched out behind. One round in four he kneads the floor.
- **Where the stretch goes.** It is one more scene in the same slot, in the shuffled bag with
  the rat and the bird, no scene twice in a row. The 50 / 30 / 20 split (D-0019) is
  unchanged; each scene now plays about six or seven times an hour, not ten.

**Why.** His direction. The stretch is also the calm scene the set lacked: nothing in it
travels fast, which the research behind D-0019 rates the least distracting kind of motion.

**Rejected.** The leap with his belly to us and arms spread (`REACH`, `SPREAD`), removed. A
slot or share of its own for the stretch: one bag keeps the cadence Kyle set.

**Not known.** Whether the open-then-clap reads at 8 frames a second on the LEDs, and whether
the second hind-leg stretch and the kneading show from across the room.
