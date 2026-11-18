"""The idle show's weather and date pages (arthur/render/pages.py, D-0019)."""

import itertools
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from arthur.config import default_config
from arthur.fixtures import FIXTURES, NOW
from arthur.model import Weather
from arthur.render import Frame, pages, render
from arthur.render import idle_show as show
from arthur.render.icons import get_icon
from arthur.render.preview import main as preview_main
from arthur.render.text import check_text, strict_text

CFG = default_config()
OFF = CFG.rgb("off")
PALETTE = set(CFG.palette.colors.values())
READY = FIXTURES["ready"].display_state(CFG)
PAGES = {"weather": pages.weather_page, "weather_big": pages.weather_big_page}
WEEKDAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DEG = "\N{DEGREE SIGN}"


def wx(temp=54.0, code=0, is_day=True, high=61.0, low=48.0) -> Weather:
    return Weather(temp_f=temp, code=code, is_day=is_day, high_f=high, low_f=low,
                   fetched_at=NOW)


def draw(name, weather, now=NOW):
    """The page under the strict text check; returns the frame and its labels."""
    with strict_text() as records:
        f = PAGES[name](replace(READY, weather=weather), now, CFG)
        check_text(f, records)
    labels = [r.text for r in records]
    assert f.size == (52, 16)
    assert set(f.pixels) <= PALETTE, (name, weather, labels)
    assert border_empty(f), (name, weather, labels)
    assert not any(w in label.split() for label in labels for w in WEEKDAY_NAMES), labels
    return f, labels


def border_empty(f: Frame) -> bool:
    return all(
        f.get(x, y) == OFF
        for x in range(f.width) for y in range(f.height)
        if x in (0, f.width - 1) or y in (0, f.height - 1)
    )


def glyph_box(f: Frame) -> tuple[tuple[int, int], ...]:
    """The 16x16 box at the left, as one comparable value."""
    return tuple(f.get(x, y) for y in range(16) for x in range(16))


def local_noon(month: int, day: int) -> datetime:
    """Noon in Boston on that date of 2026 (or 2028 for 29 February)."""
    year = 2028 if (month, day) == (2, 29) else 2026
    return datetime(year, month, day, 12, tzinfo=CFG.tz).astimezone(UTC)


# --- the WMO table ---------------------------------------------------------------------------


def test_every_wmo_code_maps_to_a_glyph_with_art():
    for code in range(100):
        g = pages.glyph_for(code)
        assert g in pages.GLYPHS, code
        for is_day, sec in itertools.product((True, False), range(4)):
            get_icon(pages.glyph_icon(g, is_day, sec), 16)


def test_wmo_table():
    expect = {
        0: "clear", 1: "clear", 2: "partly", 3: "cloud", 45: "fog", 48: "fog",
        51: "rain", 57: "rain", 61: "rain", 67: "rain", 80: "rain", 82: "rain",
        71: "snow", 77: "snow", 85: "snow", 86: "snow",
        95: "thunder", 96: "thunder", 99: "thunder",
        4: "cloud", 50: "cloud", 58: "cloud", 68: "cloud", 83: "cloud", 90: "cloud",
    }
    for code, g in expect.items():
        assert pages.glyph_for(code) == g, code
    # No code is listed twice.
    codes = [c for cs in pages.GLYPH_CODES.values() for c in cs]
    assert len(codes) == len(set(codes))


# --- glyphs ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("glyph", "is_day"), list(itertools.product(pages.GLYPHS, (True, False))))
@pytest.mark.parametrize("name", PAGES)
def test_glyph_stays_in_its_box_and_changes_at_most_once_a_second(name, glyph, is_day):
    code = pages.GLYPH_CODES[glyph][0]
    weather = wx(code=code, is_day=is_day)
    boxes = []
    for k in range(60 * 8):  # 60 seconds on the show's 1/8 s beat
        f, _ = draw(name, weather, NOW + timedelta(microseconds=k * 125_000))
        lit = {(x, y) for x in range(15) for y in range(16) if f.get(x, y) != OFF}
        assert lit, (glyph, k)
        assert all(1 <= x <= pages.GLYPH_R and 1 <= y <= 14 for x, y in lit), (glyph, k)
        boxes.append(glyph_box(f))
    changes = [k for k in range(1, len(boxes)) if boxes[k] != boxes[k - 1]]
    assert all(k % 8 == 0 for k in changes), changes  # only on a whole second
    assert len(changes) <= 60


def test_moving_glyphs_move_and_still_ones_do_not():
    def changes(glyph, is_day=True):
        seen = {glyph_box(draw("weather", wx(code=pages.GLYPH_CODES[glyph][0], is_day=is_day),
                               NOW + timedelta(seconds=s))[0]) for s in range(8)}
        return len(seen)

    for g in ("rain", "snow", "thunder", "fog"):
        assert changes(g) == 2, g
    assert changes("clear") == 2
    assert changes("clear", is_day=False) == 2  # a twinkling star
    assert changes("cloud") == changes("partly") == changes("partly", False) == 1


# --- words -----------------------------------------------------------------------------------


def test_sample_layout():
    _, labels = draw("weather", wx(54, 0, True, 61, 48), local_noon(11, 18))
    assert labels == ["54" + DEG, "61/48", "NOV 18"]
    _, labels = draw("weather_big", wx(54, 0, True, 61, 48), local_noon(11, 18))
    assert labels == ["54", DEG, "NOV", "18"]


@pytest.mark.parametrize("name", PAGES)
def test_every_temperature_fits(name):
    when = local_noon(11, 28)  # NOV 28: as wide as any date
    for t in range(-20, 106):
        for high, low in ((t, t), (t + 12, t - 12), (t + 3, t - 15)):
            weather = wx(float(t), 3, True, float(high), float(low))
            _, labels = draw(name, weather, when)
            assert any(label.startswith(str(t)) for label in labels), (t, labels)
            if name == "weather":
                assert labels[0] == f"{t}{DEG}"
                if f"{high}/{low}" in labels:
                    assert labels == [f"{t}{DEG}", f"{high}/{low}", "NOV 28"]
                else:
                    assert labels == [f"{t}{DEG}", "NOV 28"]
            else:
                assert labels[0] == str(t)
                assert labels[-2:] == ["NOV", "28"]


def test_high_and_low_show_when_both_are_two_digits():
    for t, high, low in ((54, 61, 48), (10, 19, 2), (99, 99, 80), (-9, 1, -9)):
        _, labels = draw("weather", wx(t, 0, True, high, low))
        assert f"{high}/{low}" in labels, (t, labels)


def test_wide_temperatures_drop_the_high_and_low_not_squeeze():
    for t, high, low in ((102, 104, 80), (-15, -2, -20)):
        _, labels = draw("weather", wx(t, 0, True, high, low))
        assert labels == [f"{t}{DEG}", "NOV 15"], labels


@pytest.mark.parametrize("name", PAGES)
def test_every_date_fits(name):
    for month, day in itertools.product(range(1, 13), range(1, 32)):
        try:
            when = local_noon(month, day)
        except ValueError:
            continue  # no such date
        for t in (-20, 54, 105):
            _, labels = draw(name, wx(t, 0, True, t + 5, t - 5), when)
            text = f"{pages.MONTHS[month - 1]} {day}"
            if name == "weather":
                assert labels[-1] == text
            else:
                assert labels[-2:] == text.split()


def test_rounding_and_negatives():
    assert pages.temp_text(wx(53.5)) == "54" + DEG
    assert pages.temp_text(wx(53.49)) == "53" + DEG
    assert pages.temp_text(wx(-4.6)) == "-5" + DEG
    assert pages.temp_text(wx(-0.4)) == "0" + DEG
    assert pages.hilo_text(wx(high=60.6, low=-0.2)) == "61/0"


@pytest.mark.parametrize("name", PAGES)
def test_no_weather_is_the_date_alone(name):
    f, labels = draw(name, None, local_noon(11, 18))
    assert labels == ["NOV 18"]
    assert f == pages.date_frame(CFG, "NOV 18")
    # Nothing else is lit: no glyph, no stray digits or degree sign.
    assert all(f.get(x, y) == OFF for x in range(14) for y in range(16))


@pytest.mark.parametrize("name", PAGES)
def test_missing_high_or_low_draws_neither(name):
    for high, low in ((None, 48.0), (61.0, None), (None, None)):
        _, labels = draw(name, wx(54, 0, True, high, low))
        assert not any("/" in label for label in labels), labels
        assert labels[0].startswith("54")


@pytest.mark.parametrize("name", PAGES)
def test_the_temperature_is_only_the_readings(name):
    for weather in (None, wx(41, 61, True, 47, 35), wx(28.4, 0, False, None, None)):
        _, labels = draw(name, weather)
        digits = [label for label in labels if label.lstrip("-")[:1].isdigit()]
        if weather is None:
            assert labels == ["NOV 15"] and digits == []
        else:
            assert digits[0] in (pages.temp_text(weather), pages.temp_text(weather)[:-1])


def test_pages_are_registered():
    assert show.PAGES["weather"] is pages.weather_page
    assert show.PAGES["weather_big"] is pages.weather_big_page
    assert show.DEFAULT_PAGE == "weather"


# --- fixtures and preview --------------------------------------------------------------------


def test_weather_page_fixtures():
    for name, page_name in (("idle_page", "weather"), ("idle_page_rain", "weather"),
                            ("idle_page_night", "weather"), ("idle_page_big", "weather_big")):
        fx = FIXTURES[name]
        st = fx.display_state(CFG)
        assert st.weather == fx.weather is not None and fx.cue.name == page_name
        assert render(st, NOW, CFG) == PAGES[page_name](st, NOW, CFG)
    assert pages.glyph_for(FIXTURES["idle_page_rain"].weather.code) == "rain"
    assert not FIXTURES["idle_page_night"].weather.is_day


def test_preview_weather_override(capsys, tmp_path):
    assert preview_main(["idle_page", "--weather", "61,day,41,47,35",
                         "--weather", "none", "--weather", "0,night,28,-,-"]) == 0
    out = capsys.readouterr().out
    assert "idle_page [61 day 41 47 35] [ready]" in out
    assert "idle_page [no weather] [ready]" in out
    assert "idle_page [0 night 28] [ready]" in out
    assert preview_main(["idle_page_big", "--weather", "glyphs", "--sheet", str(tmp_path),
                         "--frames", "2"]) == 0
    assert list(tmp_path.glob("sheet__idle_page_big__*frames2.png"))
    assert preview_main(["idle_page", "--weather", "61,dusk"]) == 2
