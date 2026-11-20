# MicroPython reference

How MicroPython 1.29 differs from CPython 3.13 for the code the clock is to run (D-0029).
Collected 2026-11-20 on Kyle's Mac (Apple Silicon) with MicroPython 1.29.0 from Homebrew
(the unix port, `standard` variant, 64-bit) and CPython 3.13.18, while making the shared
modules run on both. Items marked **[guard]** were found or are now caught by the pixel
comparison in `tests/test_micropython.py`; **[probe]** were seen in a small script run under
both; **[spike]** come from `spike/mpy/RESULTS-2A.md` on the local branch
`spike/micropython-slice` and were seen again here. **Nothing here ran on the clock**, whose
build is 32-bit ARM with its own libm; the spike's on-clock run is in
`docs/research/2026-11-20-no-pi-two-tests.md`.

## Which code, and the check
Shared, so it must run unchanged on both: `arthur/compat.py`, `model`, `config`,
`timebase`, `schedule`, `mapping`, `zonetable`, `mtrandom`, `sources/tracker`,
`sources/records`, `sources/spikeformat`, `sources/wire_source` (and `arthur/wire/`) and all
of `render/` except `preview.py`. Everything else (`feeder`, `pusher`, `transports/`, the
pylitterbot and weather sources, `logfile`, `spikelog`, `sim/`, `fixtures`, `tools/`,
scripts) is CPython only and may use anything; a shared module must not import one of those
(`wire_source` imports `spikelog` only inside code that runs on CPython, for the daily log
files and the token file). `tests/test_wire_source_micropython.py` runs the live source
under both Pythons against the fake Whisker (below, "The live source").

`uv run pytest tests/test_micropython.py` runs `mpy/guard.py` under `micropython -X
heapsize=2M` and under CPython over about 240 cases (every golden and scene strip, every
fixture in every variant over its animation, the scenes in both endings, the weather pages,
all scenarios mapped and drawn, the real robot's log through the tracker and mapping, the
config, generator and time zone) and compares them line by line; it takes about 45 s. It
skips, saying the guard did not run, without `micropython` on PATH. Changing a shared module
is not done until it passes: most of the differences below change pixels without an error.

MicroPython gets the stdlib stand-ins in `mpy/lib/` on `sys.path` (`__future__`, `typing`,
`functools`, `contextlib`, `contextvars`, `types`, `zlib`, `pathlib`, and micropython-lib's
`datetime` 4.1.0 with `UTC` added); CPython never sees them. What the shared code itself
imports for the rest is in `arthur/compat.py`, which on CPython is CPython's own thing.

## Differences that change results without an error
- **Dicts and sets do not keep insertion order** [spike] [probe]: `list(d)` and `list(s)`
  come out in hash order. Wherever order matters use `collections.OrderedDict` (the layout
  registry, whose first variant is the default; the weather glyph table) or sort. Three
  short keys happened to come out in order in a probe, so the guard does not fail if the
  layout registry is a plain dict today; it is an OrderedDict anyway.
- **`sorted()` and `list.sort()` are not stable** [spike]: equal keys come out in any order.
  The hourglass's sand changed in 28 frames. Use `compat.stable_sorted` where keys can tie.
- **`isinstance(True, int)` is False** [spike] [probe]. Test for `bool` first, as the
  config and tracker already do.
- **`round(x, n)` with digits is not correctly rounded** [probe]: `round(2.675, 2)` is 2.68
  (CPython 2.67). `round(x)` without digits is banker's rounding on both. The rat scene's
  `round(squeeze + 0.1, 1)` gives the same values for every squeeze it uses [guard].
- **`sum()` of floats is plain addition** [probe]: CPython 3.12+ compensates
  (`sum([0.1] * 10)` is 1.0 there, 0.9999999999999999 here). The shared code sums only ints.
- **Float to text is not the shortest round trip** [probe]: `repr(10 / 3)` is
  `3.3333333333333336` (CPython `3.3333333333333335`; same double). Never turn a float into
  text that reaches the screen or a decision; `compat.decimal_ratio` reads a config
  percentage through 15 significant digits instead of `Fraction(str(x))`.
- **`hash()` values differ** [probe] (`hash(-1)`, `hash(1.5)`). Never seed or order by one.
- **No `random.Random`, and its own `random` gives other numbers** [spike]. The spike's own
  generator changed 306 frames. `arthur/mtrandom.py` is CPython's generator in pure Python;
  `tests/test_mtrandom.py` proves it equal to `random.Random` on CPython over 424 seeds and
  every method the code calls, and the guard runs the same exercise under MicroPython
  [guard]. Seeding costs 0.32 ms here, so one generator per key per scene play, not per frame.
- **A folder `x/` next to `x.py` is imported in place of the module** [spike]: the art
  folder is `render/icon_art/`, not `render/icons/`. `tests/test_portable.py` checks.
- **micropython-lib's `datetime` raises on `dt == None`** (AttributeError; CPython says
  False). Fixed in the stand-in; `compat.record`'s `==` also never compares a field with None.
- **`re` compiles what it cannot do, and then does not match** [probe]: `\d{1,3}` matches
  neither `12` nor `1234` (a counted repetition is taken as the literal text `{1,3}`: `a{2}`
  matches `"a{2}"`), and `\b` never matches. There is no `re.IGNORECASE`. So the log's
  token scrubbing and redaction (`sources/spikeformat.py`) are hand-written scanners on
  MicroPython; on CPython the regular expressions stay, and `tests/test_spikeformat.py`
  checks the two agree over the real logs and 8,000 made-up strings.
- **String methods are ASCII-only** [probe]: `"é".isalpha()` and `" ".isspace()` are
  False. The scanners above treat non-ASCII letters and spaces as CPython's `\w` and `\s`
  would only on CPython; robot data and error texts are ASCII.
- **`json.dumps` prints floats with MicroPython's `repr`** and a dict in hash order. Log
  lines go through `spikeformat.dumps`, whose floats are `arthur.wire.util.float_text`; the
  keys of a line come out in a different order than on CPython (readers parse the JSON, so
  nothing depends on it).

## Differences that raise
- No `dataclasses` and no class-body annotations at run time [spike]: use
  `compat.record("a b c")` over the class (a frozen dataclass on CPython, where the names are
  checked against the annotations). No class-form `NamedTuple`: use `compat.tuple_record`.
  On MicroPython a tuple record is made by a factory function, because a namedtuple class
  there has no `__new__` to call (AttributeError; the spike saw `super().__new__` crash), so
  `isinstance` against it does not work. Both build `__init__` with `exec`, which the clock's
  build must keep (`MICROPY_PY_BUILTINS_EVAL_EXEC`, on in the unix port).
- No star displays: `(*a, b)`, `[*a, b]`, `{**d}` are syntax errors [spike]. Calls with
  `f(*a, b)` and `f(**d, k=1)` work [probe].
- No `zip(strict=True)` (`compat.zip_strict`), no `str[::-1]` (only lists take a step;
  use `"".join(reversed(s))`), no `str.removeprefix`, no `\N{...}` escapes, no
  `int.bit_length` [spike] [probe].
- Run-time type aliases fail (`tuple[int, int]`, `X | Y` of classes, `Callable[...]`):
  keep them under `if TYPE_CHECKING:` [spike]. `frame.RGB` stays a run-time alias on CPython,
  where CPython-only code imports it.
- No `collections.abc`, `typing`, `functools`, `fractions`, `importlib`, `pkgutil`,
  `zoneinfo`, `tomllib`, `os.path` [spike] [probe]. A stand-in package for
  `collections.abc` would hide the built-in `collections`, so those names are imported only
  under `TYPE_CHECKING`. The scenes are found by listing their folder
  (`idle_show.scene_modules`); local time goes through `Config` (`local`, `at_local_hour`)
  and a `zonetable.TableZone` built from the exported config; the config comes from
  `python -m arthur.tools.export_config`.
- `import a.b.c as x` while `a.b` is still importing fails (the attribute is not set on `a`
  yet): take the module from `sys.modules` [guard].

## Memory
The unix port's heap is fixed (`-X heapsize`) and MicroPython cannot move objects, so a
heap with plenty free can fail a request for a few kilobytes in one piece [guard].
- Compiling the shared modules from source needs the biggest blocks of a run: import them
  first, before anything else (the clock should load compiled `.mpy` files instead).
- Reading the large font whole needed a **2 MB heap**: 75 KB and 128 KB in one piece for the
  text and its 9,000 lines, then the 731-glyph table grown on a heap the parse had cut up.
  `font.load_bdf` now reads the codes, sizes the table, parses a line at a time and collects
  as it goes on MicroPython; the font loads in 768 KB and keeps about 170 KB.
- After the imports the shared code holds about 270 KB; about 340 KB after drawing the home
  page and the three scenes (small font only), about 400 KB with the large font loaded. A
  54-second scene's kept plan is the biggest thing a frame adds.
- **The whole guard's drawing (every fixture, scene, page and scenario) runs in a 1.1 MB
  heap and fails in 1 MB** (fragmentation after the 54-second bird play), loading the fonts
  first. These are 64-bit numbers; the clock is 32-bit, where pointers and small objects
  are half the size. The spike's slice (no large font) ran there in a 1 MiB heap.
- **The live source** (`sources/wire_source.py` on `arthur/wire/`), through a scripted hour
  against the fake Whisker (three visits pushed live with their history entries, a poll
  every five minutes, the heartbeats; `tests/test_wire_source_micropython.py`, 2026-11-20):
  213 KB in use after importing it from source (config, model, tracker, client and source
  together), 277 KB once the source is built and the scenario loaded, 288 KB at the end of
  the hour after a collection. **The hour runs in a 512 KB heap and fails in 480 KB**, its
  log printed as it is written. Keeping the log in memory instead (`MemorySink`) adds about
  60 KB for that hour's 22 state lines and history (a state line is 1.5 to 3 KB of text), so
  the default ring keeps 100 lines. Not measured with the drawing code loaded beside it.

## Speed on the Mac
`micropython -X heapsize=1M mpy/bench.py OUT.json` against `uv run python mpy/bench.py
OUT.json`, ms a frame, 8 frames a second, one play each with the plan kept (2026-11-20):

| | CPython mean / worst | MicroPython mean / worst | plan frame CPython / MicroPython |
|---|---|---|---|
| home, 60 s | 0.105 / 0.171 | 0.604 / 1.727 | — |
| bird, 36 s | 0.065 / 0.189 | 0.440 / 1.446 | 0.574 / 3.095 |
| rat, 36 s | 0.060 / 0.185 | 0.404 / 1.415 | 0.446 / 5.755 |
| stretch, 36 s | 0.050 / 0.185 | 0.336 / 1.260 | 0.364 / 2.198 |

"Worst" excludes the frame that builds the plan (the 14th, the first of the body). The clock
was 25 to 30 times slower than this Mac under MicroPython in the spike, which would put the
rat's plan frame near 150 to 170 ms, over the 125 ms beat; not measured on the clock.

## Not checked
- The clock's own float and libm results: `sin`, `atan2`, `radians` and float `%` were
  bit-identical between the two Pythons here because both use the Mac's libm [probe]. The
  drum, the hop and the sampling's `math.log` depend on them on the clock.
- MicroPython's float parser: 20,000 three-decimal values parsed bit-identically [probe];
  the guard passes floats by their bits, so weather JSON parsed on the clock is not covered.
- Frozen-ness: records are not frozen on MicroPython (assignment to a field succeeds).
