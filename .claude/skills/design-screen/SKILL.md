---
name: design-screen
description: Design or change what the clock shows for a display state (layout, colours, icons, wording) on the 52x16 clock, and check the result in the simulator. Use for any request to add, restyle, compare or fix a screen.
---

# Design a screen

## 0. The direction (from Kyle, 2026-11-16; see D-0009)
This is a fun project about a cat pooping and peeing. Screens should be **engaging and funny**,
and still readable in one glance from across a room. Clear first, funny second.

- **Arthur is an orange cat.** He is the mascot: draw him orange, give him poses, let him react.
- **Kyle's office has a reddish theme.** The palette is warm: oranges, golds, warm white. Avoid
  cool blues and purples as main colours. The one cool colour is a soapy light blue, used only
  for the robot cleaning and "fresh" (D-0012).
- **In use is the funny moment.** Two pages take turns: the poop emoji (with a face that
  blinks and looks around) beside a joke and "IN USE", then a smug Arthur with the timer.
- **One layout per live state** (D-0012). In use, waiting and cleaning have no alternatives;
  do not add variants back unless Kyle asks to compare something.
- **No badges** (D-0012). Do not add corner marks; a condition either gets a screen, a bar
  colour or a line of text, or it is not shown.
- **Waiting:** the hourglass, "CLEAN IN" and the countdown, in the warm scheme.
- **Interrupted is a little story** (D-0016): Arthur strolls up to the globe, the view cuts
  to inside the box where he looks in at us, then "INTERRUPTION" and a second phrase. He
  has come to have a look; he is not being told off.
- **Idle** must show the **bin level and the litter level**, plus one more thing (last visit,
  visits today). That is the home page.
- **Idle is a show** (D-0019, Kyle's split: 50 % home page, 30 % weather page, 20 % scenes).
  A **page** is information (the weather, the temperature and the date as `NOV 18`; Kyle does
  not want the weekday). A **scene** is a 36-second story with Arthur: he stays seated and
  watches for most of it, then a short burst (a chase, a leap, a stretch), then he settles back in his
  spot. At night he sleeps and there are no scenes. A real robot event replaces any of it at
  once.
- **Level bars carry meaning by colour** (D-0011). Litter follows the robot's own state word:
  green optimal, yellow refill or overfill, red low or empty. The drawer is orange and turns
  red when nearly full.
- **Rare states** (faults, no data, offline) share a template: icon, headline, and a line of
  text saying what is going on. **No data is red-family, not purple.**
- **Captions are jokes** and live in `config/display.toml` so Kyle can add his own.
- We cannot tell pee from poop. Do not claim either as fact; the poop emoji is just the mascot.
- Kyle expects to iterate on these over several days. Offer variants; do not treat any screen
  as final.

## 1. Decide which layer the change belongs to
- **What triggers a state, or what data it carries** → `arthur/mapping.py` and
  `arthur/model.py`, then update `docs/states.md`. The mapping never mentions pixels.
- **How a state looks** → `arthur/render/`. The renderer never mentions AWTRIX.
- **A colour, threshold or timing** → `config/display.toml` only. Do not hard-code them.

## 2. Know the canvas
- One canvas: **52x16**, the Ulanzi TC002 (D-0010). A 16x16 picture on the left, two lines of
  content beside it, and room for the live state and the drawer gauge together. There is no
  32x8 version to keep in step.
- The clock draws a 1-pixel dark-red border over the outermost row and column when data is
  stale. Keep anything essential readable with that border on, and draw nothing there. The
  deliberate exceptions are the flashing red ring on `drawer_full` (D-0011) and the moving
  blue border on `cleaning` (D-0012).
- Plan for about one frame a second on every state's own screen and on pages. Each changed
  frame is an HTTP push to the clock. Live states may change every second. **The home page is
  alive but not busy** (D-0013): short gestures with still pauses between them, and still for
  at least half of every minute.
- **Scenes and transitions run at 8 frames a second** (D-0019; measured on the clock). The
  rules for them, from `docs/research/2026-11-18-led-animation-and-idle-cadence.md`:
  - Motion that travels across the screen is the most distracting kind. Keep it to a short
    burst; fill the rest of a scene with small movements that stay in one place.
  - A sprite moves at most about a third of its own length per frame.
  - Ease by holding key poses (a freeze, a crouch, the top of a leap), not by slowing down.
  - Exact palette colours only. No brightness fades: a dimmed orange turns red on these LEDs.
    Change screens with a dissolve or a wipe.
  - Keep an unlit pixel between two sprites; on black, that gap is the only outline.
  - A scene starts and ends with Arthur seated at his spot, so the bars can wipe away and back.
  - Vary a scene from its seed so no two rounds match.
- Every label must fit its box. Text that runs off is a bug; the tests check it.
- A person reads this from across a room. Prefer one clear thing over three small ones, and
  make states differ by shape as well as colour.
- Dim colours look different on real LEDs. Avoid relying on subtle shades.

## 3. Loop
1. Edit the layout, icon (text-art under `arthur/render/icons/`) or config.
2. Print it: `uv run python -m arthur.render.preview --all` (or name one state). For a page
   or scene, name its fixture (`idle_page`, `idle_page_detail`, `idle_rat`, `idle_rat_turned`,
   `idle_bird`, `idle_bird_perched`, `idle_stretch`, `idle_stretch_knead`): `--at SECONDS` picks a moment, `--sheet DIR --frames N --step 0.125` writes a
   strip of consecutive frames as a PNG to open and look at, and `--weather` sets the reading.
3. Look at it in the studio: start the "studio" server and open the All states view in the
   browser pane, then play a visit in the Scenario view to see transitions. To see the idle
   show in rotation, play the `idle_hour` scenario: the show is live in the config (D-0023),
   so the studio plays it. Untick the studio's Idle show switch to keep idle on the home
   page alone while working on something else; it never changes the config. To see a screen
   against what the robot really did, pick the Robot log source and replay a recorded visit.
4. When offering alternatives, add them as variants so they appear side by side, and let Kyle
   choose. Remove the losers afterwards.

## 4. Done means
- It reads correctly with and without the stale border.
- `uv run pytest` passes; golden renders are updated on purpose, not by accident.
- `docs/states.md` still describes what the screen conveys.
