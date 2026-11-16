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
  cool blues and purples as main colours. One cool accent (mint) is reserved for the robot
  cleaning and "fresh", so that state still stands apart.
- **In use is the funny moment.** Arthur and a poop emoji, alternating or together.
- **Waiting:** the hourglass and countdown layout is liked; it uses the warm scheme.
- **Idle** must show the **bin level and the litter level**, plus one more thing (last visit,
  visits today).
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
  stale. Keep anything essential readable with that border on.
- Plan for about one frame a second. No smooth animation. Each changed frame is an HTTP push
  to the clock, so live states may change every second, but **idle must stay nearly still**
  (a blink or tail flick a couple of times a minute at most).
- Every label must fit its box. Text that runs off is a bug; the tests check it.
- A person reads this from across a room. Prefer one clear thing over three small ones, and
  make states differ by shape as well as colour.
- Dim colours look different on real LEDs. Avoid relying on subtle shades.

## 3. Loop
1. Edit the layout, icon (text-art under `arthur/render/icons/`) or config.
2. Print it: `uv run python -m arthur.render.preview --all` (or name one state).
3. Look at it in the studio: start the "studio" server and open the All states view in the
   browser pane, then play a visit in the Scenario view to see transitions. To see a screen
   against what the robot really did, pick the Robot log source and replay a recorded visit.
4. When offering alternatives, add them as variants so they appear side by side, and let Kyle
   choose. Remove the losers afterwards.

## 4. Done means
- It reads correctly with and without the stale border.
- `uv run pytest` passes; golden renders are updated on purpose, not by accident.
- `docs/states.md` still describes what the screen conveys.
