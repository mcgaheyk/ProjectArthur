import base64
import struct
import zlib
from datetime import timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW
from arthur.mapping import to_display_state
from arthur.model import KINDS, DisplayState
from arthur.render import Frame, list_kinds, list_sizes, list_variants, overlay_stale, render
from arthur.render.font import large_font, small_font
from arthur.render.icons import get_icon, icon_names
from arthur.render.screens import fmt_age, fmt_clock, fmt_countdown

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
        # Nothing may be drawn where the clock's stale border goes.
        off = CFG.rgb("off")
        border = [f.get(x, 0) for x in range(w)] + [f.get(x, h - 1) for x in range(w)]
        border += [f.get(0, y) for y in range(h)] + [f.get(w - 1, y) for y in range(h)]
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
