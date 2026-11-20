"""The idle show's player (D-0019): plays ``DisplayState.cue`` on the home page.

Mapping decides *what* plays and when its slot began (:mod:`arthur.schedule`); this module only
turns a cue and a moment into a frame. Everything runs on frames of the show's beat
(:data:`arthur.timebase.SHOW_FPS`, 8 a second): frame ``k`` of a cue is
``frames_since(cue.at, now)`` and is drawn at the start of its own 1/8 s, ``cue.at + k/8``, so
a frame depends only on its number and the state. A slot has
``N = round(cue.length_s * SHOW_FPS)`` frames; before and after it the plain home page shows.

**A page** (``cue.kind == "page"``)::

    [home -> page: dissolve, frames 0-5] [page body, frames 6 .. N-7] [page -> home: N-6 .. N-1]

Frame 0 is the home page and frame N-1 is the home page again. Pages are registered by name
with :func:`page`; a page is ``(state, now, cfg) -> Frame`` (52x16, border empty) and runs on
whole seconds like the home page. ``cue.name`` picks one; with no name it is ``"weather"``
(:mod:`arthur.render.pages`).

**A scene** (``cue.kind == "scene"``)::

    [glance: 8 frames, home page with Arthur's eyes right]
    [open: 5-frame wipe of the content columns from the home page to the stage]
    [body: N - 18 frames, drawn by the scene]
    [close: 5-frame wipe of the content columns from the stage back to the home page]

Scenes draw on a 50x14 **stage** that is blitted at (1, 1), so the outer border stays empty
by construction. Seated Arthur (:func:`draw_seated`) lands on exactly the home page's pixels,
which is what lets the wipes leave him in place. A scene is registered by name with
:func:`scene` and is a function from a :class:`SceneContext` to a sequence of :class:`Beat`;
it must end with Arthur seated at rest at his spot on an otherwise empty stage, because the
closing wipe starts from that. If its beats come up short the last frame is held; if they run
over the body, the extra frames are never shown (the tests forbid it).

Scenes live one file each in :mod:`arthur.render.scenes`, discovered by listing that folder
(MicroPython has no ``pkgutil``). Which scene plays: ``cue.name`` if set, else :func:`pick_scene` (a seeded shuffle bag over the
registered scenes, indexed by the round number, no scene twice in a row).

**Transitions** keep exact colours (no fades: a dimmed orange turns red on these LEDs): every
output pixel is a pixel of one of the two frames. :func:`dissolve`, :func:`wipe`.

**The strict text check** (``arthur.render.text``) only sees labels drawn into the frame that
``render`` finally returns. Every frame the show composes from (the home page, a page body, a
stage) is therefore drawn through :func:`checked`, which in strict mode checks that frame's
labels on that frame. A composed frame takes each pixel from one checked frame.
"""

from __future__ import annotations

import os
import sys
import zlib
from datetime import timedelta
from functools import cache
from typing import TYPE_CHECKING

from arthur.compat import Random, record, tuple_record
from arthur.render import screens
from arthur.render.frame import Frame
from arthur.render.text import check_text, is_strict, strict_text
from arthur.timebase import SHOW_FPS, frames_since

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from arthur.config import Config
    from arthur.model import Cue, DisplayState

    Page = Callable[[DisplayState, datetime, Config], Frame]
    Scene = Callable[["SceneContext"], Sequence["Beat"]]

# =========================================================================================
# Geometry and timing
# =========================================================================================

#: The frame (the TC002's 52x16).
W, H = screens.S52
#: The stage: everything inside the clock's 1-pixel stale border.
STAGE_X, STAGE_Y = 1, 1
STAGE_W, STAGE_H = W - 2, H - 2
#: The content columns the scene wipes cross: from the gap column after Arthur's tail
#: (19) to the right edge. Columns 0-18 (Arthur) never change in a wipe.
WIPE_X0, WIPE_X1 = screens.IDLE_X - 1, W
#: Last frame column Arthur's seated art (icon and long tail) may use.
SEATED_R = screens.IDLE_X - 2

#: Frames of each dissolve into and out of a page.
DISSOLVE_FRAMES = 6
#: Frames of Arthur glancing right before a scene opens.
GLANCE_FRAMES = 8
#: Frames of each wipe opening and closing a scene.
WIPE_FRAMES = 5
#: Frames of a scene slot that are not the scene's own body.
SCENE_FRAMES_AROUND = GLANCE_FRAMES + 2 * WIPE_FRAMES
#: The pose of the glance (and of the opening wipe), and the pose a scene must end in.
GLANCE_POSE = "right"
REST_POSE = "rest"
#: The page played when a page cue names none.
DEFAULT_PAGE = "weather_big"
#: Seed of the dissolve's pixel order (the push bench's, so its measurements still apply).
DISSOLVE_SEED = 52_16

_FRAME_US = 1_000_000 // SHOW_FPS


def slot_frames(cue: Cue) -> int:
    """Frames in the cue's slot: ``round(length_s * SHOW_FPS)``."""
    return round(cue.length_s * SHOW_FPS)


def frame_time(cue: Cue, k: int) -> datetime:
    """When frame ``k`` of the cue begins: ``cue.at + k / SHOW_FPS``."""
    return cue.at + timedelta(microseconds=k * _FRAME_US)


# =========================================================================================
# Strict text for composed frames
# =========================================================================================


def checked(draw: Callable[[], Frame]) -> Frame:
    """``draw()``, with its labels checked on the frame it returns when strict text is on.

    Use it for every frame the show composes from; see the module docstring.
    """
    if not is_strict():
        return draw()
    with strict_text() as records:
        f = draw()
        check_text(f, records)
    return f


# =========================================================================================
# Transitions and mirroring
# =========================================================================================


@cache
def _dissolve_order(n_pixels: int) -> tuple[int, ...]:
    order = list(range(n_pixels))
    Random(DISSOLVE_SEED).shuffle(order)
    return tuple(order)


def dissolve(a: Frame, b: Frame, i: int, n: int) -> Frame:
    """Step ``i`` of ``n`` from ``a`` to ``b``: ``a`` at 0, ``b`` at ``n``. Pixels switch in a
    fixed pseudo-random order, each exactly once; every pixel is ``a``'s or ``b``'s."""
    if a.size != b.size:
        raise ValueError(f"dissolve between {a.size} and {b.size}")
    i = max(0, min(i, n))
    order = _dissolve_order(len(a.pixels))
    out = a.copy()
    for p in order[: round(len(order) * i / n)] if n > 0 else order:
        out.pixels[p] = b.pixels[p]
    return out


def wipe(
    a: Frame, b: Frame, i: int, n: int, *, x0: int = 0, x1: int = W, from_left: bool = True
) -> Frame:
    """Step ``i`` of ``n``: a vertical edge crosses columns ``x0 .. x1 - 1``, ``b`` behind it.

    ``from_left`` moves the edge rightwards (``b`` fills in from ``x0``), otherwise leftwards
    (``b`` fills in from ``x1``). Columns outside the range are always ``a``'s, so the result
    is ``b`` at ``i = n`` only where the two agree outside it.
    """
    if a.size != b.size:
        raise ValueError(f"wipe between {a.size} and {b.size}")
    x0, x1 = max(0, x0), min(a.width, x1)
    i = max(0, min(i, n))
    span = max(0, x1 - x0)
    k = round(span * i / n) if n > 0 else span
    cols = range(x0, x0 + k) if from_left else range(x1 - k, x1)
    out = a.copy()
    w = a.width
    for y in range(a.height):
        for x in cols:
            out.pixels[y * w + x] = b.pixels[y * w + x]
    return out


def mirror(rows: Sequence[str]) -> tuple[str, ...]:
    """Text art flipped left to right (a cat facing right faces left)."""
    return tuple("".join(reversed(r)) for r in rows)  # not r[::-1]: not on MicroPython


def mirror_frame(f: Frame) -> Frame:
    """A frame flipped left to right."""
    out = Frame(f.width, f.height)
    for y in range(f.height):
        row = f.pixels[y * f.width : (y + 1) * f.width]
        out.pixels[y * f.width : (y + 1) * f.width] = row[::-1]
    return out


# =========================================================================================
# The stage
# =========================================================================================


def new_stage() -> Frame:
    """An empty 50x14 stage."""
    return Frame(STAGE_W, STAGE_H)


def stage_frame(stage: Frame) -> Frame:
    """The 52x16 frame with ``stage`` at (1, 1) and the border empty."""
    if stage.size != (STAGE_W, STAGE_H):
        raise ValueError(f"a stage is {STAGE_W}x{STAGE_H}, not {stage.width}x{stage.height}")
    f = Frame(W, H)
    f.blit(stage, STAGE_X, STAGE_Y)
    return f


def draw_seated(stage: Frame, cfg: Config, pose: str = REST_POSE, dx: int = 0) -> None:
    """Seated Arthur in ``pose`` (a key of ``screens.IDLE_POSES``) at his home spot on the
    stage, shifted ``dx`` columns. At ``dx = 0`` he is exactly the home page's Arthur."""
    screens.draw_idle_arthur(stage, cfg, pose, x=dx - STAGE_X, y=-STAGE_Y)


def seated_stage(cfg: Config, pose: str = REST_POSE) -> Frame:
    """A stage with only seated Arthur in ``pose``."""
    s = new_stage()
    draw_seated(s, cfg, pose)
    return s


# =========================================================================================
# Registries: pages and scenes
# =========================================================================================

#: Page name -> page function.
PAGES: dict[str, Page] = {}


def page(name: str) -> Callable[[Page], Page]:
    """Register a page under ``name``. A page is ``(state, now, cfg) -> Frame``: 52x16, the
    border empty, labels through ``put_text``; ``state.weather`` may be None."""

    def deco(fn: Page) -> Page:
        PAGES[name] = fn
        return fn

    return deco


@tuple_record("frames draw")
class Beat:
    """``frames`` frames drawn by ``draw(stage, i)``, ``i`` counting 0 .. frames-1 inside the
    beat. ``draw`` gets an empty stage each frame."""

    frames: int
    draw: Callable[[Frame, int], None]


@record("name cfg n frames")
class SceneContext:
    """What a scene is given: the config, the round number ``n``, how many body ``frames``
    its beats must fill, and helpers seeded by scene name, round and a key of your choosing,
    so a scene varies from round to round but is the same every time for the same round."""

    name: str
    cfg: Config
    n: int
    frames: int

    def seed(self, key: str) -> int:
        return zlib.crc32(f"{self.name}|{self.n}|{key}".encode())

    def rng(self, key: str) -> Random:
        """A ``random.Random`` for ``key``, the same for the same scene, round and key."""
        return Random(self.seed(key))

    def pick(self, key: str, options: int | Sequence[object]) -> int:
        """An index into ``options`` (a sequence, or a count) for ``key``."""
        count = options if isinstance(options, int) else len(options)
        if count <= 0:
            raise ValueError("pick needs at least one option")
        return self.seed(key) % count


@record("fn weight")
class SceneEntry:
    fn: Scene
    weight: int = 1


#: Scene name -> its function and weight (copies of it in each shuffle bag).
SCENES: dict[str, SceneEntry] = {}


def scene(name: str, *, weight: int = 1) -> Callable[[Scene], Scene]:
    """Register a scene under ``name``; ``weight`` is how many times it is in each bag."""
    if weight < 1:
        raise ValueError("a scene's weight is a whole number, 1 or more")

    def deco(fn: Scene) -> Scene:
        SCENES[name] = SceneEntry(fn, int(weight))
        return fn

    return deco


# =========================================================================================
# Which scene plays: a seeded shuffle bag
# =========================================================================================


def _bag_items(weights: Mapping[str, int]) -> list[str]:
    return [name for name in sorted(weights) for _ in range(max(1, int(weights[name])))]


def _bag_rng(b: int, items: Sequence[str], key: str) -> Random:
    return Random(zlib.crc32(f"bag|{key}|{b}|{','.join(items)}".encode()))


def _bag_last(b: int, items: Sequence[str]) -> str:
    """The last scene of bag ``b``: chosen from the bag's seed alone, so the next bag can
    avoid it without knowing anything else about this one."""
    return _bag_rng(b, items, "last").choice(list(items))


def _arrange(rest: list[str], first_not: str, last: str, rng: Random) -> list[str] | None:
    """``rest`` ordered so that no two neighbours match, the first is not ``first_not`` and
    the one before ``last`` is not ``last``; None if there is no such order."""
    order: list[str] = []
    left = list(rest)

    def ok_next(c: str) -> bool:
        prev = order[-1] if order else first_not
        if c == prev:
            return False
        return not (len(order) == len(rest) - 1 and c == last)

    def search() -> bool:
        if not left:
            return True
        tried: set[str] = set()
        for c in rng.sample(left, len(left)):
            if c in tried or not ok_next(c):
                continue
            tried.add(c)
            left.remove(c)
            order.append(c)
            if search():
                return True
            order.pop()
            left.append(c)
        return False

    return order if search() else None


def _bag(b: int, items: Sequence[str]) -> list[str]:
    distinct = sorted(set(items))
    if len(distinct) == 1:
        return list(items)
    if len(items) == 2:  # two scenes once each: the only order with no repeat alternates
        return list(items)
    last = _bag_last(b, items)
    rest = list(items)
    rest.remove(last)
    order = _arrange(rest, _bag_last(b - 1, items), last, _bag_rng(b, items, "order"))
    if order is None:  # the weights make a repeat unavoidable: a plain shuffle
        shuffled = list(items)
        _bag_rng(b, items, "order").shuffle(shuffled)
        return shuffled
    return order + [last]


def pick_scene(n: int, weights: Mapping[str, int] | None = None) -> str:
    """The scene for round ``n``: position ``n % B`` of bag ``n // B`` (``B`` = total
    weight), each bag a seeded shuffle with no scene twice in a row, also across bags
    (unless the weights make that impossible). ``weights`` defaults to the registry."""
    if weights is None:
        weights = {name: e.weight for name, e in SCENES.items()}
    if not weights:
        raise LookupError("no scenes are registered")
    items = _bag_items(weights)
    b, pos = divmod(n, len(items))
    return _bag(b, items)[pos]


# =========================================================================================
# Playing a cue
# =========================================================================================


def _home(state: DisplayState, now: datetime, cfg: Config, pose: str | None = None) -> Frame:
    return checked(lambda: screens.home_page(state, now, cfg, pose))


def scene_beats(name: str, cfg: Config, n: int, frames: int) -> Sequence[Beat]:
    """The beats scene ``name`` plays in round ``n`` to fill ``frames`` body frames."""
    return SCENES[name].fn(SceneContext(name, cfg, n, frames))


def beats_length(beats: Sequence[Beat]) -> int:
    return sum(max(0, b.frames) for b in beats)


def body_stage(beats: Sequence[Beat], j: int) -> Frame:
    """The stage for body frame ``j``: the beat it falls in, or the last frame held when the
    beats come up short."""
    stage = new_stage()
    real = [b for b in beats if b.frames > 0]
    if not real:
        return stage
    for b in real:
        if j < b.frames:
            b.draw(stage, j)
            return stage
        j -= b.frames
    last = real[-1]
    last.draw(stage, last.frames - 1)
    return stage


def part_of(cue: Cue, k: int) -> str:
    """Which part of the slot frame ``k`` is in (``"home"`` outside it)."""
    n = slot_frames(cue)
    if not 0 <= k < n:
        return "home"
    if cue.kind == "page":
        if k < DISSOLVE_FRAMES:
            return "dissolve_in"
        if k >= n - DISSOLVE_FRAMES:
            return "dissolve_out"
        return "page"
    if n < SCENE_FRAMES_AROUND:
        return "home"
    if k < GLANCE_FRAMES:
        return "glance"
    if k < GLANCE_FRAMES + WIPE_FRAMES:
        return "open"
    if k >= n - WIPE_FRAMES:
        return "close"
    return "body"


def _play_page(state: DisplayState, cue: Cue, k: int, cfg: Config) -> Frame:
    n = slot_frames(cue)
    fn = PAGES.get(cue.name or DEFAULT_PAGE)
    ft = frame_time(cue, k)
    if fn is None:
        return _home(state, ft, cfg)
    body = checked(lambda: fn(state, ft, cfg))
    part = part_of(cue, k)
    if part == "page":
        return body
    home = _home(state, ft, cfg)
    if part == "dissolve_in":
        return dissolve(home, body, k, DISSOLVE_FRAMES)
    return dissolve(body, home, k - (n - DISSOLVE_FRAMES) + 1, DISSOLVE_FRAMES)


def scene_name(cue: Cue) -> str | None:
    """The scene a scene cue plays: its pinned name, else the bag's pick; None if there is
    no such scene (or none at all)."""
    if cue.name is not None:
        return cue.name if cue.name in SCENES else None
    return pick_scene(cue.n) if SCENES else None


def _play_scene(state: DisplayState, cue: Cue, k: int, cfg: Config) -> Frame:
    n = slot_frames(cue)
    ft = frame_time(cue, k)
    name = scene_name(cue)
    part = part_of(cue, k)
    if name is None or part == "home":
        return _home(state, ft, cfg)
    if part == "glance":
        return _home(state, ft, cfg, GLANCE_POSE)
    if part == "open":
        i = k - GLANCE_FRAMES + 1
        return wipe(_home(state, ft, cfg, GLANCE_POSE),
                    stage_frame(seated_stage(cfg, GLANCE_POSE)), i, WIPE_FRAMES,
                    x0=WIPE_X0, x1=WIPE_X1)
    if part == "close":
        i = k - (n - WIPE_FRAMES) + 1
        return wipe(stage_frame(seated_stage(cfg, REST_POSE)), _home(state, ft, cfg, REST_POSE),
                    i, WIPE_FRAMES, x0=WIPE_X0, x1=WIPE_X1, from_left=False)
    body_n = n - SCENE_FRAMES_AROUND
    beats = scene_beats(name, cfg, cue.n, body_n)
    j = k - GLANCE_FRAMES - WIPE_FRAMES
    return stage_frame(checked(lambda: body_stage(beats, j)))


def play(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    """The frame for ``state.cue`` at ``now``: the plain home page outside its slot."""
    cue = state.cue
    if cue is None:
        return screens.home_page(state, now, cfg)
    k = frames_since(cue.at, now)
    if not 0 <= k < slot_frames(cue):
        return screens.home_page(state, now, cfg)
    if cue.kind == "page":
        return _play_page(state, cue, k, cfg)
    return _play_scene(state, cue, k, cfg)


# =========================================================================================
# Discovery
# =========================================================================================


def scene_modules() -> list[str]:
    """The scene modules in :mod:`arthur.render.scenes`, in name order: every ``.py`` (or
    MicroPython's compiled ``.mpy``) whose name does not start with ``_``. Listed from the
    folder, as ``pkgutil.iter_modules`` would (MicroPython has no ``pkgutil``)."""
    __import__("arthur.render.scenes")
    # From sys.modules: while arthur.render is still importing, MicroPython has not yet set
    # it as an attribute of arthur, so ``import arthur.render.scenes as pkg`` fails there.
    path = sys.modules["arthur.render.scenes"].__file__
    folder = path[: path.rfind("/")] if "/" in path else "."
    names = set()
    for f in os.listdir(folder):
        for suffix in (".py", ".mpy"):
            if f.endswith(suffix) and not f.startswith("_"):
                names.add(f[: -len(suffix)])
    return sorted(names)


def load_plugins() -> None:
    """Import the pages module and every module in :mod:`arthur.render.scenes`."""
    __import__("arthur.render.pages")
    for name in scene_modules():
        __import__("arthur.render.scenes." + name)


load_plugins()
screens.CUE_PLAYER = play
