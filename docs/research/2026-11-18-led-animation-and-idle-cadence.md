# Animating a 52x16 LED matrix, and how often an idle display should change (2026-11-18)

_Outcome (2026-11-18): this is the research behind D-0019, the idle show. Kyle set the split
himself (50 % home, 30 % weather, 20 % scenes), livelier than this research would suggest; the
findings shaped how the scenes are drawn rather than how often they play._

The question: how should short animated scenes and an information page be designed, timed and
joined on a 52x16 LED matrix that sits at the edge of vision in a home office?

This was desk research from the web. Every claim has its source. Where the only source is a
vendor blog or one person, it says so. **Nothing here was tested on the clock**; the push-speed
measurements are in `docs/reference/awtrix-ng.md`.

## What it means for our screens

- **Scenes need about 8 frames a second.** Pixel-art practice for tiny sprites is 80 to 160 ms a
  frame; one frame a second cannot show a scurry.
- **Keep fast motion across the screen rare; let most movement stay in one place.** That is why
  a scene is mostly Arthur seated and watching.
- **No brightness fades.** Use changes that keep exact colours: a dissolve, a wipe, a slide.
- **Swap an information page in place**, not with a moving edge.
- **Vary the timing and the order.** A fixed sequence is spotted quickly.
- **Hold key poses; few frames are enough.** A walk needs 2 to 6 frames at this size.
- **Static text, at least one dark pixel between letters.** Do not scroll.

## Sprites and animation at tiny sizes

- At 8 pixels and below, a few traits carry identity and extra detail hurts. Outlines are
  "often necessary to preserve readability within a limited palette". (Raymond Schlitter's
  practice, not a standard.)
  <https://www.slynyrd.com/blog/2023/11/26/pixelblog-47-tiny-pixels>,
  <https://www.slynyrd.com/blog/2025/11/28/pixelblog-59-tiny-sci-fi-pixels>
- Derek Yu disagrees on outlines: at small sizes colour defines a character more than an outline
  does. His squint test: a few large clusters of light and dark should still emerge.
  <https://www.derekyu.com/makegames/pixelart.html>
- On a black LED background a dark outline is simply unlit, so "outline" means a dark pixel of
  separation or a contrasting edge colour. (Our inference; neither author covers LEDs.)
- Frame counts (Schlitter): a 6-frame walk for a hero, 4 for most characters, 2 for simple
  ones; an 8-frame run can be cut to 4 by keeping the contact and pass poses.
  <https://slynyrd.com/blog/2018/8/19/pixelblog-8-intro-to-animation>
- Frame timing (Schlitter): 80 ms a frame for an 8-frame run, 160 ms for the 4-frame version;
  tiny runs 100 to 150 ms; too many in-betweens look sluggish. Same posts.
- Birds: draw the extreme wing poses first; a convincing flap needs fewer than 8 frames.
  <https://slynyrd.com/blog/2020/1/23/pixelblog-25-motion-cycles>
- Frame rates on LED products: Divoom's blog suggests 8 to 12 a second and four to six frames
  for a 16x16 loop (vendor marketing blog).
  <https://divoom.com/blogs/app-guide/how-to-animate-pixel-art-divoom-display>
  Tidbyt's Pixlet defaults to 50 ms a frame.
  <https://raw.githubusercontent.com/tidbyt/pixlet/main/encode/encode.go>
- **Not found:** how many pixels a recognisable cat, rat or bird needs; a limit on pixels moved
  per frame before motion strobes; hold times for key poses. Our rule of a third of the
  sprite's length per frame is a working guess to check on the LEDs.

## How LEDs differ from a monitor

- LEDs respond almost linearly and the eye does not, so drive values need gamma correction;
  uncorrected, a requested orange looks yellow.
  <https://learn.adafruit.com/led-tricks-gamma-correction>
- **Dim orange turns red.** Each channel has 256 levels, so as a colour dims the weaker channel
  rounds to zero first. (A forum answer, but it is plain arithmetic.)
  <https://wled.discourse.group/t/only-one-color-is-on-when-brightness-is-set-low/7348>
- Green LEDs are much brighter than red or blue, so mixed colours lean green.
  <https://github.com/FastLED/FastLED/wiki/FastLED-Color-Correction>
- Use strong contrast and avoid mid-tone backgrounds (Divoom, vendor blog).
  <https://divoom.com/blogs/app-guide/keep-scrolling-text-readable-pixel-display>
- A camera will show flicker the eye does not see (WS2812 PWM is about 400 Hz).
  <https://advatek.netlify.app/blog/guides/refresh-and-pwm-rates>
- **Not found:** any test of fades or other transitions on a small matrix. That a fade on an
  orange cat would step and turn red is inferred from the points above.

## Text

- Tidbyt ships 3x5, 4x6 and 5x8 fonts for a 64x32 display.
  <https://github.com/tidbyt/pixlet/blob/main/docs/fonts.md>
- Start at 5x7; 3x5 is the smallest practical; keep a pixel between letters because light
  bleeds between LEDs (Divoom, vendor blog).
  <https://divoom.com/blogs/setup-ideas/how-to-design-pixel-art-font-divoom-displays>
- Static text beats scrolling for a glance, and changes in place (a fade or an instant swap)
  beat a ticker for quick identification (McCrickard, Catrambone and Stasko, 2001).
  <https://faculty.cc.gatech.edu/~john.stasko/papers/interact01.pdf>

## What similar products do

- Tidbyt cycles apps every 15 seconds by default.
  <https://discuss.tidbyt.com/t/introducing-show-full-animation-app-cycle-speed/4898>
- AWTRIX 3 shows each app for 7 seconds and changes with a 500 ms slide by default.
  <https://raw.githubusercontent.com/Blueforcer/awtrix3/main/docs/api.md>
- WLED's default crossfade is 750 ms. <https://kno.wled.ge/features/settings/>

## Displays at the edge of vision

- "The periphery is informing without overburdening." (Weiser and Brown, 1995.)
  <https://calmtech.com/papers/designing-calm-technology>
- **Motion that travels is the most distracting kind**; motion anchored in one place is much
  less so, and slow linear motion was rated among the least irritating. Motion is noticed far
  better than colour in the periphery. (Bartram, Ware and Calvert, 2003.)
  <https://www.interruptions.net/literature/Bartram-IJHCS03.pdf>
- Animated peripheral displays did not significantly distract from a main task when changes
  were "small, subtle, and predictable". (McCrickard et al., link above.)
- Apple's always-on guidance: infrequent, subtle updates; a consistent layout; bring motion to
  a resting state rather than stopping it dead.
  <https://developer.apple.com/tutorials/data/design/human-interface-guidelines/always-on.json>
- Wear OS ambient mode: update about once a minute and pause all animation.
  <https://developer.android.com/training/wearables/views/always-on>
- Idle animations: let each behaviour fire at random within bounds; a fixed sequence is spotted
  quickly. (One developer's blog.)
  <https://dev.to/arkfuldodger/programming-natural-idle-character-animations-4l0g>
- **Not found:** any published figure for how often an idle display should change, a ratio of
  stillness to motion, or how rare a surprise should be. Our cadence is a choice, not a
  finding.

## Weather sources (no account needed)

- **Open-Meteo** gives temperature, a WMO weather code, day or night, and the daily high, low,
  sunrise and sunset in one call, with no key for non-commercial use ("personal home
  automation" is named). Limits: under 10,000 calls a day. Licence CC BY 4.0, with
  attribution. Values are from weather models, not a station. <https://open-meteo.com/en/docs>,
  <https://open-meteo.com/en/terms>, <https://open-meteo.com/en/licence>
- **US National Weather Service** (`api.weather.gov`) gives real station readings but takes
  several calls, needs a User-Agent, returns free text for conditions, and its observations can
  lag about 20 minutes or come back empty.
  <https://www.weather.gov/documentation/services-web-api>
- Open-Meteo is the simpler fit for one place fetched every 15 minutes. Its response shape has
  not been checked against a live call yet; that belongs in `docs/reference/open-meteo.md` when
  the source is built.
