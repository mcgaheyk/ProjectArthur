# STATUS.md — project memory

_Last updated: 2026-11-15._ Current state only. History belongs in git and DECISIONS.md.

## Where things stand
- **Hardware:** nothing flashed or deployed. Host will be a Raspberry Pi on the LAN (D-0006).
  The display is **not chosen yet**: Ulanzi TC001 (32x8, about $50) or TC002 (52x16, about
  $100). Every screen exists at both sizes so the choice can be made from mock-ups (D-0001).
- **Firmware target:** AWTRIX NG pinned to 1.2.3 (D-0002). The feeder draws every pixel and
  pushes a bitmap (D-0003).
- **Built and tested (428 tests):**
  - Pipeline: `arthur/model.py`, `arthur/mapping.py`, `arthur/render/` at both sizes, with
    variants for ready, in_use, waiting and cleaning.
  - Screens: second design pass done on 2026-11-16 to Kyle's direction (D-0009): orange
    Arthur, warm palette, poop emoji, joke captions in `config/display.toml`. **Kyle has not
    seen this pass yet.** It is a starting point for iteration, not a final design.
  - Transport: `arthur/transports/awtrix_ng.py`. Only ever run against the fake clock.
  - Simulator: `arthur/sim/`. Two fake clocks, a studio page (live clocks, all-states grid,
    scenario playback, kill-the-feeder switch) and live reload of screens and config.
  - Spike: `scripts/spike_log.py`. Tested against fakes only; never run on the real robot.
- **Not built:** the feeder service itself (`arthur/sources/`, the run loop). The studio has
  its own in-process stand-in that plays fixtures and scenarios.

## Next
1. Kyle: copy `.env.example` to `.env`, add the Whisker login, and leave the spike running for a
   few days. Its first run is also the first test of the real login and live connection.
2. Kyle: react to the redesigned screens in the studio's All states view: which jokes land,
   which art does not read, which variants to keep, and which size feels right. Regenerate a
   one-page overview any time with `uv run python -m arthur.render.preview --sheet DIR`.
3. Feed spike findings back into `docs/states.md` and `config/display.toml` (phase durations,
   cat-detected latency, what the raw fields add), and add a way to replay a spike log in the
   studio.

## Later
- The feeder service: `arthur/sources/whisker.py` and the run loop with heartbeat.
- Pi deployment: systemd unit, reconnect with backoff, log rotation, and a memory check
  (pylitterbot pulls in boto3/botocore).
- Flash AWTRIX NG and compare the simulator against the clock's own screen read-back. The
  guesses to check are listed at the end of `docs/reference/awtrix-ng.md`.
- Optional: build `awtrix-linux` (NG's host build) as a conformance check for the transport.
- On-device watchdog script for a louder stale screen. Review NG's Berry agent skill first.
- Alert sounds, button or knob acknowledgement, scheduled night dimming (needed on the TC002).
- Health nudges: no visit in N hours, unusually many visits.
- Experiment: the no-cloud ESPHome firmware for the robot itself.

## Known rough edges in the screens
- **In use, 52x16:** Arthur's side-eye is subtle and needs a couple of seconds of watching;
  his head is a little flat because the litter-box rim takes the lower rows. The stink lines
  read as chevrons.
- **In use, 32x8:** still the weaker size: a small strain face and a small poop take turns.
- **Captions** are capped at about seven letters at 52x16 (five at 32x8), so "SPIN CYCLE" and
  "EMPTY ME" do not fit. "POOPIN?" and "PEEIN?" carry question marks because we cannot tell
  which; Kyle may prefer them without.
- **32x8 rare states** page one word at a time (LID / OFF, START / UP), and the two idle
  gauges have no labels, only colours.
- **Badges** are still small coloured marks that do not explain themselves.
- Idle shows the last-visit time but not visits today.
- Brown and the dim shades have only been judged on a monitor, not on LEDs.
- In the studio's fixture mode, time is frozen, so timers do not count; scenario and manual
  modes run with time moving.

## Known unknowns
- How fast Whisker pushes "cat detected". Nobody has published a measurement.
- How long each phase really lasts, and whether the raw fields reveal cycle phases.
- How colours and dim shades read on real LEDs.
- TC002: whether AWTRIX NG support is out of beta, whether it has a light sensor, its physical
  size. Check before buying one.
- AWTRIX NG: whether re-pushing an app clears the stale mark (inferred, not confirmed), and
  what an empty rotation shows after a clock reboot.
