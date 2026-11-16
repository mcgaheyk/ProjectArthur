import base64
import itertools
import struct
import zlib
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW
from arthur.mapping import to_display_state
from arthur.model import KINDS, DisplayState
from arthur.render import Frame, list_kinds, list_sizes, list_variants, overlay_stale, render
from arthur.render.font import condensed_small_font, large_font, small_font
from arthur.render.icons import get_icon, icon_names
from arthur.render.screens import (
    EDGE_RING,
    IDLE_POSES,
    IDLE_R,
    IDLE_SCRIPT,
    IDLE_WAG,
    IDLE_X,
    LASER_WORDS,
    POOP_FACE_LOOP,
    TAIL_OUT,
    TAIL_REST,
    TAIL_X,
    TAIL_Y,
    YAWN_EVERY_MIN,
    _ready_arthur_52,
    _waiting_52,
    drawer_role,
    fmt_age,
    fmt_clock,
    fmt_countdown,
    has_edge_ring,
    idle_pose,
    idle_tail,
    litter_role,
    phase,
)
from arthur.render.text import check_text, strict_text

CFG = default_config()
SIZES = list(list_sizes())


def cases():
    for name, fx in FIXTURES.items():
        for size in SIZES:
            for v in list_variants(fx.kind, size):
                yield pytest.param(name, size, v, id=f"{name}-{size[0]}x{size[1]}-{v}")


# --- Frame ------------------------------------------------------------------------------


def test_frame_clips_silently():
    f = Frame(4, 3)
    f.pixel(-1, 0, (1, 2, 3))
    f.pixel(4, 0, (1, 2, 3))
    f.rect(-5, -5, 100, 100, (9, 9, 9))
    assert all(p == (9, 9, 9) for p in f.pixels)
    f.text(-3, -3, "HELLO WORLD", (1, 1, 1))  # no error


def test_frame_encoders():
    f = Frame(3, 2)
    f.pixel(0, 0, (255, 0, 0))
    f.pixel(2, 1, (0x12, 0x34, 0x56))
    assert f.to_rgb888()[:3] == b"\xff\x00\x00"
    assert f.to_rgb888()[-3:] == b"\x12\x34\x56"
    assert f.to_ints() == [0xFF0000, 0, 0, 0, 0, 0x123456]
    assert Frame.from_ints(3, 2, f.to_ints()) == f
    assert f.to_ascii({(255, 0, 0): "R"}) == "R..\n..?"


def test_png_is_valid():
    f = Frame(52, 16)
    f.pixel(1, 1, (10, 20, 30))
    png = f.to_png(scale=3)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (156, 48)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41 : 41 + idat_len])
    assert len(raw) == 48 * (1 + 156 * 3)


def test_blit_and_lines():
    src = Frame(2, 2, (5, 5, 5))
    f = Frame(4, 4)
    f.blit(src, 3, 3)
    assert f.get(3, 3) == (5, 5, 5)
    f.blit([[None, (1, 1, 1)]], 0, 0)
    assert f.get(0, 0) == (0, 0, 0) and f.get(1, 0) == (1, 1, 1)
    f.hline(0, 2, 4, (7, 7, 7))
    f.vline(0, 0, 4, (8, 8, 8))
    assert f.get(3, 2) == (7, 7, 7) and f.get(0, 3) == (8, 8, 8)
    f.rect(0, 0, 4, 4, (2, 2, 2), filled=False)
    assert f.get(1, 1) != (2, 2, 2) and f.get(3, 0) == (2, 2, 2)


def test_stale_overlay():
    f = overlay_stale(Frame(52, 16), CFG.rgb("stale"))
    assert f.get(0, 0) == f.get(51, 15) == (0x6E, 0x07, 0x00)
    assert f.get(1, 1) == (0, 0, 0)


# --- fonts and icons ---------------------------------------------------------------------


def test_fonts_have_needed_glyphs():
    needed = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ %:.-/!~"
    for font in (small_font(), large_font()):
        for ch in needed:
            assert ord(ch) in font.glyphs, (font.name, ch)
    assert small_font().cap_height == 5
    assert large_font().width("A") == 3


def test_icons_load():
    # Every icon has 16x16 art (its own, or its 8x8 drawn at double size).
    for name in icon_names():
        icon, scale = get_icon(name, 16)
        assert icon.size * scale == 16


# --- formatting --------------------------------------------------------------------------


def test_time_formats():
    assert fmt_clock(47) == "0:47"
    assert fmt_clock(599) == "9:59"
    assert fmt_clock(600) == "10M"
    assert fmt_clock(3 * 3600) == "3H"
    assert fmt_countdown(0.2) == "0:01"
    assert fmt_countdown(-5) == "0:00"
    assert fmt_age(30) == "NOW"
    assert fmt_age(25 * 60) == "25M"
    assert fmt_age(3 * 86400) == "3D"


# --- registry ----------------------------------------------------------------------------


def test_only_52x16():
    assert SIZES == [(52, 16)]


def test_every_kind_has_layouts():
    assert list_kinds() == KINDS
    for kind in KINDS:
        for size in SIZES:
            assert list_variants(kind, size)


def test_only_ready_has_variants():
    for size in SIZES:
        assert 2 <= len(list_variants("ready", size)) <= 3
        for kind in ("in_use", "cleaning", "waiting", "just_cleaned", "drawer_full"):
            assert list_variants(kind, size) == ["default"], kind


def test_unknown_variant_falls_back_to_default():
    fx = FIXTURES["ready"]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    assert render(s, NOW, CFG, "52x16", "nope") == render(s, NOW, CFG, "52x16")


def test_size_defaults_to_config_and_accepts_strings():
    s = DisplayState(kind="robot_off")
    assert render(s, NOW, CFG).size == CFG.size
    assert render(s, NOW, CFG, size="52x16").size == (52, 16)
    for bad in ((64, 32), "64x32"):
        with pytest.raises(ValueError):
            render(s, NOW, CFG, size=bad)


# --- every fixture, size and variant ---------------------------------------------------------


@pytest.mark.parametrize(("name", "size", "variant"), list(cases()))
def test_renders_cleanly(name, size, variant):
    fx = FIXTURES[name]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    for dt in (0, 1):  # both phases of any blink
        f = render(s, fx.now + timedelta(seconds=dt), CFG, size=size, variant=variant)
        w, h = size
        assert f.size == size
        assert len(f.to_rgb888()) == w * h * 3
        assert len(base64.b64encode(f.to_rgb888())) == 3328
        # Nothing may be drawn where the clock's stale border goes, except the ring of a
        # layout registered with edge_ring=True (tested on its own below).
        off = CFG.rgb("off")
        border = [f.get(x, 0) for x in range(w)] + [f.get(x, h - 1) for x in range(w)]
        border += [f.get(0, y) for y in range(h)] + [f.get(w - 1, y) for y in range(h)]
        if not has_edge_ring(fx.kind, size, variant):
            assert all(p == off for p in border), f"{name} draws on the border"
        # Only palette colours are used.
        palette = set(CFG.palette.colors.values())
        assert set(f.pixels) <= palette
        # Something is lit.
        assert any(p != off for p in f.pixels)


def test_interrupted_differs_from_cleaning_in_shape():
    def lit(name, size):
        fx = FIXTURES[name]
        s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
        f = render(s, fx.now, CFG, size=size)
        return {i for i, p in enumerate(f.pixels) if p != CFG.rgb("off")}

    for size in SIZES:
        a, b = lit("cleaning", size), lit("interrupted", size)
        assert len(a ^ b) > len(a & b)


def test_countdown_ticks():
    fx = FIXTURES["waiting"]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    frames = {render(s, fx.now + timedelta(seconds=t), CFG).to_rgb888() for t in range(3)}
    assert len(frames) == 3


# --- waiting: one layout ---------------------------------------------------------------------


def test_waiting_has_one_layout_that_says_clean_in():
    assert list_variants("waiting", (52, 16)) == ["default"]
    assert tuple(CFG.captions["waiting"]) == ("CLEAN IN",)
    fx = FIXTURES["waiting"]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    with strict_text() as records:
        f = _waiting_52(s, fx.now, CFG)
        check_text(f, records)
    assert [r.text for r in records] == ["CLEAN IN", "4:42"]
    assert f == render(s, fx.now, CFG)


# --- level colours -----------------------------------------------------------------------


@pytest.mark.parametrize(("word", "role"), [
    ("OPTIMAL", "ok"), ("REFILL", "caution"), ("OVERFILL", "caution"), ("LOW", "alert"),
    ("EMPTY", "alert"), ("optimal", "ok"), (None, "sand"), ("", "sand"), ("WEIRD", "sand"),
])
def test_litter_role_follows_the_robots_word(word, role):
    assert litter_role(DisplayState(kind="ready", litter_state=word, litter_pct=50)) == role


def test_litter_role_ignores_the_percentage():
    for pct in (0, 20, 50, 100):
        s = DisplayState(kind="ready", litter_state="OPTIMAL", litter_pct=pct)
        assert litter_role(s) == "ok"


def test_drawer_role_threshold():
    warn = CFG.thresholds.drawer_warn_pct
    for pct, role in ((None, "drawer"), (0, "drawer"), (42, "drawer"), (warn - 0.1, "drawer"),
                      (warn, "alert"), (warn + 1, "alert"), (100, "alert")):
        assert drawer_role(DisplayState(kind="ready", drawer_pct=pct), CFG) == role, pct
    for kind in ("in_use", "waiting", "cleaning", "just_cleaned"):
        assert drawer_role(DisplayState(kind=kind, drawer_pct=50), CFG) == "drawer"
    assert drawer_role(DisplayState(kind="drawer_full", drawer_pct=10), CFG) == "alert"


def test_new_roles_are_their_own_colours():
    # The level colours must not share a hex with each other or with any other role, or the
    # bars stop telling drawer from litter and the text preview cannot name them.
    level_roles = ("drawer", "caution", "ok", "alert", "sand")
    for role in level_roles:
        same = [r for r in CFG.palette.colors if r != role and CFG.rgb(r) == CFG.rgb(role)]
        assert not same, f"{role} shares its colour with {same}"
    assert not hasattr(CFG.thresholds, "drawer_amber_pct")


def test_idle_fixtures_show_each_level_colour():
    def bar_roles(name):
        fx = FIXTURES[name]
        s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
        return drawer_role(s, CFG), litter_role(s)

    assert bar_roles("ready") == ("drawer", "ok")
    assert bar_roles("ready_refill")[1] == "caution"
    assert bar_roles("ready_overfill")[1] == "caution"
    assert bar_roles("ready_litter_low")[1] == "alert"
    assert bar_roles("ready_drawer_high") == ("alert", "ok")


# --- idle: the long tail, the moved line and the laser notice --------------------------------

READY = DisplayState(kind="ready", drawer_pct=42, litter_pct=78, litter_state="OPTIMAL",
                     last_visit_at=NOW - timedelta(hours=2))


def _labels(state, now):
    """Idle Arthur's frame and its labels, checked strictly (nothing may touch a label)."""
    with strict_text() as records:
        f = _ready_arthur_52(state, now, CFG)
        check_text(f, records)
    assert f == render(state, now, CFG, variant="arthur")
    return f, list(records)


def _pose_times():
    """A moment for each idle pose (the yawn from a minute that has one)."""
    out = {}
    for dt in range(60 * YAWN_EVERY_MIN):
        out.setdefault(idle_pose(NOW + timedelta(seconds=dt)), NOW + timedelta(seconds=dt))
    assert set(out) == set(IDLE_POSES)
    return out


def test_idle_laser_notice_shows_and_fits():
    width = IDLE_R - IDLE_X + 1
    want = next(w for w in LASER_WORDS if small_font().width(w) <= width)
    dirty = DisplayState(**{**READY.__dict__, "laser_dirty": True})
    f, labels = _labels(dirty, NOW)  # strict: raises if it does not fit
    bottom = [r for r in labels if r.y == 9]
    assert [r.text for r in bottom] == [want]
    assert all(f.get(x, y) == CFG.rgb("attention") for x, y in bottom[0].ink)
    assert f != render(READY, NOW, CFG, variant="arthur")


@pytest.mark.parametrize("variant", ["vacant", "numbers"])
def test_other_idle_variants_ignore_the_laser(variant):
    dirty = DisplayState(**{**READY.__dict__, "laser_dirty": True})
    assert render(dirty, NOW, CFG, variant=variant) == render(READY, NOW, CFG, variant=variant)


@pytest.mark.parametrize("ago_s", [None, 0, 59, 60, 59 * 60, 3600, 10 * 3600, 47 * 3600,
                                   48 * 3600, 9 * 86400, 99 * 86400])
def test_idle_last_visit_forms_fit_the_moved_line(ago_s):
    last = None if ago_s is None else NOW - timedelta(seconds=ago_s)
    s = DisplayState(**{**READY.__dict__, "last_visit_at": last})
    _, labels = _labels(s, NOW)  # strict: raises if it does not fit
    (bottom,) = [r for r in labels if r.y == 9]
    assert bottom.x == IDLE_X and bottom.right == IDLE_R == 50


def _tail_cells(pose):
    return {(TAIL_X + x, TAIL_Y + y) for y, row in enumerate(idle_tail(pose))
            for x, ch in enumerate(row) if ch != "."}


# The idle script: alive, not busy.

GESTURES = {"blink", "left", "right", "ear", "squint", "yawn"}


def _runs(seq):
    """Collapse a pose sequence into gesture runs: a wag's frames are one run."""
    runs, i = [], 0
    while i < len(seq):
        if tuple(seq[i:i + len(IDLE_WAG)]) == IDLE_WAG:
            runs.append("wag")
            i += len(IDLE_WAG)
            continue
        if seq[i] in IDLE_WAG:
            runs.append("partial-wag")
        else:
            runs.append(seq[i])
        i += 1
    return runs


def test_idle_script_shape():
    assert len(IDLE_SCRIPT) == 60
    assert set(IDLE_SCRIPT) <= set(IDLE_POSES)
    assert IDLE_SCRIPT.count("rest") >= 0.6 * 60
    runs = _runs(IDLE_SCRIPT)
    assert "partial-wag" not in runs  # every wag is whole
    for g in ("blink", "left", "right", "ear", "squint", "wag"):
        assert g in runs, g
    assert runs.count("wag") == 2
    assert 1 <= runs.count("ear") <= 2
    assert runs.count("blink") >= 3
    assert IDLE_SCRIPT.count("squint") == 2  # one two-second squint
    assert "yawn" not in IDLE_SCRIPT  # the yawn has its own slower clock
    # One gesture at a time: two different gestures always have a still second between them,
    # except a glance left running into one right (or back). Wrap round the minute too.
    looped = runs + runs[:1]
    for a, b in itertools.pairwise(looped):
        if a != "rest" and b != "rest" and a != b:
            assert {a, b} == {"left", "right"}, (a, b)
    # The first half-minute (the studio tile) shows each kind of gesture.
    first = _runs(IDLE_SCRIPT[:30])
    assert {"blink", "ear", "wag"} <= set(first)
    assert {"left", "right"} & set(first)
    # Blinks are not evenly spaced.
    at = [i for i, p in enumerate(IDLE_SCRIPT) if p == "blink"]
    gaps = {(b - a) % 60 for a, b in itertools.pairwise(at + at[:1])}
    assert len(gaps) > 1


def test_idle_yawn_is_rare():
    minutes = []
    for m in range(10):
        poses = [idle_pose(NOW + timedelta(seconds=60 * m + s)) for s in range(60)]
        minutes.append(poses.count("yawn"))
        if "yawn" in poses:
            i = poses.index("yawn")
            assert poses[i:i + 2] == ["yawn", "yawn"] and poses.count("yawn") == 2
            assert poses[i - 1] == poses[i + 2] == "rest"
    assert any(minutes) and not all(minutes)
    yawn_minutes = [m for m, n in enumerate(minutes) if n]
    assert all(b - a >= 5 for a, b in itertools.pairwise(yawn_minutes))


def test_idle_is_still_most_of_the_minute():
    """Each changed frame is an HTTP push: idle stays still for at least half of every
    minute (a second whose frame equals the one before)."""
    for start in (0, 60 * 3):  # a plain minute and a yawn minute
        frames = [render(READY, NOW + timedelta(seconds=start + dt), CFG, variant="arthur")
                  for dt in range(61)]
        still = sum(a == b for a, b in itertools.pairwise(frames))
        assert still >= 30, still


def test_tail_art_shapes():
    for pose in IDLE_POSES:
        art = idle_tail(pose)
        assert len(art) == 14 and len({len(r) for r in art}) == 1
    assert idle_tail("rest") == TAIL_REST
    assert idle_tail("out") == TAIL_OUT
    for pose in IDLE_POSES:
        cells = _tail_cells(pose)
        assert max(x for x, _ in cells) > 15  # it reaches beyond the 16x16 icon
        assert all(1 <= x <= 50 and 1 <= y <= 14 for x, y in cells)
        if pose != "out":
            assert max(x for x, _ in cells) < IDLE_X - 1  # gap column before the content


def _bars_and_marks(f):
    """Lit pixels of the two marks and bars (rows 1-7 of the content columns)."""
    off = CFG.rgb("off")
    return {(x, y) for x in range(IDLE_X, 51) for y in range(1, 8) if f.get(x, y) != off}


LAST_VISITS = [None, 0, 45 * 60, 2 * 3600, 10 * 3600, 47 * 3600, 9 * 86400]


@pytest.mark.parametrize("laser", [False, True])
@pytest.mark.parametrize("drawer", [0, 42, 100])
def test_tail_never_touches_the_bars_or_marks(laser, drawer):
    s = DisplayState(**{**READY.__dict__, "laser_dirty": laser, "drawer_pct": drawer})
    off = CFG.rgb("off")
    for pose, now in _pose_times().items():
        f, _ = _labels(s, now)  # strict: nothing else drawn against a label
        tail = _tail_cells(pose)
        bars = _bars_and_marks(f)
        assert bars
        for tx, ty in tail:
            for cx, cy in bars:
                assert max(abs(cx - tx), abs(cy - ty)) >= 2, (pose, (tx, ty), (cx, cy))
        # Outside the bottom line the tail is drawn; inside it, it hides behind the letters.
        for tx, ty in tail:
            if not (IDLE_X <= tx and 9 <= ty <= 13):
                assert f.get(tx, ty) != off


def _bottom_line(state, now):
    f, labels = _labels(state, now)
    (line,) = [r for r in labels if r.y == 9]
    return f, line


@pytest.mark.parametrize("laser", [False, True])
def test_tail_swings_through_the_text_and_tints_it(laser):
    tint = CFG.rgb("fur_tint")
    for ago in LAST_VISITS:
        last = None if ago is None else NOW - timedelta(seconds=ago)
        s = DisplayState(**{**READY.__dict__, "last_visit_at": last, "laser_dirty": laser})
        now = NOW + timedelta(seconds=IDLE_SCRIPT.index("out"))
        f, line = _bottom_line(s, now)
        tinted = {p for p in line.ink if f.get(*p) == tint}
        assert len(tinted) >= 3, (line.text, len(tinted))
        # Letters keep their shapes: no pixel inside the line's ink box changes from dark.
        xs = [x for x, _ in line.ink]
        ref = render(s, NOW, CFG, variant="arthur")  # a resting frame
        for x in range(min(xs), max(xs) + 1):
            for y in range(9, 14):
                assert (f.get(x, y) == CFG.rgb("off")) == (ref.get(x, y) == CFG.rgb("off"))


@pytest.mark.parametrize("laser", [False, True])
def test_letters_stay_intact_for_ten_minutes(laser):
    tint = CFG.rgb("fur_tint")
    normal = CFG.rgb("attention" if laser else "text")
    off = CFG.rgb("off")
    for ago in [None, 45 * 60, 2 * 3600, 9 * 86400]:
        last = None if ago is None else NOW - timedelta(seconds=ago)
        s = DisplayState(**{**READY.__dict__, "last_visit_at": last, "laser_dirty": laser})
        for dt in range(600):
            f, line = _bottom_line(s, NOW + timedelta(seconds=dt))
            assert all(f.get(*p) in (normal, tint) for p in line.ink), (line.text, dt)
            border = [f.get(x, y) for x in range(52) for y in (0, 15)]
            border += [f.get(x, y) for x in (0, 51) for y in range(16)]
            assert all(p == off for p in border)


def test_tint_is_only_used_by_the_tail():
    tint = CFG.rgb("fur_tint")
    for pose, now in _pose_times().items():
        f = render(READY, now, CFG, variant="arthur")
        assert (tint in f.pixels) == (pose == "out"), pose


def test_idle_bars_keep_length_and_colours():
    for pct, role in ((50, "drawer"), (100, "alert")):
        s = DisplayState(**{**READY.__dict__, "drawer_pct": pct, "litter_pct": 100})
        f = render(s, NOW, CFG, variant="arthur")
        lit = [x for x in range(52) if f.get(x, 2) == CFG.rgb(role)]
        track = [x for x in range(52) if f.get(x, 2) == CFG.rgb("dim")]
        litter = [x for x in range(52) if f.get(x, 6) == CFG.rgb("ok")]
        assert sorted(lit + track) == litter == list(range(IDLE_X + 7, IDLE_R + 1))


#: Layouts allowed to draw in columns 48-50 (column 51 is the border): idle Arthur moved its
#: bars and bottom line there, and drawer_full's FULL and caption run to column 49.
WIDE = {("ready", "arthur"), ("drawer_full", "default")}


@pytest.mark.parametrize(("name", "size", "variant"), list(cases()))
def test_only_wide_layouts_use_columns_48_to_50(name, size, variant):
    fx = FIXTURES[name]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    if (fx.kind, variant) in WIDE:
        return
    off = CFG.rgb("off")
    for dt in range(4):
        f = render(s, fx.now + timedelta(seconds=dt), CFG, size=size, variant=variant)
        assert all(f.get(x, y) == off for x in (48, 49, 50) for y in range(1, 15)), name


# --- drawer full: the flashing edge ring -----------------------------------------------------


def _edge(f):
    w, h = f.size
    pts = {(x, y) for x in range(w) for y in (0, h - 1)} | {(x, y) for x in (0, w - 1)
                                                           for y in range(h)}
    return pts


def _drawer_full_frame(dt):
    fx = FIXTURES["drawer_full"]
    s = to_display_state(fx.snapshot, fx.health, fx.now, CFG)
    return render(s, fx.now + timedelta(seconds=dt), CFG)


def test_only_drawer_full_and_cleaning_have_an_edge_ring():
    assert EDGE_RING == {((52, 16), "drawer_full", "default"), ((52, 16), "cleaning", "default")}
    assert has_edge_ring("drawer_full", (52, 16))
    assert has_edge_ring("cleaning", (52, 16))
    assert not has_edge_ring("ready", (52, 16))
    assert not has_edge_ring("in_use", (52, 16))


def test_drawer_full_ring_flashes_on_even_seconds():
    for dt in range(6):
        f = _drawer_full_frame(dt)
        want = CFG.rgb("alert") if dt % 2 == 0 else CFG.rgb("off")
        assert all(f.get(x, y) == want for x, y in _edge(f)), dt


#: The poop's face (eyes and mouth) on screen: columns 4-12, rows 7-13. Only it and the ring
#: may change between drawer-full frames.
FACE_BOX = {(x, y) for x in range(4, 13) for y in range(7, 14)}


def _drawer_full_face(dt):
    fx = FIXTURES["drawer_full"]
    return POOP_FACE_LOOP[phase(fx.now + timedelta(seconds=dt), len(POOP_FACE_LOOP))]


def test_drawer_full_keeps_a_gap_inside_the_ring_and_only_ring_and_face_move():
    off = CFG.rgb("off")
    frames = [_drawer_full_frame(dt) for dt in range(2 * len(POOP_FACE_LOOP))]
    for f in frames:
        w, h = f.size
        gap = {(x, y) for x in range(1, w - 1) for y in (1, h - 2)}
        gap |= {(x, y) for x in (1, w - 2) for y in range(1, h - 1)}
        assert all(f.get(x, y) == off for x, y in gap)
    rest = [[f.get(x, y) for x in range(1, 51) for y in range(1, 15) if (x, y) not in FACE_BOX]
            for f in frames]
    assert all(r == rest[0] for r in rest)


def test_drawer_full_face_loop_shape():
    loop = POOP_FACE_LOOP
    assert len(loop) % 2 == 1  # odd, so the face drifts against the 2 s ring
    assert loop.count("poop_full") > len(loop) / 2
    assert loop.count("poop_full_left") >= 1 and loop.count("poop_full_right") >= 1
    assert loop.count("poop_full_blink") >= 2 and loop.count("poop_full_wink") == 1
    for a, b in itertools.pairwise(loop + loop[:1]):
        assert a == "poop_full" or a != b, (a, b)


def test_drawer_full_face_is_independent_of_the_ring():
    alert = CFG.rgb("alert")
    seen: dict[str, set[bool]] = {}
    for dt in range(2 * len(POOP_FACE_LOOP)):
        ring_on = _drawer_full_frame(dt).get(0, 0) == alert
        seen.setdefault(_drawer_full_face(dt), set()).add(ring_on)
    for face, phases in seen.items():
        if face != "poop_full":
            assert phases == {True, False}, face
    assert set(seen) == set(POOP_FACE_LOOP)


def test_drawer_full_has_no_shout_and_the_mouth_never_moves():
    assert "poop_full_shout" not in icon_names()
    assert "poop_full_shout" not in POOP_FACE_LOOP
    mouths = {tuple(_drawer_full_frame(dt).get(x, y) for x in range(2, 15) for y in range(10, 14))
              for dt in range(len(POOP_FACE_LOOP))}
    assert len(mouths) == 1


def test_drawer_full_calm_faces_are_distinct():
    rows = {get_icon(name, 16)[0].rows for name in set(POOP_FACE_LOOP)}
    assert len(rows) == len(set(POOP_FACE_LOOP)) == 5


def test_drawer_full_faces_share_one_body():
    # Every face fills the same body outline: only interior eye and mouth cells differ.
    base = get_icon("poop_full", 16)[0].rows
    for name in set(POOP_FACE_LOOP):
        rows = get_icon(name, 16)[0].rows
        for y in range(16):
            for x in range(16):
                if (x, y) not in FACE_BOX:
                    assert rows[y][x] == base[y][x], (name, x, y)


def test_stale_overlay_covers_the_ring():
    stale = CFG.rgb("stale")
    for dt in (0, 1):
        f = overlay_stale(_drawer_full_frame(dt), stale)
        assert all(f.get(x, y) == stale for x, y in _edge(f))


def test_drawer_full_captions_fit_their_line():
    font = condensed_small_font()
    for c in CFG.captions["drawer_full"]:
        assert font.width(c) <= 34, c
    assert small_font().width("EMPTY NOW") > 34  # why the condensed M and W are needed
    assert font.width("EMPTY NOW") == 34
