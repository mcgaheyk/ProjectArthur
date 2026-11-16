# STATUS.md — project memory

_Last updated: 2026-11-16._ Current state only. History belongs in git and DECISIONS.md.

## Where things stand
- **Hardware:** Kyle has a **Ulanzi TC002** (52x16) and a **Raspberry Pi 5** (D-0010). Neither
  is set up: the clock still runs Ulanzi's own firmware and nothing is deployed to the Pi.
- **One size.** Everything is 52x16. The 32x8 layouts and the second fake clock were removed
  (D-0010); commit 91e4960 is the last one that has them.
- **Firmware target:** AWTRIX NG pinned to 1.2.3 (D-0002). It is the latest release and ships a
  TC002 build. The feeder draws every pixel and pushes a bitmap (D-0003).
- **Built and tested (334 tests):**
  - Pipeline: `arthur/model.py`, `arthur/mapping.py`, `arthur/render/`, with variants for
    ready, in_use, waiting and cleaning.
  - Screens: second design pass done on 2026-11-16 to Kyle's direction (D-0009): orange
    Arthur, warm palette, poop emoji, joke captions in `config/display.toml`. **Kyle has not
    reacted to this pass yet.** It is a starting point for iteration, not a final design.
  - Transport: `arthur/transports/awtrix_ng.py`. Before pushing, it turns off the TC002's
    double-size drawing (`enlargeApps`); without that the clock shows a quarter of the frame.
    Only ever run against the fake clock.
  - Simulator: `arthur/sim/`. One fake TC002 that starts with `enlargeApps` on, as a real
    clock does, and a studio page (live clock, all-states grid, scenario playback,
    kill-the-feeder switch) with live reload of screens and config.
  - Spike: `scripts/spike_log.py`. Tested against fakes only; never run on the real robot.
- **Not built:** the feeder service itself (`arthur/sources/`, the run loop). The studio has
  its own in-process stand-in that plays fixtures and scenarios.

## Next
1. Kyle: copy `.env.example` to `.env`, add the Whisker login, and leave the spike running for a
   few days. Its first run is also the first test of the real login and live connection.
2. Kyle: react to the redesigned screens in the studio's All states view: which jokes land,
   which art does not read, which variants to keep. Regenerate a one-page overview any time
   with `uv run python -m arthur.render.preview --sheet DIR`.
3. Kyle, with Claude: install AWTRIX NG 1.2.3 on the TC002 with the desktop USB installer. The
   steps and the way back to Ulanzi's firmware are in `docs/reference/awtrix-ng.md`; installing
   wipes the Ulanzi app's Wi-Fi and login. Then push a frame to it and compare its screen
   read-back with the simulator's. The guesses to check are listed at the end of that file.
4. Build the feeder service: `arthur/sources/whisker.py` and the run loop with heartbeat. With
   the hardware here it is the main missing piece.
5. Feed spike findings back into `docs/states.md` and `config/display.toml` (phase durations,
   cat-detected latency, what the raw fields add), and add a way to replay a spike log in the
   studio.

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
- Experiment: the no-cloud ESPHome firmware for the robot itself.

## Known rough edges in the screens
- **In use:** Arthur's side-eye is subtle and needs a couple of seconds of watching; his head
  is a little flat because the litter-box rim takes the lower rows. The stink lines read as
  chevrons.
- **Captions** are capped at about seven letters, so "SPIN CYCLE" and "EMPTY ME" do not fit.
  "POOPIN?" and "PEEIN?" carry question marks because we cannot tell which; Kyle may prefer
  them without.
- **Badges** are still small coloured marks that do not explain themselves.
- Idle shows the last-visit time but not visits today.
- Brown and the dim shades have only been judged on a monitor, not on LEDs.
- In the studio's fixture mode, time is frozen, so timers do not count; scenario and manual
  modes run with time moving.

## Known unknowns
- How fast Whisker pushes "cat detected". Nobody has published a measurement.
- How long each phase really lasts, and whether the raw fields reveal cycle phases.
- How colours and dim shades read on the TC002's LEDs. Checkable once NG is installed.
- AWTRIX NG on the TC002: whether re-pushing an app clears the stale mark (inferred, not
  confirmed); what an empty rotation shows after a clock reboot; exactly which pixels the three
  indicators use; whether the built-in apps stay out of the rotation after a reboot (open
  reports #141 and #142 say app order and a disabled app can change).
- Which Raspberry Pi 5 it is (RAM size).
