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

_→ See D-0029_

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
deleted; commit 7d4393e is the last one that has them.

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

**Rejected along the way** (commits 7f7d2e7, 59871b9 and 7ec6ae5 have them): a stop-paw pun
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

## D-0023 — The idle show went live before the long check at the clock

**2026-11-18.** Kyle said to go live with all of it the same afternoon it was built: the
weather page at 30 %, scenes at 20 % (rat, bird and stretch), and the night sleep. The shares
were set in `config/display.toml` at 13:53 and the running feeder took them up by itself.

**What was skipped.** The plan (D-0019) put two things before going live: the ten-minute run
at 8 frames a second with the test patterns on the LEDs, and Kyle's reaction to each screen
in the studio. He had seen stills and the studio, chosen the weather layout (D-0021) and
redirected the leap (D-0022), and had watched in use on the clock. The long run was not done.

**What stood in for it.** The morning's bench: 8 a second for ten seconds, slowest push 64 ms
of a 125 ms budget. And the first live round, read back from the clock's own screen at twelve
moments: every part showed when the schedule said it should, and the feeder logged no error.

**Accepted.** The first hours of real use are the test. The show is one config edit from off
(both shares to 0), with no restart.

**Not known.** How the clock holds up over days at about 4,500 pushes an hour, and how any
of the show looks on the LEDs to Kyle.

## D-0024 — Start the Mac's feeder from Terminal, not from a Claude session

**2026-11-18.** The clock showed the stale border from 17:22 to 18:04. The feeder kept running
and kept reading the robot (it logged a whole visit from 17:26 to 17:37), but every push to the
clock failed with "No route to host" and it logged no recovery. At the same time a new process
on the same Mac reached the clock at once, from the same address.

**Cause, very likely.** macOS's Local Network privacy, which allows or refuses each connection
to the home network by the program responsible for the process making it. The feeder had been
started at 13:47 from a Claude Code session's shell, which made that session responsible for
it. The session closed at 17:22 (the shutdown lines at the end of its transcript are from that
minute); twenty seconds later the feeder lost the clock while keeping the internet. Earlier
feeders never outlived the session that started them. Not proven: the Mac's system log could
not be read from the session.

**Decided.** On the Mac the long-running feeder is started with
`open -a Terminal scripts/start_feeder.command`. Terminal launches it, so it no longer depends
on any session. The 18:04 start was made that way; the clock's read-back then showed no stale
border and a changing picture.

**Rejected.** Restarting it from the session again: it would fail the same way when that
session closed. A launchd agent: it would also survive a Mac restart, but it is the Pi's job
and its own Local Network permission would need checking first.

**Not known.** Whether it holds when Terminal quits (closing the window is fine; the feeder is
detached). On the Pi none of this applies: Linux has no such check.

## D-0025 — A gap in watching is treated like a restart

**2026-11-19.** At 08:29 the clock said Arthur last went 15 hours ago. The robot's history
said 22:39 the night before, about 10 hours. The MacBook's lid had been closed at 22:26, on
battery, so the Mac slept until 23:26 and the feeder with it; the visit fell in that hour. On
waking, the first poll showed one more clean cycle and a new weight, and nothing was made of
them: the feeder counted only a visit it watched begin, and read the robot's history into
"last visit" once, at start (D-0018). The routine read at 01:02 fetched the 22:39 visit, wrote
it to the log and did nothing else with it. Only a restart would have put it right.

**Decided.** What D-0018 does at start, the feeder now does after any gap:

- **A gap** is more than `no_data_after_s` (600 s) between two contacts with the robot, the
  same line at which the screen shows NO DATA. Its cause does not matter: the Mac asleep,
  Whisker down, the network down.
- **When contact comes back after a gap,** the tracker forgets what only watching can tell
  (when the status began, the status before it, the visit under way) and keeps the last
  visit. The history is read at once and fills in the last visit and, if a visit is under
  way, when it began and whether he has left.
- **A clean cycle that no watched visit explains** (the robot's cycle count rose and the last
  visit has not moved since the count last rose) also sends the feeder to the history. This
  does not depend on noticing the gap. A manual clean costs one request and changes nothing.
- **Every history read may move the last visit later,** never earlier, so the six-hourly read
  is a net under the other two.
- These reads are never closer together than 120 s. Each is one line in
  `logs/feeder-console.log`.

**Why the history can be trusted for this.** All 27 "Cat Detected" entries in it from
2026-11-09 to 11-18 are real visits. The short false detections the live status shows (a
hand at the box, a manual clean) are not in it. One real visit, 14:17 on 11-18, has no weight
entry, so a weight is not required.

**Rejected.** Reading the history on every poll: twelve times the requests, against an
unofficial API (D-0005). Keeping the Mac awake with its lid closed: it takes a system power
setting, and the Pi ends the problem anyway. Showing the last visit as unknown until the
history has been read: the wait is about a second.

**Not known.** Whether the history ever lists a detection that was not a visit; it now moves
the last visit whenever it is read, not only at start. How soon after a detection the history
has it. What the first minutes after a real gap look like on the clock: only the tests have
seen one. The studio's Replay does not apply the gap rule; its last-visit time was already
taken from the history.

## D-0026 — Shared as code plus a build-your-own prompt
_→ See D-0028_

**2026-11-19.** People commenting on Kyle's posts want one of their own. The project will be
shared as a public GitHub repository (Kyle creates it and pushes), and a Reddit post links to
it; the instructions live in the repository, where they can be corrected, not in the post.

- **Two ways in.** Same hardware (a Litter-Robot 4 and a TC002): clone and run, following the
  README, which is now written for them. Anything else: `BUILD-YOUR-OWN.md`, a prompt for
  Claude Code or Codex. The agent clones this repository as `reference/`, reads `states.md`,
  this file and the reference notes, plans for the person's hardware and waits for an OK. The
  prompt names the rules that cost the most to learn (D-0004, D-0017, D-0018, D-0025, one
  Whisker connection, checking names against the source) instead of restating the docs.
- **MIT licence**, the same as pylitterbot. The copyright line says "the ProjectArthur
  contributors" until Kyle decides whether his name goes public with it.
- **Running it beyond Kyle's Mac.** A systemd unit for the Pi or any Linux machine,
  `scripts/arthur-feeder.service`, reads the clock's address from `.env`. The Mac script now
  looks for uv where its installers put it and creates `logs/` on a fresh clone; it keeps
  Kyle's clock address on its `CLOCK=` line, which others change.
- **CLAUDE.md says** that in someone else's copy, STATUS.md describes Kyle's setup, not theirs.
- **Checked before sharing:** no `.env`, token or log was ever committed, and the real-visit
  fixture holds no serials or account ids. Kyle's name is the commit author and is throughout
  the docs; the Boston coordinates are downtown.

**Why.** The code alone serves only people with exactly this hardware, and many who asked
will have something else, a TC001 or a Home Assistant dashboard. A prompt alone loses what was
learned against the real robot: an agent that does not know D-0017 and D-0025 builds something
that looks right and reads visits wrong. The docs are the distilled part, so the prompt points
at them.

**Rejected.** Instructions in the Reddit post itself: they cannot be updated and cannot hold
code. A prompt instead of the code. Bringing back the 32x8 layouts for TC001 owners: D-0010
stands, and the prompt points them at commit 7d4393e.

**Not known.** Whether a fresh agent following the prompt reaches a working display; none has
tried. Whether the systemd unit runs as written; there is no Pi yet. Whether the feeder can
share a Whisker account with Home Assistant's Litter-Robot integration.

## D-0027 — The public history carries GitHub's no-reply address, not Kyle's Gmail

**2026-11-19.** Before the repo went public, every commit's author and committer email was
rewritten from Kyle's personal address to GitHub's no-reply address, and this repo's git config
now commits with it. Rewriting changed every commit ID, so the five IDs the docs cite were
updated in place to name the same commits (D-0010, D-0016, D-0026, STATUS, the AWTRIX NG
notes and BUILD-YOUR-OWN.md); that is the one edit made to older entries here. The rewritten history went to a fresh
GitHub repo; the old one, which still has the old commits, was renamed
`mcgaheyk/ProjectArthur-old` and kept private as a backup until Kyle deletes it.

**Why.** Public commits show their email to anyone, and scrapers collect them. Force-pushing
over the old repo would have left the old commits reachable by ID on GitHub, and old versions
of the docs name some of those IDs.

**Rejected.** Publishing with the Gmail. A fresh one-commit copy: it loses the history the
docs refer to.

## D-0028 — Setting up is an interview by an agent, from one short prompt

**2026-11-19.** Supersedes D-0026's prompt with blanks to fill in. Kyle asked for setup to be
as painless as possible: no editing the prompt, and the agent asks about the cat's colours and
the place, so the time and weather are right.

- **The copy-paste prompt is four lines** (`BUILD-YOUR-OWN.md`): clone the repo, read
  `docs/agent-setup.md`, follow it, and say so if the download fails. The instructions live in
  the repo, so they can be corrected without anyone copying a new prompt.
- **The agent interviews first,** one question at a time with choices: robot, display,
  computer, the cat (a photo is welcome), the town and °F or °C, and Home Assistant. The town
  goes through Open-Meteo's geocoding (no key), which gives the coordinates and the time zone;
  the agent confirms the match and rounds to two decimals.
- **Two paths.** A Litter-Robot 4 and a TC002 (or no display yet): the agent sets up this
  code: config, the studio, the clock's firmware, the push test, the first run, keeping it
  running. Anything else: it builds one for their hardware, with D-0026's reading list and
  rules.
- **What stays with the person:** cables and installer clicks, reading the clock's address
  from the router, and typing the Whisker login into `.env`, which the agent opens for them
  and never reads. No password in the chat; a town, never an address.
- **Celsius:** `[weather] units = "celsius"`. Fahrenheit stays the default, and the key is a
  comment in Kyle's config so the running feeder is untouched.
- **`AGENTS.md`** points Codex at the guide and at CLAUDE.md.

**Why.** Blanks ask people to answer in the agent's terms before anything has happened; an
interview asks in theirs, and can take a photo of the cat. People with the same hardware get
this code set up for them instead of a rebuild, which is faster and safer.

**Rejected.** Keeping the blanks. Asking for a street address or exact location: a town is
enough for the weather. Having people paste their login to the agent.

**Not known.** Whether an agent follows the guide end to end; none has tried it. Only the
config edits it makes were tried, on a copy (Celsius, a dark-grey cat, another time zone).
Windows has never run the feeder; the Pi unit has never run.

## D-0029 — The clock is to run itself; the Pi and a cloud relay are not the plan

**2026-11-20.** Supersedes D-0006 as the target. Kyle chose to build toward the TC002 running
the display with no Pi and no Mac: a MicroPython program on the clock, started by AWTRIX NG's
autostart slot, that talks to Whisker, draws with the same Python the studio uses, and pushes
frames to AWTRIX over the clock's own HTTP API. The Mac's feeder stays as it is until the
on-clock program has passed its trials; nothing is cut over by this entry.

- **One codebase.** `arthur/`'s model, mapping, schedule, tracker and drawing code is made to
  run on both CPython and MicroPython. The test suite compares every golden frame under both,
  because MicroPython differs in ways that change pixels without raising an error.
- **A Whisker connection of our own** for MicroPython. pylitterbot cannot run there: its AWS
  libraries alone are 27 MB and the clock has 13 MiB of memory free and 8 MiB of storage.
- **Changes go from the Mac to the clock over Wi-Fi.** Kyle's condition for the whole route:
  "ideally I'd be able to do it from my computer and it show up on the device without too
  much work."
- D-0003 and D-0004 stand. We still draw every pixel, AWTRIX still only shows a bitmap, and
  it still marks the frame stale by itself if our program stops.

**Why.** No monthly cost, the Whisker login stays in the house, nothing has to be kept
running anywhere else, and frames never cross the internet. The test on the real clock that
day removed the doubts about fit and speed
(`docs/research/2026-11-20-no-pi-two-tests.md`): identical pixels, 16 ms a frame for the home
screen, 11 ms for the bird scene with its plan built once, 1.5 MiB of memory, TLS working, and
a cold boot with nothing connected.

**Rejected.**
- A cloud relay (the feeder on Railway, frames over MQTT). Its first test also passed: Whisker
  accepted the login from a data centre. It would have been quicker to build. It costs about
  $5 a month, keeps the login on Railway, and its live events were never seen.
- A rewrite in a compiled language, as another AI system proposed
  (`docs/research/ProjectArthur-TC002-Technical-Plan.md`). That report was right that a
  program can run on the clock, and its test standards were adopted. But it means two
  codebases while the screens are still changing daily, so every change would be made twice.
- The Pi (D-0006). It would work with today's code and remains the fallback.

**Risks accepted.** We own the Whisker connection: when Whisker changes its API, pylitterbot's
maintainer no longer fixes it for us. The autostart slot is a feature of one maintainer's
firmware, new in 1.2.2, and holding the clock's reset key for five seconds erases our program
with everything else. The clock is 25 to 30 times slower than the Mac.

**Not known.** Whether a Whisker connection in MicroPython holds up over weeks. Memory with
everything running at once. Whether the frame that builds a scene's plan (87 ms) shows.

## D-0030 — The clock keeps the Whisker login

**2026-11-20.** The on-clock program (D-0029) stores Kyle's Whisker email and password on the
clock, under `/data/awtrix-ng/state/`, which only root over the USB cable can read (AWTRIX's
web UI and HTTP API cannot). The installer on the Mac copies them from `.env` itself; they
never pass through a chat or a log.

**Why.** The point of D-0029 is that the Mac can stay shut. The client renews its one-hour
tokens from a long-lived refresh token, but how long that token lasts is not known (Cognito's
default is 30 days) and renewal does not replace it. With the login on the clock, the clock
signs in again by itself when the token dies (an estimated 11 to 14 seconds of arithmetic
there, done in slices).

**Rejected.** Keeping only the token on the clock: the password would never leave the Mac, but
the clock would show NO DATA until the Mac came back, at a moment nobody can predict.

**Risk accepted.** Anyone holding the clock and a USB cable can read the login. Holding the
clock's reset key for five seconds erases it with everything else on `/data`.

**Not known.** Whether our own password login is accepted by the real service; it has only
run against a fake.


## D-0031 — The clock stays on its own program; what is slow there is made cheaper in the same Python

**2026-11-21 and 22.** Two hours after the first install (D-0029), asked whether to leave the
clock running on its own program or go back to the Mac's feeder, Kyle said to leave it. The
Mac's feeder stays stopped as the way back. This is the trial going on, not the cutover.

The clock then showed what holds its loop (figures in `docs/reference/onclock.md`, "What
holds the loop on the clock"), and two things were chosen:

- **The scenes' random numbers keep their exact sequences and are computed in 16-bit
  halves** (`arthur/mtrandom.py`), the same code on the Mac and the clock. The clock's
  MicroPython is 32-bit; with 32-bit words every value was a heap-allocated big integer, and
  a scene's plan took 0.4 to 1.0 s. In halves: 0.15 s for the bird and the stretch, 0.46 s
  for the rat.
- **The weather waits out a visit.** Its TLS handshake holds the whole program for 2.2 s. It
  starts only on the idle home page (or NO DATA), and waits up to 20 minutes for one.

**Why.** D-0029 rests on one body of Python that the studio, the tests and the clock all
run, checked frame for frame on the Mac. A fix that only the clock runs gives that up.

**Rejected.**
- MicroPython's native and viper code. The clock's build has the emitters and they would
  make the seeding far faster still (not measured); the Mac's arm64 build does not have
  them, so the Mac's 40,000-frame comparison could not run the code the clock would.
- The old whole-word arithmetic kept for 64-bit builds, where it is a little faster: the
  same objection the other way round.
- Building a scene's plan in pieces over several beats. It would restructure three scenes
  to spread a stall that falls on a still home page, and most of the stall was the seeding.
- A cheaper generator. Every round of every scene would play differently, and the goldens
  with them, for no gain a viewer could see.
- For the weather: a second process on the clock's other core (one more program to start,
  watch and update, for a decoration), or a faster elliptic-curve build of mbedTLS (a new
  install over USB, untried). Either can come later.

**Accepted.** On the Mac's 64-bit MicroPython seeding is a little slower than before
(0.32 ms against 0.25 to 0.28). The home page stands still for 2.2 s every 15 minutes while
the weather is fetched. Whisker's five-minute poll still holds the loop 0.2 to 0.3 s
wherever it falls, a scene included.
