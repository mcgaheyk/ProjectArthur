# ProjectArthur

A pixel clock that shows what a Litter-Robot 4 is doing. It was built for Arthur, an orange
tabby. When he steps in, the clock shows a poop emoji with a joke and how long he has been in
there; when he leaves, it counts down to the clean cycle, then shows the robot cleaning. The
rest of the time it shows how full the drawer is, the litter level and when he last went, and
now and then the weather, or a short scene of him chasing a rat or a bird. At night he sleeps.

<!-- A clip of the clock goes here. -->

## Two ways to get one

- **You have a Litter-Robot 4 and a Ulanzi TC002:** run this code. Setup is below.
- **You have something else** (a TC001, another display, a Home Assistant dashboard, a
  different robot), or you would rather build your own: [BUILD-YOUR-OWN.md](BUILD-YOUR-OWN.md)
  is a prompt for Claude Code or Codex. It points the agent at what this project learned the
  hard way and has it build one for your hardware.

## What it shows

| Screen | When |
|---|---|
| Arthur, the litter bar, the drawer bar, "last visit" | Nothing happening |
| The weather and date, or a 36-second scene (rat, bird or stretch) | Nothing happening, in turns with the screen above; at night he sleeps instead |
| A poop emoji with a joke and "IN USE", then Arthur with a timer | The cat is in the box |
| An hourglass and "CLEAN IN 7:42" | He has left; counting down to the clean cycle |
| Soapy blue, "CLEANING" | The clean cycle (about two minutes) |
| "FRESHLY CLEANED" | Five seconds after |
| "FULL", "EMPTY NOW" and a flashing red ring | The drawer is full |
| A little story about him peering into the globe | He interrupted a clean cycle |
| NO DATA | The program cannot reach Whisker |
| A dark red border around the last picture | The program stopped. The clock draws this itself |

The bars change colour with the robot's own readings. Every screen, and the robot reading that
drives it, is in [docs/states.md](docs/states.md).

## What you need

- A **Litter-Robot 4** set up in the Whisker app. The program logs in with the same email and
  password.
- A **Ulanzi TC002**, the 52x16 pixel clock. The smaller TC001 (32x8) is not supported.
- A **computer that stays on**, on the same network as the clock: a Raspberry Pi, a Linux box
  or a Mac. It has run on a Mac; it is written for a Pi but has not run on one yet.
- A **USB-C data cable** (not a charge-only one) to install the clock's firmware, and
  **2.4 GHz Wi-Fi** for the clock.
- Some comfort with a terminal.

## Setup

### 1. Put AWTRIX NG on the clock

The clock ships with Ulanzi's own firmware. Replace it with **AWTRIX NG 1.2.3**: download the
TC002 installer and follow the TC002 getting-started guide in the
[AWTRIX NG repository](https://github.com/Blueforcer/awtrix-ng). What we ran into:

- Plug in only one clock. Open the installer, wait for "Ready when you are.", then switch the
  clock off and on once: it accepts the connection only briefly after power-on.
- On a Mac, if the installer says "USB is in use", run `adb kill-server` (Android tools grab the
  clock) and try again. "USB did not become stable" was a poor cable.
- Installing erases the Wi-Fi and cloud login saved by Ulanzi's app. Enter your Wi-Fi in the
  installer. Holding the reset button for five seconds puts Ulanzi's firmware back.
- Find the clock's address in your router's list of devices (it is named `awtrixng-` and six
  characters). Give it a fixed address there, because the program is started with it.

Nothing else needs setting on the clock. The program takes the built-in Time and Status apps
out of the clock's rotation and turns off its double-size drawing by itself.

### 2. Get the code

Install [uv](https://docs.astral.sh/uv/), then:

```bash
git clone https://github.com/mcgaheyk/ProjectArthur.git
```

```bash
cd ProjectArthur && uv sync
```

uv fetches the right Python by itself.

### 3. Look at it before plugging anything in

```bash
uv run python -m arthur.sim
```

Open <http://127.0.0.1:7001/>. This is the studio: a fake clock in the browser, every screen
side by side, and played-out scenarios such as a whole visit or an idle hour. It needs no
robot, no clock and no login. Edit `config/display.toml` while it runs and it redraws.

### 4. Connect it to the robot and the clock

```bash
cp .env.example .env
```

Fill in `.env`: `WHISKER_USERNAME` and `WHISKER_PASSWORD` (your Whisker app login) and
`CLOCK_URL=http://` followed by the clock's address. `.env` is never committed and the program
never prints it. Leave the PETLIBRO lines empty; they belong to an unfinished experiment.

```bash
uv run --env-file .env python -m arthur.feeder
```

Within a minute the clock shows the home screen. The program reads the robot's last 72 hours
to find the last visit, then follows the robot live. Ctrl-C stops it, and about 90 seconds
later the clock puts the dark red border round the last picture.

**Run one copy per Whisker account.** Whisker allows one connection for this.

### 5. Keep it running

- **Raspberry Pi or Linux:** [scripts/arthur-feeder.service](scripts/arthur-feeder.service) is
  a systemd unit; the steps are at its top. It has not been run on a Pi yet.
- **Mac:** set your clock's address on the `CLOCK=` line of
  [scripts/start_feeder.command](scripts/start_feeder.command), then start it with
  `open -a Terminal scripts/start_feeder.command`. Start it that way, not from an editor's or
  an agent's terminal: macOS can cut a program off from the local network when the app that
  started it closes. While the Mac sleeps (closing the lid counts) the clock shows the border;
  the program catches up when the Mac wakes. `pkill -f arthur.feeder` stops it.

`logs/` grows by about 300 KB a day and nothing deletes it.

## Make it your cat

Everything you are likely to change is in [config/display.toml](config/display.toml), and the
running program picks up changes by itself.

- **His colours** are `fur`, `stripe`, `cream`, `nose` and `eye` under `[palette]`. On LEDs black
  is off, so a black cat needs a very dark grey or he disappears; try it in the studio first.
- **The jokes** are under `[captions]`. One is picked per visit. About seven letters fit; a
  longer one is skipped, never cut off.
- **Where and when:** `timezone`, the night hours he sleeps (`quiet_from_h`, `quiet_until_h`) and
  the weather's `latitude` and `longitude` (from [Open-Meteo](https://open-meteo.com/), no key
  needed). It starts out in Boston.
- **The idle show:** `page_pct` and `scene_pct` under `[idle]` set how much of the idle time goes
  to the weather and the scenes. Set both to 0 for the home screen only.

His poses are text-art files in `arthur/render/icons/`, one character per colour; the scenes
are drawn in `arthur/render/scenes/`. This project was built with
[Claude Code](https://claude.com/claude-code), and the repository carries its instructions
(`CLAUDE.md` and a screen-design skill), so you can open it there and ask for "a grey tabby
with white paws" and have it check the result in the studio.

## Good to know

- **Whisker's API is unofficial**, and Whisker's terms of service prohibit automated tools. This
  uses [pylitterbot](https://github.com/natekspencer/pylitterbot), the library behind Home
  Assistant's Litter-Robot integration. It follows the robot's live updates and asks again
  every five minutes. If Whisker changes something and it breaks, the clock says NO DATA rather
  than showing something old.
- **It is tested on one robot, one clock and one cat.** A Litter-Robot 4 and a TC002 running
  AWTRIX NG 1.2.3. With several Litter-Robot 4s on the account it shows the first.
- **Some numbers are estimates.** The cleaning countdown assumes 130 seconds (real cycles took
  123 to 134). Why each choice was made, and what was measured, is in
  [docs/DECISIONS.md](docs/DECISIONS.md).
- During scenes the clock takes 8 pictures a second, about 4,500 pushes an hour. It has kept
  up so far.
- Not affiliated with Whisker, Ulanzi or AWTRIX.

## How it works

```
source → RobotSnapshot → mapping → DisplayState → render → Frame → transport → clock | simulator
```

The program draws every pixel itself and sends the clock a bitmap. Only the transport knows the
clock's API, and the mapping and drawing are pure functions, so the studio and about 3,000 tests
run the same code the clock sees. [CLAUDE.md](CLAUDE.md) has the layout of the code;
[docs/reference/](docs/reference/) has the checked notes on the robot's and the clock's APIs.

```bash
uv run pytest
```

## License

[MIT](LICENSE).
