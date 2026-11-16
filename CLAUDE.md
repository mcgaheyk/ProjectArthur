# CLAUDE.md

@docs/STATUS.md

ProjectArthur is a small always-on display that shows what a Litter-Robot 4 is doing. (Arthur
is the cat.) A Python service, the **feeder**, watches the robot through Whisker's cloud,
decides what to show, draws the frame itself, and pushes it to a Ulanzi TC002 pixel clock
(52x16) running AWTRIX NG. The feeder will run on a Raspberry Pi 5. A browser **studio**
simulates the clock so screens can be designed without the hardware on the desk.

## Read before working
- `docs/STATUS.md` (imported above): what is done and what is next. Current state only.
- `docs/DECISIONS.md`: why things are this way. Append-only.
- `docs/states.md`: every display state and the robot fact that drives it.
- `docs/reference/pylitterbot.md`, `docs/reference/awtrix-ng.md`: verified API notes.
- `docs/reference/petlibro.md`: PETLIBRO cloud API notes (food and water). Read from a
  third-party integration's source, not yet confirmed live.
- `docs/research/`: the hardware and firmware comparison behind the decisions.
- `docs/handoff.md`: the original brief. DECISIONS.md overrides it where they differ.

## Pipeline
`source → RobotSnapshot → mapping → DisplayState → render → Frame → transport → clock | simulator`

| Stage | Module | Rule |
|---|---|---|
| source | `arthur/sources/` (not built yet) | The only code that imports pylitterbot |
| mapping | `arthur/mapping.py` | Pure function. Knows nothing about displays or pixels |
| render | `arthur/render/` | Pure function of DisplayState, time, config and size. Knows nothing about AWTRIX |
| transport | `arthur/transports/` | Behind an interface. The only code that knows the clock's API |
| simulator | `arthur/sim/` | A fake clock that speaks the same API subset, plus the studio page |

## Invariants
- **The feeder draws every pixel.** The clock only shows a bitmap (D-0003). Do not move layout
  into AWTRIX text, icon or progress keys.
- **One size: 52x16**, the Ulanzi TC002 (D-0010). The 32x8 layouts were removed on purpose; do
  not add a second size back without Kyle asking.
- **One config file.** Colours, thresholds and timings live in `config/display.toml`.
- **Credentials** come from the environment or a gitignored `.env` that Kyle fills in himself.
  Never read `.env` or `.whisker-token.json`, never print or log their contents.
- **Whisker's API is unofficial.** One shared connection, poll no faster than every few
  minutes, and fail visibly: on any auth or API error show NO DATA and keep retrying. Never
  leave an old state on screen as if it were current.
- **Verify before you use.** Before using any pylitterbot or AWTRIX NG name that is not already
  in `docs/reference/`, read the installed source
  (`.venv/lib/python3.13/site-packages/pylitterbot/`) or the official docs, then add it to the
  reference file with where you found it. Say so when the docs or the handoff were wrong.
- **AWTRIX NG is pinned to 1.2.3** and rejects unknown keys with 422. The simulator does too.

## Commands
- `uv run pytest` and `uv run ruff check`
- `uv run python -m arthur.sim` starts the studio (also the "studio" entry in `.claude/launch.json`)
- `uv run python -m arthur.render.preview --all` prints every screen as text
- `uv run --env-file .env python scripts/spike_log.py` logs the real robot (needs Kyle's `.env`)
- `uv run --env-file .env python scripts/spike_petlibro.py` logs the real PETLIBRO devices,
  read only (needs a second PETLIBRO account in Kyle's `.env`)

## Working here
- Changing what a screen looks like: use the `design-screen` skill. Look at the result (preview
  output or the studio in the browser pane) before calling a screen done.
- Ending a session: use the `wrap-up` skill so STATUS.md and DECISIONS.md stay true.
- Commits: one per finished step, on `main`. Nothing is pushed.
- **Model routing. Applies only when this session runs on Fable** (your environment context
  states the model; on any other model this bullet is inert and you work directly). On Fable,
  keep the judgment work in the main loop: planning, design decisions, docs and review.
  **Delegate coding to Opus subagents**: Agent tool, `subagent_type: "general-purpose"`,
  `model: "opus"`. Coding means Python under `arthur/`, `scripts/` and `tests/`, and the
  studio's HTML, CSS and JS. Point the subagent at the docs it needs (this file,
  `docs/states.md`, the reference notes, the task's success criteria), then review its diff
  and run the checks before finishing the turn. A trivial edit of a few lines may be done
  directly. See D-0008.
