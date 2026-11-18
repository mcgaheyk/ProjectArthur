# STATUS.md — project memory

_Last updated: 2026-11-18, late morning._ Current state only. History belongs in git and DECISIONS.md.

## Where things stand
- **Hardware:** Kyle has a **Ulanzi TC002** (52x16) and a **Raspberry Pi 5** (D-0010). **The
  clock runs AWTRIX NG 1.2.3 since 2026-11-17** and is on the home Wi-Fi as `awtrixng-77a767`
  (192.168.86.41 that day; the address can change). Its built-in Time and Status apps are out
  of the rotation. The Pi has not arrived; nothing is deployed to it.
- **One size.** Everything is 52x16. The 32x8 layouts and the second fake clock were removed
  (D-0010); commit 91e4960 is the last one that has them.
- **Firmware target:** AWTRIX NG pinned to 1.2.3 (D-0002). It is the latest release and ships a
  TC002 build. The feeder draws every pixel and pushes a bitmap (D-0003).
- **The clock is live.** The feeder (`python -m arthur.feeder`) has been **running on Kyle's
  Mac since 18:12 on 2026-11-17** (restarted at 18:33 for the litter bar, and at 09:10 on 2026-11-18 after the push bench), detached under `caffeinate`, against the real robot and
  the real clock (D-0018). It logged in, read the robot and showed idle; the clock's screen
  read-back matched our render of the robot's state on all 832 pixels. **No real visit has
  been watched on the clock yet.** It stops on a Mac restart and the clock shows the stale
  border while the Mac sleeps. Its messages go to `logs/feeder-console.log`. Stop it with
  `pkill -f arthur.feeder`; start it with the command in CLAUDE.md. Never run a second copy,
  or `scripts/spike_log.py`, on the same account.
- **Built and tested (2,388 tests):**
  - Feeder: `arthur/feeder.py` (the loop), `arthur/pusher.py` (when to push),
    `arthur/sources/whisker.py` (the one Whisker connection). On a login or API failure it
    shows NO DATA and retries; it reloads `config/display.toml` when the file changes; it
    writes `logs/spike-*.jsonl` in the spike's format, so the studio's Robot log source
    follows it.
  - Visit rules (D-0017): in use lasts until the weight reading arrives, the countdown runs
    from that moment, cleaning is a 130-second estimate. `arthur/sources/tracker.py` keeps
    these observed facts for both the live source and the log reader. Replayed over the log's
    seven real visits, the countdown ended within 4 s of the real clean start every time.
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
    every five minutes. Kyle said idle looks good on the LEDs; no other screen has been seen
    on hardware.
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
- **In progress: the idle show (D-0019, D-0020).** Idle will rotate in a three-minute round:
  home 45 s, a weather and date page 54 s, home 45 s, a 36-second scene, with Arthur asleep at
  night. Kyle approved the plan and set the split (50 / 30 / 20) on 2026-11-18.
  **It is built but switched off:** `[idle] page_pct` and `scene_pct` are 0 in
  `config/display.toml`, so the clock still shows only the home page. Built and tested:
  - The loop ticks 8 times a second on the wall clock (`arthur/timebase.py`). Measured on the
    clock: 8 frames a second is comfortable (slowest push 64 ms of a 125 ms budget); figures
    in `docs/reference/awtrix-ng.md`.
  - The schedule (`arthur/schedule.py`), decided in mapping and carried as a cue.
  - The engine (`arthur/render/idle_show.py`): a page dissolves in and out; a scene opens
    with the bars wiping away beside seated Arthur and closes with them wiping back.
  - The weather and date page in two layouts, `weather` and `weather_big`
    (`arthur/render/pages.py`). The date is `NOV 18`: Kyle does not want the weekday.
  - The weather source (`arthur/sources/weather.py`): Open-Meteo for Boston every 15 minutes,
    confirmed with one live request (`docs/reference/open-meteo.md`).
  - The rat scene with both endings (`arthur/render/scenes/rat.py`): the rat creeps toward
    seated Arthur, bolts, and scurries back across the empty stage with him galloping after
    it; one round in four the tables are turned.
  - The bird scene with both endings (`arthur/render/scenes/bird.py`): he watches it, chases
    it, crouches, and hangs at the top of his leap spread-eagled; a feather lands on his
    head; one round in four the bird comes back and perches there.
  - Night sleep: in the quiet hours (22:00 to 07:00 Boston time) the home page shows him
    curled up, breathing every 12 s with Zs, 10 screen changes a minute instead of 27.
  - The studio plays all of it: an Idle show switch, sample weather, and the `idle_hour` and
    `visit_mid_scene` scenarios, and `night_falls` and `morning` for the sleep transitions.
    Fixtures `idle_page`, `idle_page_big`, `idle_rat`, `idle_rat_turned`, `idle_bird`,
    `idle_bird_perched` and `ready_asleep` pin one screen each. In `idle_hour` the first
    bird scene is at 2:24, the first rat at 5:24, the perched ending at 8:24 and the
    tables-turned ending at 11:24.
  **Kyle has seen stills of the art and the weather page, not the studio, and nothing on
  LEDs. Every screen here is a first cut for him to react to.**
- **The running feeder is older than this code.** It was started at 09:10 on 2026-11-18,
  before the 8-a-second loop and the weather source existed. The next restart brings both in:
  the loop changes and the feeder starts fetching the weather, though with the shares at 0
  nothing new is shown. That restart has not been tried against the real robot and clock.
- **Not built:** the Pi deployment (systemd unit, log retention), night dimming, food and
  water. See Later.
## Next
**In hand, with Claude: the idle show (D-0019).** Left to do:
- **Kyle, in the studio:** tick the Idle show switch and play `idle_hour`; look at the tiles
  in All states. Choose the weather layout (`weather` or `weather_big`), react to the rat
  and bird scenes and their second endings, the sleeping cat, the dissolve and the wipe, the
  rat's grey and the bird's blue, whether the high and low should stay orange, and whether
  the bird's wings (beating four times a second for about 20 s) are too busy.
- **Kyle, at the clock, about 15 minutes with the feeder stopped:** the bench's ten-minute run
  at 8 a second and its test patterns (`scripts/push_bench.py`: `sustained`, `walk`,
  `dissolve`, `wipe`, `slide`, `counter`), then any one cutaway with
  `scripts/push_test.py --clock http://ADDRESS idle_rat`.
- **Go live:** set `page_pct = 30` and `scene_pct = 20` in `config/display.toml` and restart
  the feeder. The page can go first by setting only `page_pct`.

1. Kyle: watch Arthur's next visits on the clock. In use should last until he leaves, then a
   countdown from about 8:00 that reaches zero as the clean cycle starts, then about two
   minutes of cleaning. Say what looks wrong; `logs/spike-*.jsonl` has the robot's side of
   each visit and the studio's Replay shows what the clock should have drawn.
2. Kyle: react to the screens, now on real LEDs: which jokes land, which art does not read,
   colours and dim shades. Any one screen: `scripts/push_test.py` (stop the feeder first).
3. With Claude, when they happen or can be staged: the cases the feeder has never met. The
   stale border (stop the feeder for two minutes), a clock reboot (do Time and Status stay
   out of the rotation?), an interruption, drawer full, the robot offline. The remaining
   guesses about the clock are at the end of `docs/reference/awtrix-ng.md`.
4. When the Pi arrives: deploy the feeder there (Later, first item) and stop the Mac's copy.
   Give the clock a fixed address in the router first; the feeder is started with its
   address and the router can change it.
5. Kyle: leave the PETLIBRO spike running until the Granary has run low and been refilled at
   least once. That is the one thing it has not seen: what `surplusGrain` and
   `warehouseSurplusGrain` read when food is low, and how soon. Then read the log back into
   `docs/reference/petlibro.md`.
6. Kyle: buy a fountain, a plug-in model, PLWF105 or PLWF106 (see the research note), and
   share it to the same second account. The running spike picks it up within the hour.

## Later
- Pi 5 deployment: Raspberry Pi OS Lite (64-bit), a systemd unit for `python -m arthur.feeder`
  (the feeder already reconnects with backoff and stops cleanly on SIGTERM), and retention for
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
- What the feeder does in the cases it has never met live: a visit with no weight push, an
  interruption, Whisker's connection dropping, a rejected login, a clock reboot (D-0018).
- How colours and dim shades read on the TC002's LEDs. NG is installed; only idle has been looked at.
- AWTRIX NG on the TC002: whether re-pushing an app clears the stale mark (inferred, not
  confirmed); what an empty rotation shows after a clock reboot; exactly which pixels the three
  indicators use; whether the built-in apps stay out of the rotation after a reboot (open
  reports #141 and #142 say app order and a disabled app can change).
- Which Raspberry Pi 5 it is (RAM size).
