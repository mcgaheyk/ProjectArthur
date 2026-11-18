"""The idle show's information pages (D-0019), registered with :func:`idle_show.page`.

A page is ``(state, now, cfg) -> Frame`` at 52x16 with the border empty; it runs on whole
seconds like the home page. The player dissolves into and out of it.

Two layouts of Boston's weather and the date. Kyle chose ``weather_big`` in the studio
(D-0021); ``weather`` is kept, unplayed, until he has seen the page on the LEDs:

- ``weather_big`` (the page a page cue plays by default): a weather glyph on the left, the
  temperature at double size and the date stacked (``NOV`` over ``18``) to its right; no
  high and low.
- ``weather``: the same glyph; beside it the temperature in the 7-row font with today's high
  and low in the small font after it, and the date (``NOV 18``) on the line below.

With no fresh reading (``state.weather`` is None) both show the date alone, centred in the
7-row font: never an old temperature. The date is the month and the day in the config's zone;
Kyle does not want the day of the week on this page.

Glyphs are text-art icons ``icons/wx_*.16.txt``, inked within columns 1-13 and rows 1-14.
Their motion stays in place and changes at most once a second (rain and snow every second,
the sun's rays and the fog every two).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime

from arthur.config import Config
from arthur.model import DisplayState, Weather
from arthur.render.font import condensed_small_font, large_font, small_font
from arthur.render.frame import Frame
from arthur.render.icons import draw_icon
from arthur.render.idle_show import STAGE_W, STAGE_X, H, W, page
from arthur.render.text import put_text

MONTHS: tuple[str, ...] = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)
#: Top row of the date-only line: the 7-row capitals sit on rows 4-10, centred.
DATE_Y = (H - 7) // 2
#: Colour roles.
DATE_ROLE = "text"
TEMP_ROLE = "text"
HILO_ROLE = "sand"
STAR_ROLE = "text"

#: Last column a glyph may ink. The words start three columns after it: with only one unlit
#: column the sun's side ray and the cloud's edge ran into the first digit.
GLYPH_R = 13
#: The words' box on ``weather``: columns TEXT_X to TEXT_R.
TEXT_X = GLYPH_R + 3
TEXT_R = STAGE_X + STAGE_W - 1
#: ``weather``: line 1 (7-row font, rows 1-7) and line 2 (small font, rows 9-13).
LINE1_Y = 1
LINE2_Y = 9
#: Least gap, in unlit columns, between the temperature and the high and low.
HILO_GAP = 3

# =========================================================================================
# WMO weather code -> glyph
# =========================================================================================

#: Glyph name -> the WMO codes it stands for. Every other code is the plain cloud.
GLYPH_CODES: dict[str, tuple[int, ...]] = {
    "clear": (0, 1),
    "partly": (2,),
    "cloud": (3,),
    "fog": (45, 48),
    "rain": (*range(51, 58), *range(61, 68), *range(80, 83)),
    "snow": (*range(71, 78), 85, 86),
    "thunder": (95, 96, 97, 98, 99),
}
FALLBACK_GLYPH = "cloud"
_CODE_GLYPH = {c: g for g, codes in GLYPH_CODES.items() for c in codes}
GLYPHS: tuple[str, ...] = tuple(GLYPH_CODES)


def glyph_for(code: int) -> str:
    """The glyph for a WMO weather code."""
    return _CODE_GLYPH.get(code, FALLBACK_GLYPH)


#: Stars beside the moon on a clear night: the first always lit, the second on every other
#: two seconds.
STARS: tuple[tuple[int, int], ...] = ((11, 2), (9, 6))


def _alternate(a: str, b: str, period_s: int) -> Callable[[int], str]:
    return lambda sec: a if (sec // period_s) % 2 == 0 else b


#: (glyph, is_day) -> the icon to show at a given whole second.
_ICONS: dict[tuple[str, bool], Callable[[int], str]] = {
    ("clear", True): _alternate("wx_sun_a", "wx_sun_b", 2),
    ("clear", False): lambda sec: "moon",
    ("partly", True): lambda sec: "wx_partly_day",
    ("partly", False): lambda sec: "wx_partly_night",
    ("cloud", True): lambda sec: "wx_cloud",
    ("fog", True): _alternate("wx_fog_a", "wx_fog_b", 2),
    ("rain", True): _alternate("wx_rain_a", "wx_rain_b", 1),
    ("snow", True): _alternate("wx_snow_a", "wx_snow_b", 1),
    ("thunder", True): _alternate("wx_thunder_a", "wx_thunder_b", 1),
}


def glyph_icon(glyph: str, is_day: bool, sec: int) -> str:
    """The icon name drawn for ``glyph`` at whole second ``sec``. Glyphs with no night art
    of their own use the day art."""
    fn = _ICONS.get((glyph, is_day)) or _ICONS[(glyph, True)]
    return fn(sec)


def draw_glyph(f: Frame, cfg: Config, glyph: str, is_day: bool, sec: int) -> None:
    """The weather glyph in the 16x16 box at the left."""
    draw_icon(f, glyph_icon(glyph, is_day, sec), 0, 0, cfg)
    if glyph == "clear" and not is_day:
        for i, (x, y) in enumerate(STARS):
            if i == 0 or (sec // 2) % 2 == 0:
                f.pixel(x, y, cfg.rgb(STAR_ROLE))


# =========================================================================================
# Words
# =========================================================================================


def date_text(d: date) -> str:
    """``NOV 18``: month and day of month."""
    return f"{MONTHS[d.month - 1]} {d.day}"


def whole(x: float) -> int:
    """``x`` rounded to the nearest whole number, halves up."""
    return math.floor(x + 0.5)


def temp_text(w: Weather) -> str:
    """``54°``, ``-5°``: the temperature, rounded, with a degree sign and no unit letter."""
    return f"{whole(w.temp_f)}\N{DEGREE SIGN}"


def hilo_text(w: Weather) -> str | None:
    """``61/48``: today's high and low, or None when either is missing."""
    if w.high_f is None or w.low_f is None:
        return None
    return f"{whole(w.high_f)}/{whole(w.low_f)}"


def _sec(now: datetime) -> int:
    return math.floor(now.timestamp())


def draw_centred(f: Frame, cfg: Config, text: str, y: int, role: str) -> None:
    """``text`` in the 7-row font, centred inside the border (columns 1-50)."""
    font = large_font()
    left, right = STAGE_X, STAGE_X + STAGE_W - 1
    x = left + max(0, (right - left + 1 - font.width(text)) // 2)
    put_text(f, x, y, text, cfg.rgb(role), font, right=right)


def date_frame(cfg: Config, text: str) -> Frame:
    """A page with only the date line."""
    f = Frame(W, H)
    draw_centred(f, cfg, text, DATE_Y, DATE_ROLE)
    return f


# =========================================================================================
# The pages
# =========================================================================================


@page("weather")
def weather_page(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    """Glyph; temperature and high/low on line 1; the date on line 2."""
    day = date_text(cfg.local(now).date())
    w = state.weather
    if w is None:
        return date_frame(cfg, day)
    f = Frame(W, H)
    draw_glyph(f, cfg, glyph_for(w.code), w.is_day, _sec(now))
    big, small = large_font(), small_font()
    temp = temp_text(w)
    put_text(f, TEXT_X, LINE1_Y, temp, cfg.rgb(TEMP_ROLE), big, right=TEXT_R)
    hilo = hilo_text(w)
    if hilo is not None:
        hx = TEXT_R - small.width(hilo) + 1
        if hx - (TEXT_X + big.width(temp)) >= HILO_GAP:
            # Bottom-aligned with the temperature: same baseline.
            put_text(f, hx, LINE1_Y + 7 - 5, hilo, cfg.rgb(HILO_ROLE), small, right=TEXT_R)
    put_text(f, TEXT_X, LINE2_Y, day, cfg.rgb(DATE_ROLE), small, right=TEXT_R)
    return f


#: ``weather_big``: the double-size temperature (10 rows) and the stacked date.
BIG_Y = 3
BIG_DATE_GAP = 2


@page("weather_big")
def weather_big_page(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    """Glyph; the temperature at double size; the month over the day to its right."""
    local = cfg.local(now).date()
    w = state.weather
    if w is None:
        return date_frame(cfg, date_text(local))
    f = Frame(W, H)
    draw_glyph(f, cfg, glyph_for(w.code), w.is_day, _sec(now))
    small, big = small_font(), large_font()
    # The month in the small font with narrow M and W, so MAR, MAY and NOV are no wider than
    # the rest and a three-digit temperature always fits beside the date.
    narrow = condensed_small_font()
    month, dom = MONTHS[local.month - 1], str(local.day)
    date_w = max(narrow.width(month), small.width(dom))
    date_x = TEXT_R - date_w + 1
    digits = str(whole(w.temp_f))
    dw = small.width(digits, 2)
    deg = "\N{DEGREE SIGN}"
    # The degree sign is the 7-row font's small ring at the top of the big digits; it is
    # dropped before anything would crowd the date. The temperature is centred between the
    # glyph and the date.
    left, right = GLYPH_R + 2, date_x - 1 - BIG_DATE_GAP
    tw = dw + 1 + big.width(deg)
    show_deg = tw <= right - left + 1
    if not show_deg:
        tw = dw
    x = left + max(0, (right - left + 1 - tw) // 2)
    put_text(f, x, BIG_Y, digits, cfg.rgb(TEMP_ROLE), small, right=right, scale=2)
    if show_deg:
        put_text(f, x + dw + 1, BIG_Y, deg, cfg.rgb(TEMP_ROLE), big, right=right)
    for text, y, font in ((month, BIG_Y, narrow), (dom, BIG_Y + 5 + 1, small)):
        x = TEXT_R - font.width(text) + 1
        put_text(f, x, y, text, cfg.rgb(DATE_ROLE), font, right=TEXT_R)
    return f
