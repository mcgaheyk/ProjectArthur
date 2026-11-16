import base64
import struct
import zlib
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW
from arthur.mapping import to_display_state
from arthur.model import BADGES, KINDS, DisplayState
from arthur.render import Frame, list_kinds, list_sizes, list_variants, overlay_stale, render
from arthur.render.font import condensed_small_font, large_font, small_font
from arthur.render.icons import get_icon, icon_names
from arthur.render.screens import (
    EDGE_RING,
    badges_to_draw,
    drawer_role,
    fmt_age,
    fmt_clock,
    fmt_countdown,
    has_edge_ring,
    litter_role,
)

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


@pytest.mark.parametrize("kind", ["ready", "in_use", "waiting", "cleaning"])
def test_variant_kinds_have_two_or_more(kind):
    for size in SIZES:
        assert 2 <= len(list_variants(kind, size)) <= 3


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
    frames = {render(s, fx.now + timedelta(seconds=t), CFG, variant="countdown").to_rgb888()
              for t in range(3)}
    assert len(frames) == 3


def test_badges_change_pixels():
    plain = FIXTURES["ready"]
    badged = FIXTURES["ready_badges"]
    for size in SIZES:
        s = to_display_state(badged.snapshot, badged.health, badged.now, CFG)
        f = render(s, NOW, CFG, size=size)
        s2 = DisplayState(**{**s.__dict__, "badges": frozenset()})
        assert f != render(s2, NOW, CFG, size=size)
    assert plain.kind == badged.kind


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


# --- badges ------------------------------------------------------------------------------


def test_idle_hides_the_level_badges_and_other_kinds_keep_them():
    every = frozenset(BADGES)
    idle = badges_to_draw(DisplayState(kind="ready", badges=every))
    assert idle == every - {"drawer_nearly_full", "litter_low"}
    for kind in ("in_use", "waiting", "cleaning", "just_cleaned", "interrupted", "fault"):
        assert badges_to_draw(DisplayState(kind=kind, badges=every)) == every
    assert badges_to_draw(DisplayState(kind="no_data", badges=every)) == frozenset()
    assert badges_to_draw(DisplayState(kind="drawer_full", badges=every)) == frozenset()


@pytest.mark.parametrize("variant", list_variants("ready", (52, 16)))
def test_idle_level_badges_draw_nothing(variant):
    plain = DisplayState(kind="ready", drawer_pct=85, litter_pct=20, litter_state="LOW")
    badged = DisplayState(**{**plain.__dict__,
                             "badges": frozenset({"drawer_nearly_full", "litter_low"})})
    assert render(badged, NOW, CFG, variant=variant) == render(plain, NOW, CFG, variant=variant)
    laser = DisplayState(**{**plain.__dict__, "badges": frozenset({"laser_dirty"})})
    assert render(laser, NOW, CFG, variant=variant) != render(plain, NOW, CFG, variant=variant)


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


def test_only_drawer_full_has_an_edge_ring():
    assert EDGE_RING == {((52, 16), "drawer_full", "default")}
    assert has_edge_ring("drawer_full", (52, 16))
    assert not has_edge_ring("ready", (52, 16))


def test_drawer_full_ring_flashes_on_even_seconds():
    for dt in range(6):
        f = _drawer_full_frame(dt)
        want = CFG.rgb("alert") if dt % 2 == 0 else CFG.rgb("off")
        assert all(f.get(x, y) == want for x, y in _edge(f)), dt


def test_drawer_full_keeps_a_gap_inside_the_ring_and_only_the_ring_moves():
    off = CFG.rgb("off")
    frames = [_drawer_full_frame(dt) for dt in range(4)]
    for f in frames:
        w, h = f.size
        gap = {(x, y) for x in range(1, w - 1) for y in (1, h - 2)}
        gap |= {(x, y) for x in (1, w - 2) for y in range(1, h - 1)}
        assert all(f.get(x, y) == off for x, y in gap)
    inside = [[f.get(x, y) for x in range(1, 51) for y in range(1, 15)] for f in frames]
    assert all(i == inside[0] for i in inside)


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
