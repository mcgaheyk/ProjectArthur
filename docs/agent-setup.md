# Setting up ProjectArthur for someone (instructions for an AI agent)

You are Claude Code, Codex or a similar agent. Someone pasted a short prompt asking you to set
up ProjectArthur, a pixel clock that shows what their cat's litter robot is doing. You cloned
this repository and are reading this file. Follow it.

Your job is to make this painless for them: you ask the questions, you do the technical work,
and you check each step works before going on. They do only what needs hands or secrets:
plugging things in, a few clicks in an installer, and typing their own password into one file.

This repository was built for one person (Kyle) and one cat (Arthur). `CLAUDE.md` and
`docs/STATUS.md` describe Kyle's setup and Kyle's working rules; they are not about this
person. Do not commit or push anything unless they ask.

## How to work with them
- Assume they are not technical unless they say otherwise. No jargon; when a word like
  "terminal" or "IP address" is unavoidable, say what it is in a few words.
- Ask one question at a time. Offer choices and say which you would pick. Say in a sentence
  why you are asking.
- Keep a short checklist of the steps and show where they are now and then.
- Before a command that installs something or changes their computer, say what it does in one
  line. Run things yourself; ask them to type a command only when it needs their password
  (`sudo`) or must run outside your session.
- When something fails, find out why before asking them to try again.
- **Never ask for a password, token or login in the chat, and never read, print or log the
  `.env` file.** They type their Whisker login into `.env` themselves (step A6).
- Ask for a town or city, never a street address.

## Step 1: get to know their setup
Ask these in order, skipping anything they have already told you. Then sum up what you heard
and which path below fits, and wait for their OK.

1. **Their litter robot.** Litter-Robot 4, Litter-Robot 3, or something else? (The Whisker
   app names the model.)
2. **Their display.** Do they have one yet?
   - a **Ulanzi TC002** (the wider one: 52 by 16 lights): what this repository runs on;
   - a **Ulanzi TC001** (32 by 8 lights): not supported here, but you can build for it (path B);
   - **none yet**: the TC002 is the easy route. They can still see everything in the studio
     (step A3) before buying one;
   - something else: a Home Assistant dashboard, a tablet, an e-ink screen (path B).
3. **The computer that will run it.** It has to stay on: a laptop that sleeps (closing the lid
   counts) freezes the clock until it wakes. A Mac, a Raspberry Pi or another Linux machine
   have been planned for; a Mac is the one it has actually run on. Windows has not been tried.
4. **Their cat.** Name, coat colours and pattern, eye colour. Tell them a photo is the easiest
   answer if your interface takes images. With several cats, ask which one to draw.
5. **Where they live,** as a town or city: for the weather on the clock and its clock time.
   Look it up (below), confirm the match, and ask °F or °C, suggesting the usual one for their
   country.
6. **Home Assistant.** Do they already use Home Assistant's Litter-Robot integration? If so,
   say that nobody has tried this alongside it on the same Whisker account.

**Looking up the town** (no key needed, details in `docs/reference/open-meteo.md`):
`https://geocoding-api.open-meteo.com/v1/search?name=<town>&count=5&language=en&format=json`.
Each result has `name`, `admin1` (state or region), `country`, `latitude`, `longitude` and
`timezone`. Several places share names (Portland is in Oregon, Maine and more), so read the
choices back and let them pick. Round the coordinates to two decimal places.

**Which path:** a Litter-Robot 4 and a TC002 (or none yet and happy to get one): **path A**.
Anything else: **path B**.

## Path A: set up this repository (a Litter-Robot 4 and a TC002)
Work inside the cloned `ProjectArthur` folder. The README has the same steps for people doing
it by hand; it is the reference for anything not spelled out here.

**A1. Install.** They need `git` (you already used it) and `uv`; install `uv` with its
official installer (<https://docs.astral.sh/uv/>) if it is missing. Then `uv sync`, which also
fetches the right Python. Check it works: `uv run python -m arthur.render.preview --all`
prints every screen as text.

**A2. Make it theirs** in `config/display.toml`. Change only these:
- `timezone` under `[display]`: the town's `timezone`.
- `[weather]`: `latitude` and `longitude`; for Celsius, uncomment `units = "celsius"`.
- `quiet_from_h` and `quiet_until_h` under `[idle]`: the hours the cat is drawn asleep (22 to
  7). Mention it; change only if they want.
- **The cat's colours** under `[palette]`: `fur` (body), `stripe` (stripes and closed eyes),
  `cream` (muzzle, chest and paws; also the hourglass frame and the moon), `nose`, `eye`. On LEDs black is off, so a black cat needs a
  very dark grey or it vanishes; a white cat works as a warm white. The drawings have just those
  five parts, so a tabby, a solid colour or a tuxedo (`fur` dark, `cream` white) work well; a
  calico's patches or a face mask need new drawings, which you can offer as a later step.
  Keep the cat clearly different from two other colours: `poop` (brown, the poop emoji) and
  `ghost` (grey, the "we don't know what he's doing" cat). For a brown or grey cat, shift
  those instead and tell them.
- Leave the other colours alone unless they ask: red means trouble and soapy blue means
  cleaning on every screen.
- The jokes under `[captions]` can stay. If they want their own, about seven letters fit; a
  longer one is skipped, and `uv run pytest -q tests/test_captions_and_motion.py` names any
  that do not fit.

The cat's name does not appear on the screens, so nothing else needs renaming.

**A3. Show them.** Start the studio, `uv run python -m arthur.sim` (leave it running), and
open <http://127.0.0.1:7001/> for them (`open` on a Mac, `xdg-open` on Linux). The All states
view shows every screen with their cat and their colours. Adjust colours with them until they
like it; the studio redraws when the config file changes. Stop the studio when done.

**A4. Put AWTRIX NG on the clock** (skip if it already runs AWTRIX NG 1.2.3). Walk them through
it one physical step at a time, using the README's "Put AWTRIX NG on the clock" and the
"Installing NG on the TC002" notes in `docs/reference/awtrix-ng.md`. The TC002 installer comes
from the AWTRIX NG repository (<https://github.com/Blueforcer/awtrix-ng>, folder
`installer/tc002/`). On a Mac it is not notarized, so macOS warns before opening it; tell them
that is expected for this installer and how to open it anyway (System Settings, Privacy &
Security, Open Anyway), and let them decide. They need a USB-C data cable, and they enter their
2.4 GHz Wi-Fi in the installer. If the installer says "USB is in use" on a Mac, run
`adb kill-server` if `adb` exists.

**A5. Find the clock and test it.** The clock calls itself `awtrixng-` plus six characters.
Ask them to find it in their router's app or device list and read you the address (or the full
name; `awtrixng-xxxxxx.local` usually works on a Mac). Then put their cat on it:
`uv run python scripts/push_test.py --clock http://ADDRESS --seconds 30`. Ask what they see.
Suggest giving the clock a fixed address in the router ("DHCP reservation") so it never moves;
help them find the setting if they want.

**A6. Their Whisker login.** Copy `.env.example` to `.env` and set its `CLOCK_URL=` line to
`http://ADDRESS` yourself, before they add anything. Then open `.env` in a plain text editor for
them (`open -e .env` on a Mac, `nano .env` on Linux) and ask them to type their Whisker app
email after `WHISKER_USERNAME=` and password after `WHISKER_PASSWORD=`, no spaces or quotes,
then save and close. Leave the PETLIBRO lines empty. From here on do not open, read or print
`.env`; if the login fails, ask them to check it themselves.

**A7. First run.** Make sure nothing else uses their Whisker account through this project,
then run `uv run --env-file .env python -m arthur.feeder` and watch its messages. Within a
minute the clock shows the home screen: the cat, the litter bar, the drawer bar and the time
since the last visit. NO DATA means it cannot reach Whisker: usually the login; ask them to
recheck `.env`. Then stop it with Ctrl-C (or end the process); about 90 seconds later the clock
draws a dark red border, which is how it says the picture is out of date. Show them that, so
they know it.

**A8. Keep it running.** Only one copy may run at a time.
- **Mac:** set the `CLOCK=` line in `scripts/start_feeder.command` to their address, then run
  `open -a Terminal scripts/start_feeder.command`. That hands the start to Terminal. **Never
  start the long-running feeder from your own shell or in the background:** macOS cuts it off
  from the clock when your session ends (`docs/DECISIONS.md`, D-0024). Remind them the Mac must
  stay awake, and that it does not restart by itself after the Mac restarts (they run the same
  command again). `pkill -f arthur.feeder` stops it.
- **Raspberry Pi or Linux:** fill in `scripts/arthur-feeder.service` (their user name, the
  folder, the path `command -v uv` gives) and give them the `sudo` commands at its top to paste
  into their own terminal. It has not been run on a Pi before; check `journalctl -u
  arthur-feeder` afterwards. If the Pi is a separate machine, get everything working on their
  main computer first, then repeat A1, A6 and A8 on the Pi.
- **Windows:** not tried. Say so. If they want to try, run it in the foreground first; keeping
  it running would need a Task Scheduler task.

**A9. Finish.** Tell them in a few lines: what is running and where, how to stop and start it,
what NO DATA and the red border mean, that the cleaning countdown is an estimate, and that they
can come back to an agent in this folder to change colours or jokes. Mention `logs/` grows by
about 300 KB a day.

## Path B: build one for their hardware
Do not run this repository's code against their accounts. Make a new folder for their project
next to `ProjectArthur` and use `ProjectArthur` as a reference.

First read, from `ProjectArthur/`:
1. `README.md` and `docs/states.md`: every screen and the robot reading that drives it.
2. `docs/DECISIONS.md`: why things are the way they are, including what went wrong on the real
   robot and clock.
3. `docs/reference/pylitterbot.md`: the robot's cloud API through the pylitterbot library,
   checked against its source and a real robot. If their display runs AWTRIX, also
   `docs/reference/awtrix-ng.md`; it covers AWTRIX NG 1.2.3, and AWTRIX 3 has a different API.
   `docs/reference/open-meteo.md` for the weather.

These notes were measured on real hardware, so prefer them over what you remember. They cover
one robot, one clock and one firmware version, so work out what differs for theirs. For a
32x8 display, this repository's git history has 32x8 layouts (commit 7d4393e) from before it
went 52x16 only.

Then give them a short plan in plain words: what you will build, what carries over and what
does not, and what cannot be known until it runs. Wait for their OK.

Rules that are easy to get wrong:
- **A visit is read from the weight reading, not from the status.** `CAT_DETECTED` and
  `CAT_SENSOR_TIMING` do not track the cat. "In use" lasts until the robot reports a new
  weight, and the robot's wait before cleaning counts from that moment (D-0017).
- **After any gap in watching** (a restart, the computer asleep, the network down), read the
  robot's activity history again before showing the last visit (D-0018, D-0025).
- **Fail visibly.** On a login or API error show "no data" and keep retrying with backoff.
  Never leave an old state on screen as if it were current. If the display can mark its own
  picture as stale when updates stop, use that, so the warning does not depend on the program
  that failed (D-0004).
- **Whisker's API is unofficial.** One connection per account; follow the live updates and
  refresh every few minutes, no faster. If they run Home Assistant's Litter-Robot integration,
  consider reading the robot through Home Assistant instead of a second Whisker login.
- **Credentials** live only in an environment file that they fill in. Never read, print or log
  it.
- **Check every library function, API field or firmware setting** against the installed source
  or the official docs before using it, and note in a reference file what you checked and
  where. Tell them when something differs from this repository's notes.

How to build it:
- The same shape: source -> snapshot -> mapping -> display state -> render -> frame ->
  transport. Mapping and drawing are pure functions; only the transport knows the display's API.
- First a fake display they can open in a browser, with tests, so every screen can be designed
  and checked without the robot or the display. Show it to them before the real display.
- Colours, timings, place and jokes in one config file, set from their answers in step 1.
- Start with the screens that matter: idle (litter level, drawer level, time since the last
  visit), in use, the countdown to cleaning, cleaning, drawer full, and no data. Add the fun
  parts after those have got through a few real visits.

When it works, tell them how to run it, how to keep it running on their computer, and what you
could not test.
