# STATUS.md — project memory

_Last updated: 2026-11-20, early afternoon._ Current state only. History belongs in git and DECISIONS.md.

## Where things stand
- **Hardware:** Kyle has a **Ulanzi TC002** (52x16) and a **Raspberry Pi 5** (D-0010). **The
  clock runs AWTRIX NG 1.2.3 since 2026-11-17** and is on the home Wi-Fi as `awtrixng-77a767`
  (192.168.86.41 that day; the address can change). Its built-in Time and Status apps are out
  of the rotation. The Pi has not arrived; nothing is deployed to it.
- **One size.** Everything is 52x16. The 32x8 layouts and the second fake clock were removed
  (D-0010); commit 7d4393e is the last one that has them.
- **Firmware target:** AWTRIX NG pinned to 1.2.3 (D-0002). It is the latest release and ships a
  TC002 build. The feeder draws every pixel and pushes a bitmap (D-0003).
- **The clock is live.** The feeder (`python -m arthur.feeder`) has been **running on Kyle's
  Mac since 18:12 on 2026-11-17** (restarted at 18:33 for the litter bar, at 09:10 on 2026-11-18 after the push bench, at 13:47 that day after Kyle watched in use on the clock, at 18:04 from Terminal after a 42-minute outage, D-0024, at 08:56 on 2026-11-19 for the gap rule, D-0025, at 12:46 on 2026-11-20 after a 58-minute stop for the no-Pi tests, D-0029, at 14:00 that day after a five-minute stop for the first live check of our own Whisker client, and at 14:08 for the shared code, D-0029), detached under `caffeinate`, against the real robot and
  the real clock (D-0018). It logged in, read the robot and showed idle; the clock's screen
  read-back matched our render of the robot's state on all 832 pixels. **No real visit has
  been watched on the clock yet.** It stops on a Mac restart and the clock shows the stale
  border while the Mac sleeps; closing the MacBook's lid puts it to sleep whatever
  `caffeinate` says. **A visit missed while it was not watching is now counted (D-0025):**
  on 2026-11-19 the clock said "15H AGO" when the truth was 10, because the 22:39 visit fell
  in an hour the Mac slept. After more than ten minutes without contact, or a clean cycle
  with no visit seen, the feeder reads the robot's history again by itself; each time is a
  line in the console log ("Not watching from … to …", "Clean cycle with no visit seen",
  "Last visit moved …"). **The first real gap was met on 2026-11-19:** Kyle closed the lid
  at 23:49 and the Mac woke at 23:57. NO DATA showed from 23:58 until the first poll at
  00:02, which logged "Not watching from 23:48 to 00:02" and read the history; the clock's
  read-back then showed the sleeping cat and "4H AGO", right for the 19:42 visit, and no
  push to the clock failed. No visit fell in that gap, so the history has not yet been seen
  moving the last-visit time after one. After the Mac wakes the first poll can be up to
  five minutes away (it was four that night), and NO DATA shows until then. **The clock survives a power loss:** on 2026-11-18 its battery
  ran down at 21:17 and Kyle charged it at about 23:26; it rejoined Wi-Fi, the running feeder
  drew on it again at 23:27 with no restart, and four minutes later only our app was in the
  rotation and double-size drawing was off. Keep it on USB power. Its messages go to `logs/feeder-console.log`. Stop it with
  `pkill -f arthur.feeder`; start it with `open -a Terminal scripts/start_feeder.command`,
  **never from a Claude session's shell**: the 13:47 copy lost the clock (not the robot) at
  17:22, when the session that started it closed, and the clock showed the stale border until
  18:04 (D-0024). Never run a second copy, or `scripts/spike_log.py`, on the same account.
- **Built and tested (3,766 tests):**
  - Feeder: `arthur/feeder.py` (the loop), `arthur/pusher.py` (when to push),
    `arthur/sources/whisker.py` (the one Whisker connection). On a login or API failure it
    shows NO DATA and retries; it reloads `config/display.toml` when the file changes; it
    writes `logs/spike-*.jsonl` in the spike's format, so the studio's Robot log source
    follows it.
  - Visit rules (D-0017): in use lasts until the weight reading arrives, the countdown runs
    from that moment, cleaning is a 130-second estimate. `arthur/sources/tracker.py` keeps
    these observed facts for both the live source and the log reader. Replayed over the log's
    seven real visits, the countdown ended within 4 s of the real clean start every time.
    What the feeder did not watch comes from the robot's activity history: at start, after
    a gap, after a clean with no visit seen, and every six hours (D-0018, D-0025). The
    studio's Replay does not apply the gap rule.
  - Pipeline: `arthur/model.py`, `arthur/mapping.py`, `arthur/render/`, with variants for
    ready, in_use, waiting and cleaning.
  - Screens: second design pass done on 2026-11-16 to Kyle's direction (D-0009): orange
    Arthur, warm palette, poop emoji, joke captions in `config/display.toml`. **Kyle has not
    reacted to this pass yet.** It is a starting point for iteration, not a final design.
    Since then, to his direction (D-0011): the level bars are coloured by state (litter by the
    robot's own word, drawer orange then red), and drawer-full is a poop emoji, "FULL",
    "EMPTY NOW" and a flashing red ring.
    Then pass 4 (D-0012): one layout each for in use, waiting and cleaning; in use takes
    turns between the poop emoji with a cycling joke and "IN USE", and a smug Arthur with the
    timer; cleaning and just-cleaned are soapy blue with a moving border on cleaning; idle has
    more room and a longer tail; badges are gone. Idle Arthur then got a one-minute script of
    gestures (D-0013, D-0014): blinks, looks, an ear twitch, a squint, a small tail wag in
    the gap beside him, and a yawn every five minutes. **Kyle has not reacted to pass 4 or the idle script
    yet.**
    Just cleaned is now five seconds of "FRESHLY CLEANED" (D-0015).
    Interrupted is now a little story that repeats every 21 s (D-0016), built over several
    rounds with Kyle on 2026-11-16 and 17: Arthur strolls up to the globe, the view cuts to
    inside the box where he looks in at us, "INTERRUPTION", his face again, a second
    phrase, a short rest on the empty globe. **Kyle chose it in the studio; it has not
    been seen on LEDs.**
  - Transport: `arthur/transports/awtrix_ng.py`. Before pushing, it turns off the TC002's
    double-size drawing (`enlargeApps`); after pushing, it takes every other app out of the
    clock's rotation (a new install rotates Time and Status with ours). Both are rechecked
    every five minutes. Kyle said idle looks good on the LEDs, and watched in use on the
    clock for about 90 s on 2026-11-18 (`scripts/push_test.py in_use`); he has not said what
    he thought of it. No other screen has been seen on hardware.
  - Push test: `scripts/push_test.py --clock http://HOST [NAME] [--seconds N] [--readback]`
    shows any one screen on a real clock. Stop the feeder first, or the two fight over the
    screen.
  - Simulator: `arthur/sim/`. One fake TC002 that starts with `enlargeApps` on, as a real
    clock does, and a studio page (live clock, all-states grid, scenario playback,
    kill-the-feeder switch) with live reload of screens and config. Its **Robot log** source
    drives the clock from the spike's log: Follow shows the real robot live, Replay plays a
    recorded visit at 1x, 10x or 60x, and both show the robot's raw fields beside the mapped
    state.
  - Log source: `arthur/sources/logfile.py` turns the spike's log lines into `RobotSnapshot`s
    (read only; it never talks to Whisker).
  - Spike: `scripts/spike_log.py` ran from 2026-11-15 to 18:11 on 2026-11-17 and logged ten
    cycles, seven of them real visits. **Stopped: the feeder replaced it** and writes the same
    log.
  - PETLIBRO spike: `scripts/spike_petlibro.py` (logic in `arthur/petlibro_spike.py`, 39
    tests). Logs what the Granary and a fountain report through PETLIBRO's cloud, read only.
    **Running on Kyle's Mac since 10:33 on 2026-11-16**, detached, against the real Granary
    through a second PETLIBRO account it is shared to. So the login, a shared account and the
    Granary's fields are confirmed; `docs/reference/petlibro.md` says which. It writes
    `logs/petlibro-*.jsonl` and `logs/petlibro-console.log`. It stops on a Mac restart and
    pauses during sleep; stop it with `pkill -f spike_petlibro`. Never start a second copy on
    the same account.
- **The idle show is live (D-0019 to D-0023).** Since 13:53 on 2026-11-18 idle rotates in a
  three-minute round: home 45 s, the weather and date page 54 s, home 45 s, a 36-second
  scene (rat, bird or stretch), with Arthur asleep at night. Kyle set the split
  (50 / 30 / 20) and said to go live the same day. `[idle] page_pct = 30` and
  `scene_pct = 20` in `config/display.toml`; the running feeder picked them up by itself.
  **The first full round was read back from the clock's own screen:** the home page, the
  weather page (sun, 63°, `NOV` over `18`) from 0:45, home again, then the rat scene from
  2:24, ending with him seated at his spot. The feeder logged no error. **The ten-minute
  run at 8 a second and the test patterns were never run on the clock** (D-0023), and
  Kyle has not yet said how any of it looks on the LEDs. What it is made of:
  - The loop ticks 8 times a second on the wall clock (`arthur/timebase.py`). Measured on the
    clock: 8 frames a second is comfortable (slowest push 64 ms of a 125 ms budget); figures
    in `docs/reference/awtrix-ng.md`.
  - The schedule (`arthur/schedule.py`), decided in mapping and carried as a cue.
  - The engine (`arthur/render/idle_show.py`): a page dissolves in and out; a scene opens
    with the bars wiping away beside seated Arthur and closes with them wiping back.
  - The weather and date page (`arthur/render/pages.py`). **Kyle chose the big-temperature
    layout, `weather_big`** (D-0021): the picture, the temperature at double size, the date
    stacked as `NOV` over `18`, no high and low and no weekday. The other layout, `weather`,
    is kept unplayed until he has seen the page on LEDs.
  - The weather source (`arthur/sources/weather.py`): Open-Meteo for Boston every 15 minutes,
    confirmed with one live request (`docs/reference/open-meteo.md`).
  - The rat scene with both endings (`arthur/render/scenes/rat.py`): the rat creeps toward
    seated Arthur, bolts, and scurries back across the empty stage with him galloping after
    it; one round in four the tables are turned.
  - The bird scene with both endings (`arthur/render/scenes/bird.py`): he watches it, chases
    it, crouches well short of it, and leaps across at it seen from the side (D-0022): his
    forelegs fly open, clap shut on nothing, and he hangs there; a feather lands on his
    head; one round in four the bird comes back and perches there.
  - The stretch scene (`arthur/render/scenes/stretch.py`, D-0022): he yawns, strolls to the
    middle, goes down into the cat stretch (rump up, forelegs flat out in front) and holds
    it about 3 s with his eyes shut, then stretches each hind leg out behind and strolls
    home. One round in four he kneads the floor; on some rounds he sits in the middle
    first. With three scenes each plays about six or seven times an hour.
  - Night sleep: in the quiet hours (22:00 to 07:00 Boston time) the home page shows him
    curled up, breathing every 12 s with Zs, 10 screen changes a minute instead of 27.
  - The studio plays all of it. Its Idle show switch follows the config (so it is on) and
    can be unticked to keep idle on the home page alone in the studio; it never writes the
    config. Also sample weather, and the `idle_hour` and
    `visit_mid_scene` scenarios, and `night_falls` and `morning` for the sleep transitions.
    Fixtures `idle_page`, `idle_page_detail`, `idle_rat`, `idle_rat_turned`, `idle_bird`,
    `idle_bird_perched`, `idle_stretch`, `idle_stretch_knead` and `ready_asleep` pin one
    screen each. In `idle_hour` the first bird scene is at 2:24, the first stretch at 5:24
    and the first rat at 8:24; the knead ending is at 29:24 and the tables-turned ending at
    47:24. The perched ending does not come up in that hour; use its tile.
  **Kyle opened the studio on 2026-11-18, chose the weather layout, and asked for the
  side-on leap and the stretch scene (D-0022). He has seen stills of both, not the built
  animations. He has not yet reacted to the rat scene, the sleeping cat or the
  transitions, and has seen nothing on LEDs. Every screen here is a first cut.**
- **The running feeder has this code.** It was restarted at 13:47 on 2026-11-18, the first
  run of the 8-a-second loop and the weather source against the real robot and clock, and
  again at 18:04 from Terminal with the same code (D-0024); the clock's read-back then showed
  no stale border and a changing picture. It was restarted at 08:56 on 2026-11-19 with the
  gap rule (D-0025): it read the 22:39 visit from the history at start and the clock's
  read-back showed "10H AGO". The 13:47 copy
  logged in, showed the home page (the clock's read-back showed Arthur, the bars and the
  last-visit line) and logged no error in its first minute. It has played the idle show
  since 13:53. It has not been watched through a real visit, so a visit cutting into a
  page or scene has only been seen in tests and the studio. It was restarted at 14:08 on
  2026-11-20 on the shared code (D-0029): its first four minutes, a full idle round, logged no
  error or warning, and the clock's read-back showed no stale border.
- **The clock is to run itself (D-0029), chosen 2026-11-20; not built.** The goal is no Pi
  and no Mac: a MicroPython program on the TC002, started by AWTRIX NG's autostart slot,
  that talks to Whisker, draws with the same Python the studio uses and pushes frames to
  AWTRIX on the clock. Two routes were tested that day
  (`docs/research/2026-11-20-no-pi-two-tests.md`):
  - **On the clock (chosen):** a slice of the real drawing code ran on Kyle's clock under
    MicroPython with pixels identical to the Mac's: 16 ms a frame for the home screen, 11 ms
    for the bird scene once its plan is built once and kept (93 ms as written), 1.5 MiB of
    memory, ten minutes at 8 a second with 1 beat missed of 4,800, TLS working, and a cold
    boot with the cable out. **Only a slice has run there.** The rest of the code has not
    been ported, and nothing of ours is on the clock now.
  - **Our own Whisker client is in `main`** (`arthur/wire/`, read-only, no outside libraries,
    the same code on CPython and MicroPython; protocol in `docs/reference/whisker-wire.md`).
    Nothing uses it yet. It is tested against a fake Whisker, and on 2026-11-20 it made its
    first real contact from the Mac (`scripts/wire_check.py`): Whisker accepted our
    User-Agent, the token renewed, the robot and the history read correctly, and the live
    socket held for five minutes. **No live update has been seen through it** (the robot was
    idle), its password login has never been tried for real, and it has not run on the clock.
  - **The shared code runs on both Pythons, in `main`.** `arthur/`'s model, config, schedule,
    mapping, tracker and all the drawing code run unchanged under MicroPython 1.29
    (`arthur/compat.py`, stand-ins in `mpy/lib/`; the rule is in CLAUDE.md and the
    differences in `docs/reference/micropython.md`). `tests/test_micropython.py` draws over
    40,000 frames under MicroPython and compares each with CPython's; it needs `micropython`
    on PATH (Homebrew) and says so when it did not run. Nothing changed on CPython: every
    golden is identical, and 46,656 frames drawn by the old and new code matched. The
    scenes still use CPython's exact random sequences (`arthur/mtrandom.py` reproduces them
    on MicroPython). Each scene's plan is now built once per play. The art folder is
    `arthur/render/icon_art/`. **Measured on the Mac only:** the whole drawing job needs a
    1.1 MB heap there, and the rat scene's plan takes 5.8 ms to build under MicroPython,
    which at the clock's 25 to 30 times would be 150 to 170 ms, over the 125 ms beat.
  - **Cloud relay (not chosen):** the feeder's image (`deploy/feeder/Dockerfile`) logged in
    to Whisker from Railway and read the robot for 43 minutes with no error. No visit
    happened, so live events from a data centre are unproven. **A Railway project
    `ProjectArthur` still exists, stopped, holding Kyle's sealed Whisker login.**
  - The test code is on the local branch `spike/micropython-slice` (kit:
    `spike/mpy/clock/kit.py`; measurements: `spike/mpy/clock/results/`), not in `main` and
    not pushed. Its worktrees are under `.claude/worktrees/`.
  - To reach the clock over USB, plug the cable into the clock itself, not its charging
    base. What was learned about the clock's insides is in `docs/reference/awtrix-ng.md`,
    "Running our own program on the TC002".
- **Public since 2026-11-19 (D-0026)** at <https://github.com/mcgaheyk/ProjectArthur>. People
  asked Kyle how to make their own, so the repo has a README written for them (hardware,
  installing NG, setup, keeping it running, making it their cat), an MIT `LICENSE`, a systemd
  unit, and a note in CLAUDE.md for other people's copies. **Setting up by AI agent
  (D-0028):** `BUILD-YOUR-OWN.md` is a four-line prompt for Claude Code or Codex; the agent
  clones the repo and follows `docs/agent-setup.md`, which has it ask about the robot, display,
  computer, cat (a photo will do) and town, then either set up this code for them (Litter-Robot
  4 and TC002) or build one for their hardware. `AGENTS.md` points Codex at it. The weather
  page can show Celsius (`[weather] units`). **Checked before it went public:** no `.env`,
  token or log was ever committed; `tests/data/real-cycles.jsonl` has no serials or account
  ids. Kyle's name is on the commits and about 100 lines of the docs, but **his Gmail is not**:
  every commit's email was rewritten to GitHub's no-reply address (all commit IDs changed; the
  docs cite the new ones) and pushed to a fresh repo. The pre-rewrite copy, Gmail and all, is
  kept private as `mcgaheyk/ProjectArthur-old`. **No agent has followed the setup guide yet;
  only its config edits were tried, on a copy.**
- **Not built:** the on-clock program (D-0029; see Next), night dimming, food and water. The
  Pi deployment is now only a fallback (the unit `scripts/arthur-feeder.service` is written
  but has never run). See Later.
## Next
**Sharing (D-0026), Kyle's steps before posting:**
- Delete `mcgaheyk/ProjectArthur-old` on GitHub once sure nothing is missing from the new
  repo (its Settings page, at the bottom). It is private and only a backup.
- `LICENSE` says "the ProjectArthur contributors"; Kyle may put his name there.
- Record a short clip of the clock for the README's top and the post; better after a real
  visit has been watched on the clock (item 1 below).
- With Claude, optional: a fresh agent follows the prompt in `BUILD-YOUR-OWN.md` once, playing
  a new owner, through path A up to the studio (no clock or login), and once through path B's
  plan, to see where it stumbles.


**In hand, with Claude: the idle show (D-0019), live and waiting for Kyle's reactions.**
- **Kyle, at the clock over the next days:** say what looks wrong and what wears. The studio
  (`idle_hour`, and the tiles in All states) shows any one part on demand.
  React to the new side-on leap and the stretch scene, the rat scene, the
  second endings, the sleeping cat, the dissolve and the wipe, the rat's grey and the
  bird's blue, and whether the bird's wings (beating four times a second for about 20 s)
  are too busy. Known weak spots in the new work: his run before the crouch is only four
  frames; the second hind-leg stretch and the kneading are subtle. (The bird hovers at
  the far right edge before the leap; Kyle chose to leave that as it is.)
- **Watch the clock's health.** It now takes about 4,500 pushes an hour, up from about
  1,600, in bursts of 8 a second for 36 s of every three minutes. Trouble would look like
  a frozen picture, the stale border during a scene, or the clock restarting; push errors
  go to `logs/feeder-console.log`. **To switch the show off, set both shares to 0 in
  `config/display.toml`**; the feeder reloads the file by itself, no restart.
- **Still worth doing once, feeder stopped:** the bench's ten-minute run
  (`scripts/push_bench.py --clock http://ADDRESS sustained`), for numbers to go with what
  the eye sees.
- **When Kyle has seen the weather page on the LEDs:** remove the unplayed layout
  (`weather`, tile `idle_page_detail`) (D-0021).

1. Kyle: watch Arthur's next visits on the clock. In use should last until he leaves, then a
   countdown from about 8:00 that reaches zero as the clean cycle starts, then about two
   minutes of cleaning. Say what looks wrong; `logs/spike-*.jsonl` has the robot's side of
   each visit and the studio's Replay shows what the clock should have drawn.
2. Kyle: react to the screens, now on real LEDs: which jokes land, which art does not read,
   colours and dim shades. Any one screen: `scripts/push_test.py` (stop the feeder first).
3. With Claude, when they happen or can be staged: the cases the feeder has never met. The
   stale border (stop the feeder for two minutes), an interruption, drawer full, and the
   robot offline. The remaining
   guesses about the clock are at the end of `docs/reference/awtrix-ng.md`.
4. **The on-clock build (D-0029), with Claude.** The Mac's feeder stays live until the last
   step passes. In order:
   - the Whisker client (`arthur/wire/`, built) seen through one real visit: whether each
     live update carries every field, on which the visit rule (D-0017) depends; and one real
     password login;
   - each scene's plan built ahead of its first frame, in pieces, during the frames before
     it: the rat's is an estimated 150 to 170 ms on the clock in one go;
   - the clock's program and its installer: the loop, a launcher that falls back to the last
     good version, sending changes from the Mac over Wi-Fi, and a way home for the visit log;
   - trials on the clock: a real visit, a token renewal, a Wi-Fi outage, a cold boot, an
     AWTRIX restart and 48 hours, then the way back to the Mac rehearsed once.
   Kyle: delete the Railway project `ProjectArthur` (it holds the Whisker login) unless the
   cloud relay is wanted as a fallback.
5. Kyle: leave the PETLIBRO spike running until the Granary has run low and been refilled at
   least once. That is the one thing it has not seen: what `surplusGrain` and
   `warehouseSurplusGrain` read when food is low, and how soon. Then read the log back into
   `docs/reference/petlibro.md`.
6. Kyle: buy a fountain, a plug-in model, PLWF105 or PLWF106 (see the research note), and
   share it to the same second account. The running spike picks it up within the hour.

## Later
- Pi 5 deployment, only if the on-clock build (D-0029) is abandoned: Raspberry Pi OS Lite (64-bit), the systemd unit in
  `scripts/arthur-feeder.service` (written, never run; the feeder already reconnects with
  backoff and stops cleanly on SIGTERM), and retention for
  `logs/`, which grow by a file a day and are never deleted. The memory check (pylitterbot pulls in boto3/botocore) only matters if it is the
  1 GB model.
- Night dimming. The TC002 has no light sensor and NG has no night schedule, so the feeder has
  to set `brightness` on a timer.
- Optional: build `awtrix-linux` (the same program the TC002 runs) on the Mac as a conformance
  check for the transport.
- On-device watchdog script for a louder stale screen. Review NG's Berry agent skill first.
- Alert sounds (the TC002 has a speaker) and acknowledgement with its knob or buttons.
- Health nudges: no visit in N hours, unusually many visits.
- Food and water on the display: a PETLIBRO source (`arthur/sources/petlibro.py`), mapping and
  screens for the Granary's low-food flag and a fountain's water level. The Granary side can
  be built from what is confirmed now; the low-food reading and the fountain wait on Next,
  items 6 and 7. Research:
  `docs/research/2026-11-16-petlibro-food-and-water.md`.
- Experiment: the no-cloud ESPHome firmware for the robot itself.

## Known rough edges in the screens
- **In use:** the joke page and the poop's face loop share a rhythm (pages 6 s + 4 s, face
  15 s), so the same blinks land on the same pages every third round. Arthur's smirk and the
  rising stink lines have only been judged on a monitor.
- **Interrupted:** words are on screen for 5 seconds in every 21, so a quick glance is
  more likely to catch a picture than a word, and an interruption shorter than ten seconds
  ends before "INTERRUPTION" shows. No real interruption is in the spike's logs yet, so
  how long they last is unknown. The walk repeats every episode although he only walks up
  once.
- **Jokes** are capped at about seven letters. "POOPIN?" and "PEEIN?" carry question marks
  because we cannot tell which.
- **Idle gestures:** the tail wag is a small lift (the tip moves two rows) and the ear twitch
  is faint; both may not show from across the room. Idle changes about 27 times a minute, each
  one a push to the clock.
- **Idle laser notice** can only say "LASER": "WIPE LASER" and "LASER DIRTY" are too wide for
  the line. It does not say what to do.
- **Cleaning:** the countdown is an estimate (130 s; real cycles took 123 to 134 s), so it can
  reach zero up to 7 s early or be cut off up to 4 s early.
  The 6-row digits and the pale bubbles on the blue border have only been judged on a monitor.
- On-battery and connection-down are no longer shown anywhere (badges were removed).
- **Drawer full:** the narrow M and W in "EMPTY NOW" and the flashing ring have only been
  judged on a monitor. The ring means a push to the clock every second for as long as the
  drawer stays full. The poop's face runs on its own 15-second loop (blink, glance left and
  right, one wink), deliberately out of step with the ring; Kyle did not want an opening
  mouth or anything in sync with the flashing. The glances move one pixel and may not show
  from across the room.
- **The litter bar reaches its 2-pixel stub at 60 %**, a guess at "basically empty": the
  robot said LOW at 72 % and no EMPTY reading has been seen. `litter_empty_pct` in the config.
- **The drawer bar turns red at 80 %**, which is a guess. The robot's percentage is rough and
  some owners report it jumping from about 70 % to full.
- Idle shows the last-visit time but not visits today.
- Brown and the dim shades have only been judged on a monitor, not on LEDs.
- The studio's live reload does not pick up a new key in `config/display.toml`: it keeps
  the config code it started with, logs "unknown key" and stays on the last good version.
  Restart the studio after adding a key.
- In the studio, All states tiles and fixture mode on the live clock play a 30-second loop and
  then jump back, so a timer counts 30 seconds and restarts. Scenario, Manual and Robot log
  modes run with time moving normally.

## Known unknowns
- How fast Whisker pushes "cat detected" after Arthur steps in. The push trails the robot's
  own timestamp by up to 5 s; the robot's own delay is not known.
- What the feeder does in the cases it has never met live: an interruption, Whisker's
  connection dropping, a rejected login (D-0018), a visit that falls in a gap in watching
  (D-0025; a gap with no visit in it was met on 2026-11-19). A visit with
  no weight push has now happened, at 14:17 on 2026-11-18: the log shows waiting began 120 s
  after detection (the fallback) and the history had no weight for it either. Nobody was
  watching the clock.
- Whether the robot's history ever lists a detection that was not a visit. It now moves the
  last-visit time whenever it is read (D-0025); all 27 entries so far are real visits.
- How colours and dim shades read on the TC002's LEDs. NG is installed; only idle has been looked at.
- AWTRIX NG on the TC002: whether re-pushing an app clears the stale mark (inferred, not
  confirmed); what an empty rotation shows after a clock reboot; exactly which pixels the three
  indicators use; whether the clock itself keeps Time and Status out of the rotation after a
  reboot (open reports #141 and #142 say it may not). After the 2026-11-18 power loss both were
  out, but the feeder's overdue check may have put them out again; it does not log that.
- Which Raspberry Pi 5 it is (RAM size).
- Whether the feeder can share a Whisker account with Home Assistant's Litter-Robot
  integration. People who build their own will ask; the README does not claim either way.
- For the on-clock build (D-0029): whether a Whisker connection written for MicroPython holds
  up (the password login is about 20 seconds of arithmetic on the clock by estimate; live
  events; weeks of uptime); how long Whisker's refresh token lasts; memory with drawing, TLS,
  the live connection and the weather all running at once (each was measured alone); whether
  the 87 ms frame that builds a scene's plan shows on the LEDs; whether AWTRIX's autostart
  survives a firmware update on this clock (the docs say it does; not tried).
