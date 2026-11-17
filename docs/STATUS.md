# STATUS.md — project memory

_Last updated: 2026-11-17._ Current state only. History belongs in git and DECISIONS.md.

## Where things stand
- **Hardware:** Kyle has a **Ulanzi TC002** (52x16) and a **Raspberry Pi 5** (D-0010). **The
  clock runs AWTRIX NG 1.2.3 since 2026-11-17** and is on the home Wi-Fi as `awtrixng-77a767`
  (192.168.86.41 that day; the address can change). Its built-in Time and Status apps are out
  of the rotation. The Pi has not arrived; nothing is deployed to it.
- **One size.** Everything is 52x16. The 32x8 layouts and the second fake clock were removed
  (D-0010); commit 91e4960 is the last one that has them.
- **Firmware target:** AWTRIX NG pinned to 1.2.3 (D-0002). It is the latest release and ships a
  TC002 build. The feeder draws every pixel and pushes a bitmap (D-0003).
- **Built and tested (544 tests):**
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
    double-size drawing (`enlargeApps`); without that the clock shows a quarter of the frame.
    Run against the real clock on 2026-11-17: idle animated for ten minutes and the clock's
    screen read-back matched the frames sent, pixel for pixel. Kyle said idle looks good on
    the LEDs. No other screen has been seen on hardware.
  - Push test: `scripts/push_test.py --clock http://HOST [NAME] [--seconds N] [--readback]`
    (logic in `arthur/pushtest.py`) shows any screen on a real clock. A test tool, not the
    feeder: nothing pushes to the clock unless it is running.
  - Simulator: `arthur/sim/`. One fake TC002 that starts with `enlargeApps` on, as a real
    clock does, and a studio page (live clock, all-states grid, scenario playback,
    kill-the-feeder switch) with live reload of screens and config. Its **Robot log** source
    drives the clock from the spike's log: Follow shows the real robot live, Replay plays a
    recorded visit at 1x, 10x or 60x, and both show the robot's raw fields beside the mapped
    state.
  - Log source: `arthur/sources/logfile.py` turns the spike's log lines into `RobotSnapshot`s
    (read only; it never talks to Whisker).
  - Spike: `scripts/spike_log.py`. **Running on Kyle's Mac since the evening of 2026-11-15**,
    so the real login and live connection work. By the morning of the 16th it had logged two
    complete visits in `logs/spike-*.jsonl`. The studio's Robot log source reads them.
  - PETLIBRO spike: `scripts/spike_petlibro.py` (logic in `arthur/petlibro_spike.py`, 39
    tests). Logs what the Granary and a fountain report through PETLIBRO's cloud, read only.
    **Running on Kyle's Mac since 10:33 on 2026-11-16**, detached, against the real Granary
    through a second PETLIBRO account it is shared to. So the login, a shared account and the
    Granary's fields are confirmed; `docs/reference/petlibro.md` says which. It writes
    `logs/petlibro-*.jsonl` and `logs/petlibro-console.log`. It stops on a Mac restart and
    pauses during sleep; stop it with `pkill -f spike_petlibro`. Never start a second copy on
    the same account.
- **Not built:** the feeder service itself (`arthur/sources/whisker.py`, the run loop). The
  studio has its own in-process stand-in that plays fixtures, scenarios and the spike's log.

## Next
1. Kyle: leave the spike running for a few days, and watch real visits in the studio (Live
   view, Source: Robot log). Follow only works while the spike is running.
2. Kyle: react to the redesigned screens in the studio's All states view: which jokes land,
   which art does not read, which variants to keep. Regenerate a one-page overview any time
   with `uv run python -m arthur.render.preview --sheet DIR`.
3. Kyle, with Claude: look at the other screens on the real clock with `scripts/push_test.py`
   (drawer full, in use, cleaning, interrupted, no data) and check the remaining guesses at
   the end of `docs/reference/awtrix-ng.md`: the stale mark, re-pushing clearing it, the
   indicator pixels, and whether Time and Status stay out of the rotation after a reboot.
4. Build the feeder service: `arthur/sources/whisker.py` and the run loop with heartbeat. With
   the hardware here it is the main missing piece. Just cleaned now lasts 5 s (D-0015), so the
   feeder must date a status change from when it saw it.
5. Decide with Kyle how to fix what the first two real visits showed, then change
   `arthur/mapping.py`, `docs/states.md` and `config/display.toml`. Nothing has been changed
   yet. Replayed through the real mapping:
   - **In use and waiting cannot be told apart from `status` alone.** Sunday: `CAT_DETECTED`
     for 6 s, then `CAT_SENSOR_TIMING` (raw `ROBOT_CAT_DETECT_DELAY`) while he was probably
     still inside, so in-use flashed by and the countdown started early. Monday: `CAT_DETECTED`
     (raw `ROBOT_CAT_DETECT`) for 9 min 13 s, straight into the clean cycle, so the in-use
     timer counted to nine minutes after he had left. The pet-weight update about a minute in
     may be the better "he has left" signal.
   - **The countdown ends early.** The clean cycle started about 9.3 minutes after detection
     both times, against a wait setting of 8 minutes, so "DUE" showed for 81 s on Sunday.
     `clean_cycle_wait_time_minutes` reads 8, which is not a documented value; the studio's
     Manual mode rejects it.
   - **Cleaning is shorter than the estimate:** 2 min 3 s and 2 min 10 s against
     `clean_cycle_estimate_s = 150`, so the progress never fills. Raw `robotCycleStatus` shows
     real phases (`CYCLE_DUMP`, `CYCLE_DFI`, `CYCLE_LEVEL`, `CYCLE_HOME`) that could replace the
     estimate.
   - Raw `catDetect` never left `CAT_DETECT_CLEAR`. `last_seen` is stuck at 2026-11-13, so the
     offline screen's "seen" age would be wrong.
6. Kyle: leave the PETLIBRO spike running until the Granary has run low and been refilled at
   least once. That is the one thing it has not seen: what `surplusGrain` and
   `warehouseSurplusGrain` read when food is low, and how soon. Then read the log back into
   `docs/reference/petlibro.md`.
7. Kyle: buy a fountain, a plug-in model, PLWF105 or PLWF106 (see the research note), and
   share it to the same second account. The running spike picks it up within the hour.

## Later
- Pi 5 deployment: Raspberry Pi OS Lite (64-bit), systemd unit, reconnect with backoff, log
  rotation. The memory check (pylitterbot pulls in boto3/botocore) only matters if it is the
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
- **Cleaning:** the countdown is still an estimate (150 s against real cycles of about 125 s).
  The 6-row digits and the pale bubbles on the blue border have only been judged on a monitor.
- On-battery and connection-down are no longer shown anywhere (badges were removed).
- **Drawer full:** the narrow M and W in "EMPTY NOW" and the flashing ring have only been
  judged on a monitor. The ring means a push to the clock every second for as long as the
  drawer stays full. The poop's face runs on its own 15-second loop (blink, glance left and
  right, one wink), deliberately out of step with the ring; Kyle did not want an opening
  mouth or anything in sync with the flashing. The glances move one pixel and may not show
  from across the room.
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
- How fast Whisker pushes "cat detected". Nobody has published a measurement.
- How long each phase really lasts, and whether the raw fields reveal cycle phases.
- How colours and dim shades read on the TC002's LEDs. NG is installed; only idle has been looked at.
- AWTRIX NG on the TC002: whether re-pushing an app clears the stale mark (inferred, not
  confirmed); what an empty rotation shows after a clock reboot; exactly which pixels the three
  indicators use; whether the built-in apps stay out of the rotation after a reboot (open
  reports #141 and #142 say app order and a disabled app can change).
- Which Raspberry Pi 5 it is (RAM size).
