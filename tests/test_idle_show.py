"""The idle show's player (arthur/render/idle_show.py), its pages and its scenes (D-0019)."""

import itertools
import os
import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW
from arthur.model import Cue
from arthur.pushbench import TRANSITIONS
from arthur.render import Frame, render
from arthur.render import idle_show as show
from arthur.render.pages import MONTHS, date_frame, date_text, weather_page
from arthur.render.preview import frame_text, legend_text
from arthur.render.preview import main as preview_main
from arthur.render.screens import IDLE_POSES, home_page
from arthur.render.text import check_text, strict_text
from arthur.timebase import SHOW_FPS

CFG = default_config()
OFF = CFG.rgb("off")
PALETTE = set(CFG.palette.colors.values())
READY = FIXTURES["ready"].display_state(CFG)
FRAME_US = 1_000_000 // SHOW_FPS
SLOT_LENGTHS = (24, 36, 54)
ROUNDS = (0, 1, 98_765)
GOLDEN = Path(__file__).parent / "golden" / "strips"
UPDATE = os.environ.get("ARTHUR_UPDATE_GOLDEN") == "1"

#: Changed frames allowed in one 36-second scene slot (288 frames). The rat scene
#: (tests/test_scene_rat.py) measured 105-122: its quiet part moves in small darts and
#: twitches (about 40 changes), the burst changes on nearly every frame while something
#: crosses the stage (about 45), the settle's walk and sit about 20, and the glance and wipes
#: 11. The bird scene (tests/test_scene_bird.py) measured 173-186 over rounds 0-11: its bird
#: beats its wings every 2 frames all the time it is in the air. Raise this on purpose.
CHANGED_FRAME_BUDGET = 200


def at(cue: Cue, k: int, us: int = 0) -> datetime:
    return cue.at + timedelta(microseconds=k * FRAME_US + us)


def play(cue: Cue, k: int, us: int = 0, state=READY) -> Frame:
    return render(replace(state, cue=cue), at(cue, k, us), CFG, variant="arthur")


def scene_cue(name: str | None, n: int = 0, length: float = 36) -> Cue:
    return Cue(kind="scene", at=NOW, length_s=float(length), n=n, name=name)


def page_cue(name: str | None = "weather", length: float = 54) -> Cue:
    return Cue(kind="page", at=NOW, length_s=float(length), n=3, name=name)


def border_empty(f: Frame) -> bool:
    w, h = f.size
    return all(f.get(x, y) == OFF for x in range(w) for y in (0, h - 1)) and all(
        f.get(x, y) == OFF for x in (0, w - 1) for y in range(h))


def two_frames(seed: int = 1) -> tuple[Frame, Frame]:
    """Two frames that differ in every pixel."""
    rng = random.Random(seed)
    colours = sorted(PALETTE - {OFF})
    a, b = Frame(52, 16), Frame(52, 16)
    for i in range(len(a.pixels)):
        ca, cb = rng.sample(colours, 2)
        a.pixels[i], b.pixels[i] = ca, cb
    return a, b


# --- with no cue, nothing changes ----------------------------------------------------------


def test_no_cue_is_the_home_page():
    for dt in range(0, 600, 7):
        now = NOW + timedelta(seconds=dt)
        assert render(READY, now, CFG, variant="arthur") == home_page(READY, now, CFG)


def test_a_forced_pose_changes_only_arthur():
    plain = home_page(READY, NOW, CFG, "rest")
    glance = home_page(READY, NOW, CFG, "right")
    assert plain != glance
    assert all(plain.get(x, y) == glance.get(x, y) for x in range(19, 52) for y in range(16))


# --- transitions -----------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 5, 6, 8])
def test_dissolve_ends_and_flips_each_pixel_once(n):
    a, b = two_frames()
    assert show.dissolve(a, b, 0, n) == a
    assert show.dissolve(a, b, n, n) == b
    taken: set[int] = set()
    for i in range(n + 1):
        f = show.dissolve(a, b, i, n)
        from_b = {p for p, c in enumerate(f.pixels) if c == b.pixels[p]}
        assert all(c in (a.pixels[p], b.pixels[p]) for p, c in enumerate(f.pixels))
        assert taken <= from_b  # a pixel that switched never switches back
        taken = from_b
    assert len(taken) == len(a.pixels)


def test_dissolve_order_is_fixed_and_scattered():
    a, b = two_frames()
    half = show.dissolve(a, b, 3, 6)
    assert half == show.dissolve(a, b, 3, 6)
    cols = {p % 52 for p, c in enumerate(half.pixels) if c == b.pixels[p]}
    assert len(cols) > 40  # not a wipe in disguise


@pytest.mark.parametrize("from_left", [True, False])
@pytest.mark.parametrize(("x0", "x1"), [(0, 52), (19, 52), (10, 30)])
def test_wipe_touches_only_its_columns(x0, x1, from_left):
    a, b = two_frames(2)
    n = 5
    prev_cols: set[int] = set()
    for i in range(n + 1):
        f = show.wipe(a, b, i, n, x0=x0, x1=x1, from_left=from_left)
        cols = {x for x in range(52) if f.get(x, 0) == b.get(x, 0)}
        assert all(f.get(x, y) == (b if x in cols else a).get(x, y)
                   for x in range(52) for y in range(16))  # whole columns, from a or b
        assert cols <= set(range(x0, x1))
        assert prev_cols <= cols
        if cols:  # a vertical edge: one run of columns, on the side it comes from
            assert (min(cols) == x0) if from_left else (max(cols) == x1 - 1)
            assert cols == set(range(min(cols), max(cols) + 1))
        prev_cols = cols
    assert show.wipe(a, b, 0, n, x0=x0, x1=x1) == a
    assert prev_cols == set(range(x0, x1))
    assert show.wipe(a, b, n, n) == b


def test_mirror():
    art = ("AB.", "..C")
    assert show.mirror(art) == (".BA", "C..")
    assert show.mirror(show.mirror(art)) == art
    a, _ = two_frames()
    assert show.mirror_frame(a).get(0, 3) == a.get(51, 3)
    assert show.mirror_frame(show.mirror_frame(a)) == a


def test_push_bench_uses_the_engines_transitions():
    assert TRANSITIONS["dissolve"] is show.dissolve
    assert TRANSITIONS["wipe"] is show.wipe


# --- the stage -------------------------------------------------------------------------------


@pytest.mark.parametrize("pose", list(IDLE_POSES))
def test_seated_arthur_on_the_stage_is_the_home_pages_arthur(pose):
    staged = show.stage_frame(show.seated_stage(CFG, pose))
    home = home_page(READY, NOW, CFG, pose)
    assert all(staged.get(x, y) == home.get(x, y)
               for x in range(show.SEATED_R + 1) for y in range(16)), pose
    assert all(staged.get(x, y) == OFF for x in range(show.SEATED_R + 1, 52) for y in range(16))
    assert any(staged.get(x, y) != OFF for x in range(16, show.SEATED_R + 1) for y in range(16))


def test_stage_keeps_the_border_empty():
    s = show.new_stage()
    s.fill(CFG.rgb("fur"))
    f = show.stage_frame(s)
    assert border_empty(f)
    assert f.get(1, 1) == f.get(50, 14) == CFG.rgb("fur")


# --- scenes ----------------------------------------------------------------------------------


def body_frames(cue: Cue) -> int:
    return show.slot_frames(cue) - show.SCENE_FRAMES_AROUND


def test_both_placeholders_are_registered():
    assert {"rat", "bird", "stretch"} <= set(show.SCENES)


@pytest.mark.parametrize("length", SLOT_LENGTHS)
@pytest.mark.parametrize("n", ROUNDS)
@pytest.mark.parametrize("name", sorted(show.SCENES))
def test_scene_plays_cleanly(name, n, length):
    cue = scene_cue(name, n, length)
    big_n = show.slot_frames(cue)
    body = body_frames(cue)
    beats = show.scene_beats(name, CFG, n, body)
    assert all(b.frames >= 1 for b in beats)
    assert show.beats_length(beats) <= body, "the beats run past the body"
    # The last body frame: seated at rest on an otherwise empty stage.
    assert show.body_stage(beats, body - 1) == show.seated_stage(CFG, show.REST_POSE)
    for k in range(big_n):
        f = play(cue, k)
        assert f.size == (52, 16)
        assert set(f.pixels) <= PALETTE, k
        assert border_empty(f), k
    after = at(cue, big_n)
    assert render(replace(READY, cue=cue), after, CFG) == home_page(READY, after, CFG)


@pytest.mark.parametrize("name", sorted(show.SCENES))
def test_scene_slot_shape(name):
    cue = scene_cue(name)
    big_n = show.slot_frames(cue)
    t0 = at(cue, 0)
    glance = home_page(READY, t0, CFG, show.GLANCE_POSE)
    assert all(play(cue, k) == glance for k in range(show.GLANCE_FRAMES))
    opened = play(cue, show.GLANCE_FRAMES + show.WIPE_FRAMES - 1)
    assert opened == show.stage_frame(show.seated_stage(CFG, show.GLANCE_POSE))
    # During the wipes Arthur's columns never change.
    for k in [*range(show.GLANCE_FRAMES, show.GLANCE_FRAMES + show.WIPE_FRAMES),
              *range(big_n - show.WIPE_FRAMES, big_n - 1)]:
        f = play(cue, k)
        pose = show.GLANCE_POSE if k < big_n // 2 else show.REST_POSE
        ref = home_page(READY, at(cue, k), CFG, pose)
        assert all(f.get(x, y) == ref.get(x, y)
                   for x in range(show.SEATED_R + 1) for y in range(16)), k
    assert play(cue, big_n - 1) == home_page(READY, at(cue, big_n - 1), CFG, show.REST_POSE)


@pytest.mark.parametrize("name", sorted(show.SCENES))
def test_scene_frames_are_steady_within_a_beat(name):
    cue = scene_cue(name, n=4)
    for k in range(0, show.slot_frames(cue), 3):
        assert play(cue, k) == play(cue, k, FRAME_US - 1), k


@pytest.mark.parametrize("name", sorted(show.SCENES))
def test_rounds_vary_the_quiet_part_and_repeat_exactly(name):
    def quiet_frames(n):
        cue = scene_cue(name, n)
        beats = show.scene_beats(name, CFG, n, body_frames(cue))
        return tuple(tuple(show.body_stage(beats, j).pixels) for j in range(0, 150, 2))

    rounds = [quiet_frames(n) for n in range(6)]
    assert len(set(rounds)) == len(rounds)
    assert quiet_frames(3) == rounds[3]


def test_scenes_differ():
    def body(name):
        cue = scene_cue(name)
        beats = show.scene_beats(name, CFG, 0, body_frames(cue))
        return [show.body_stage(beats, j) for j in range(body_frames(cue))]

    bodies = {name: body(name) for name in sorted(show.SCENES)}
    for a, b in itertools.combinations(sorted(bodies), 2):
        assert bodies[a] != bodies[b]
        # The burst differs too, not only the seeded quiet part.
        assert bodies[a][-80:] != bodies[b][-80:]


@pytest.mark.parametrize("name", sorted(show.SCENES))
def test_changed_frames_stay_under_budget(name):
    for n in range(5):
        cue = scene_cue(name, n)
        frames = [play(cue, k) for k in range(show.slot_frames(cue) + 1)]
        changed = sum(a != b for a, b in itertools.pairwise(frames))
        assert changed <= CHANGED_FRAME_BUDGET, (name, n, changed)


def test_scene_frames_pass_the_strict_text_check():
    for name in sorted(show.SCENES):
        cue = scene_cue(name)
        with strict_text():
            for k in range(0, show.slot_frames(cue), 2):
                play(cue, k)


def test_unknown_scene_name_shows_home():
    cue = scene_cue("no_such_scene")
    for k in (0, 50, 200):
        assert play(cue, k) == home_page(READY, at(cue, k), CFG)


def test_a_scene_that_comes_up_short_holds_its_last_frame(monkeypatch):
    def short(ctx):
        return [show.Beat(10, lambda s, i: show.draw_seated(s, ctx.cfg, "left")),
                show.Beat(5, lambda s, i: show.draw_seated(s, ctx.cfg, "rest"))]

    monkeypatch.setitem(show.SCENES, "short", show.SceneEntry(short))
    cue = scene_cue("short")
    first = show.GLANCE_FRAMES + show.WIPE_FRAMES
    held = show.stage_frame(show.seated_stage(CFG))
    assert play(cue, first) == show.stage_frame(show.seated_stage(CFG, "left"))
    assert all(play(cue, k) == held for k in range(first + 15, first + 200, 17))


# --- which scene plays ---------------------------------------------------------------------


@pytest.mark.parametrize("weights", [
    {"rat": 1, "bird": 1},
    {"a": 1, "b": 1, "c": 1},
    {"a": 1, "b": 1, "c": 1, "d": 1, "e": 1},
    {"a": 2, "b": 2, "c": 1},
])
def test_bag_never_repeats_a_scene(weights):
    seq = [show.pick_scene(n, weights) for n in range(3000)]
    assert all(a != b for a, b in itertools.pairwise(seq))
    size = sum(weights.values())
    for b in range(len(seq) // size):  # every bag holds each scene its weight's times
        assert Counter(seq[b * size:(b + 1) * size]) == Counter(weights)


def test_bag_order_varies_between_bags():
    weights = {"a": 1, "b": 1, "c": 1, "d": 1}
    bags = {tuple(show.pick_scene(n, weights) for n in range(4 * b, 4 * b + 4))
            for b in range(30)}
    assert len(bags) > 5


def test_bag_uses_the_registry_and_round_numbers_far_from_zero():
    seq = [show.pick_scene(n) for n in range(9_000_000, 9_000_200)]
    assert set(seq) == set(show.SCENES)
    assert all(a != b for a, b in itertools.pairwise(seq))


def test_a_pinned_name_always_wins():
    for n in range(20):
        for name in show.SCENES:
            assert show.scene_name(scene_cue(name, n)) == name


def test_one_registered_scene_still_works(monkeypatch):
    only = {"rat": show.SCENES["rat"]}
    monkeypatch.setattr(show, "SCENES", only)
    assert [show.pick_scene(n) for n in range(5)] == ["rat"] * 5
    cue = scene_cue(None, n=7)
    rat = scene_cue("rat", n=7)
    for k in (0, 20, 150, 270):
        assert play(cue, k) == play(rat, k)


def test_no_name_plays_the_bags_pick():
    for n in range(6):
        cue, pinned = scene_cue(None, n), scene_cue(show.pick_scene(n), n)
        assert play(cue, 100) == play(pinned, 100)


# --- pages -----------------------------------------------------------------------------------


def test_page_cue_shape():
    cue = page_cue()
    big_n = show.slot_frames(cue)
    st = replace(READY, cue=cue)
    body = weather_page(st, at(cue, 0), CFG)
    assert play(cue, 0) == home_page(READY, at(cue, 0), CFG)
    for k in range(1, show.DISSOLVE_FRAMES):
        f, home = play(cue, k), home_page(READY, at(cue, k), CFG)
        assert f not in (home, body)
        assert all(c in (home.pixels[p], body.pixels[p]) for p, c in enumerate(f.pixels))
    for k in range(show.DISSOLVE_FRAMES, big_n - show.DISSOLVE_FRAMES):
        assert play(cue, k) == weather_page(st, at(cue, k), CFG), k
    for k in range(big_n - show.DISSOLVE_FRAMES, big_n - 1):
        assert play(cue, k) != home_page(READY, at(cue, k), CFG)
    assert play(cue, big_n - 1) == home_page(READY, at(cue, big_n - 1), CFG)
    assert play(cue, big_n) == home_page(READY, at(cue, big_n), CFG)
    assert play(cue, -1) == home_page(READY, at(cue, -1), CFG)


def test_page_frames_are_clean_steady_and_strict():
    cue = page_cue()
    with strict_text():
        for k in range(show.slot_frames(cue)):
            f = play(cue, k)
            assert set(f.pixels) <= PALETTE and border_empty(f)
            assert f == play(cue, k, FRAME_US - 1)


def test_page_with_no_name_is_the_default_page():
    k = 40
    assert play(page_cue(None), k) == play(page_cue(show.DEFAULT_PAGE), k)


def test_unknown_page_shows_home():
    cue = page_cue("no_such_page")
    assert play(cue, 40) == home_page(READY, at(cue, 40), CFG)


def test_date_page_says_the_local_date():
    # READY carries no weather, so the page is the date alone: month and day, no weekday.
    def label(now):
        with strict_text() as records:
            f = weather_page(READY, now, CFG)
            check_text(f, records)
        return [r.text for r in records]

    assert label(NOW) == ["NOV 15"]  # 13:00 in Boston
    # 03:00 UTC on the 19th is still the evening of the 18th in Boston.
    assert label(datetime(2026, 11, 19, 3, 0, tzinfo=UTC)) == ["NOV 18"]
    assert date_text(datetime(2026, 11, 18).date()) == "NOV 18"


def test_every_date_fits_centred_and_strict():
    for m, d in itertools.product(MONTHS, range(1, 32)):
        text = f"{m} {d}"
        with strict_text() as records:
            f = date_frame(CFG, text)
            check_text(f, records)
        assert border_empty(f), text
        lit = [x for x in range(52) for y in range(16) if f.get(x, y) != OFF]
        left, right = min(lit) - 1, 50 - max(lit)
        assert abs(left - right) <= 1, text


# --- fixtures, preview -----------------------------------------------------------------------


def test_show_fixtures():
    page, rat, bird = (FIXTURES[n] for n in ("idle_page", "idle_rat", "idle_bird"))
    for fx in (page, rat, bird):
        assert fx.kind == "ready" and fx.fps == SHOW_FPS and fx.weather is not None
        st = fx.display_state(CFG)
        assert st.cue == fx.cue and st.weather == fx.weather
        assert fx.loop_s == fx.cue.length_s
        assert fx.loop_s * SHOW_FPS == int(fx.loop_s * SHOW_FPS)  # whole beats
    assert page.cue.kind == "page" and page.cue.at == NOW - timedelta(seconds=2)
    assert page.cue.name == show.DEFAULT_PAGE
    assert render(page.display_state(CFG), NOW, CFG) == show.PAGES[page.cue.name](
        page.display_state(CFG), NOW, CFG)
    assert (rat.cue.name, bird.cue.name) == ("rat", "bird")
    assert rat.cue.at == bird.cue.at == NOW and rat.cue.length_s == 36
    # Looping: the page plays to the end of its slot, then from the slot's start.
    assert page.time_at(0) == NOW
    assert page.time_at(52) == page.cue.at
    assert page.time_at(54) == NOW
    assert rat.time_at(35.875) == NOW + timedelta(seconds=35.875)
    assert rat.time_at(36) == NOW
    assert FIXTURES["ready"].time_at(100) == NOW + timedelta(seconds=100)


def test_preview_at_and_strip_sheet(capsys, tmp_path):
    assert preview_main(["idle_rat", "--at", "20"]) == 0
    out = capsys.readouterr().out
    assert "idle_rat [ready] 52x16 variant=arthur at +20s" in out
    assert preview_main(["idle_bird", "--sheet", str(tmp_path), "--frames", "4",
                         "--step", "0.125", "--at", "26"]) == 0
    assert list(tmp_path.glob("sheet__idle_bird__*frames4.png"))


# --- frame strips: one per scene, so a changed scene shows up in review ---------------------

STRIP_EVERY = SHOW_FPS  # one frame a second of the 36 s slot


@pytest.mark.parametrize("name", sorted(show.SCENES))
def test_scene_strip_golden(name):
    cue = scene_cue(name)
    parts = [legend_text(CFG)]
    for k in range(0, show.slot_frames(cue), STRIP_EVERY):
        parts.append(f"frame {k} (+{k / SHOW_FPS:g}s) {show.part_of(cue, k)}")
        parts.append(frame_text(play(cue, k), CFG))
    text = "\n".join(parts) + "\n"
    p = GOLDEN / f"scene_{name}__round0__36s.txt"
    if UPDATE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return
    assert p.exists(), f"missing {p.name}; run ARTHUR_UPDATE_GOLDEN=1 uv run pytest {__file__}"
    assert text == p.read_text(), f"{p.name} changed"
