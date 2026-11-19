# CLAUDE.md

@docs/STATUS.md

ProjectArthur is a small always-on display that shows what a Litter-Robot 4 is doing. (Arthur
is the cat.) A Python service, the **feeder**, watches the robot through Whisker's cloud,
decides what to show, draws the frame itself, and pushes it to a Ulanzi TC002 pixel clock
(52x16) running AWTRIX NG. The feeder will run on a Raspberry Pi 5. A browser **studio**
simulates the clock so screens can be designed without the hardware on the desk.

**If this is someone else's copy.** This file, STATUS.md and DECISIONS.md describe Kyle's clock
and Kyle's cat. In a copy someone cloned to run their own, the clock address, `.env` and
`config/display.toml` are theirs, STATUS.md's feeder "running on Kyle's Mac" is not on this
machine (check with `pgrep -fl arthur.feeder`), and Kyle's directions in the `design-screen`
skill are defaults the owner may change. The README is the setup guide for them.

## Read before working
- `docs/STATUS.md` (imported above): what is done and what is next. Current state only.
- `docs/DECISIONS.md`: why things are this way. Append-only.
- `docs/states.md`: every display state and the robot fact that drives it.
- `docs/reference/pylitterbot.md`, `docs/reference/awtrix-ng.md`: verified API notes.
- `docs/reference/petlibro.md`: PETLIBRO cloud API notes (food and water). Read from a
  third-party integration's source, not yet confirmed live.
- `docs/reference/open-meteo.md`: the weather API the idle show's page uses, confirmed live.
- `docs/research/`: the hardware and firmware comparison behind the decisions, and the LED
  animation and idle cadence research behind the idle show (D-0019).
- `docs/handoff.md`: the original brief. DECISIONS.md overrides it where they differ.

## Pipeline
`source → RobotSnapshot → mapping → DisplayState → render → Frame → transport → clock | simulator`

| Stage | Module | Rule |
|---|---|---|
| source | `arthur/sources/` | `whisker.py` is the live robot and writes `logs/spike-*.jsonl`; `logfile.py` reads that log for the studio; `tracker.py` keeps what the feeder observed over time (when a status began, when the cat left) for both. pylitterbot is imported only by `whisker.py` and `arthur/spikelog.py`. `weather.py` fetches Boston's weather from Open-Meteo for the idle show's page; it is a decoration, never robot data |
| mapping | `arthur/mapping.py`, `arthur/schedule.py` | Pure functions. Know nothing about displays or pixels. The schedule says which part of the idle show is on (home, page or scene) and mapping carries it on the state as a cue (D-0019) |
| render | `arthur/render/` | Pure function of DisplayState, time, config and size. Knows nothing about AWTRIX. `idle_show.py` plays a cue; `pages.py` holds the weather page; each scene is one file under `scenes/` |
| transport | `arthur/transports/` | Behind an interface. The only code that knows the clock's API |
| simulator | `arthur/sim/` | A fake clock that speaks the same API subset, plus the studio page |
| feeder | `arthur/feeder.py`, `arthur/pusher.py` | The run loop: 8 times a second, on the beat of the wall clock (`arthur/timebase.py`), it asks the sources, maps, renders and pushes when the frame changed or the heartbeat is due |

## Invariants
- **The feeder draws every pixel.** The clock only shows a bitmap (D-0003). Do not move layout
  into AWTRIX text, icon or progress keys.
- **One size: 52x16**, the Ulanzi TC002 (D-0010). The 32x8 layouts were removed on purpose; do
  not add a second size back without Kyle asking.
- **The idle show is decoration** (D-0019). Pages and scenes play only on idle, any real robot
  event replaces them at once, and a failure in the weather source or in drawing a page or
  scene must fall back to the home page, never stop the display.
- **One config file.** Colours, thresholds and timings live in `config/display.toml`.
- **Credentials** come from the environment or a gitignored `.env` that Kyle fills in himself.
  Never read `.env` or `.whisker-token.json`, never print or log their contents.
- **Whisker's API is unofficial.** One shared connection, so **only one of the feeder and
  `scripts/spike_log.py` may run on the account at a time**; poll no faster than every few
  minutes, and fail visibly: on any auth or API error show NO DATA and keep retrying. Never
  leave an old state on screen as if it were current.
- **Verify before you use.** Before using any pylitterbot or AWTRIX NG name that is not already
  in `docs/reference/`, read the installed source
  (`.venv/lib/python3.13/site-packages/pylitterbot/`) or the official docs, then add it to the
  reference file with where you found it. Say so when the docs or the handoff were wrong.
- **AWTRIX NG is pinned to 1.2.3** and rejects unknown keys with 422. The simulator does too.

## Commands
- `uv run pytest` and `uv run ruff check`
- `uv run python -m arthur.sim` starts the studio (also the "studio" entry in `.claude/launch.json`).
  Its Robot log source follows or replays the spike's `logs/spike-*.jsonl`
- `uv run python -m arthur.render.preview --all` prints every screen as text
- `uv run --env-file .env python -m arthur.feeder --clock http://ADDRESS` runs the feeder
  against the real robot and clock (needs Kyle's `.env`). It writes the same log the spike did.
  Check for a running one first: `pgrep -fl arthur.feeder`. To leave it running on the Mac,
  start it with `open -a Terminal scripts/start_feeder.command`, never detached from a
  session's shell: macOS cuts it off the clock when that session closes (D-0024)
- `uv run python scripts/push_test.py --clock http://ADDRESS NAME` shows one screen on a real
  clock; stop the feeder first or the two fight over the screen
- `uv run python scripts/push_bench.py --clock http://ADDRESS MODE` times frame pushes to a real
  clock and shows motion test patterns (`--help` lists the modes); stop the feeder first
- `uv run --env-file .env python scripts/spike_log.py` logs the real robot without a clock.
  Superseded by the feeder; never run both
- `uv run --env-file .env python scripts/spike_petlibro.py` logs the real PETLIBRO devices,
  read only (needs a second PETLIBRO account in Kyle's `.env`)

## Working here
- Changing what a screen looks like: use the `design-screen` skill. Look at the result (preview
  output or the studio in the browser pane) before calling a screen done.
- Ending a session: use the `wrap-up` skill so STATUS.md and DECISIONS.md stay true.
- Commits: one per finished step, on `main`. `origin` is the **public** GitHub repo
  `mcgaheyk/ProjectArthur`; push only when Kyle asks. This repo's git config commits with
  GitHub's no-reply address; never commit with Kyle's personal email.
- **Model routing. Applies only when this session runs on Fable** (your environment context
  states the model; on any other model this bullet is inert and you work directly). On Fable,
  keep the judgment work in the main loop: planning, design decisions, docs and review.
  **Delegate coding to Opus subagents**: Agent tool, `subagent_type: "general-purpose"`,
  `model: "opus"`. Coding means Python under `arthur/`, `scripts/` and `tests/`, and the
  studio's HTML, CSS and JS. Point the subagent at the docs it needs (this file,
  `docs/states.md`, the reference notes, the task's success criteria), then review its diff
  and run the checks before finishing the turn. A trivial edit of a few lines may be done
  directly. See D-0008.
