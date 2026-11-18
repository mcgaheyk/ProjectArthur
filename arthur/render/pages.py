"""The idle show's information pages (D-0019), registered with :func:`idle_show.page`.

A page is ``(state, now, cfg) -> Frame`` at 52x16 with the border empty; it runs on whole
seconds like the home page. The player dissolves into and out of it.

``weather`` is the page a page cue plays by default. For now it shows the date alone, centred
in the 7-row font, which is also what it must show when there is no fresh weather reading
(``state.weather`` is None): never an old temperature. The weather itself comes in a later
step, in this file.
"""

from __future__ import annotations

from datetime import date, datetime

from arthur.config import Config
from arthur.model import DisplayState
from arthur.render.font import large_font
from arthur.render.frame import Frame
from arthur.render.idle_show import STAGE_W, STAGE_X, H, W, page
from arthur.render.text import put_text

#: ``date.weekday()`` order: Monday first.
WEEKDAYS: tuple[str, ...] = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MONTHS: tuple[str, ...] = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)
#: Top row of the date line: the 7-row capitals sit on rows 4-10, centred.
DATE_Y = (H - 7) // 2
#: The date's colour role.
DATE_ROLE = "text"


def date_text(d: date) -> str:
    """``WED NOV 18``: weekday, month and day of month."""
    return f"{WEEKDAYS[d.weekday()]} {MONTHS[d.month - 1]} {d.day}"


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


@page("weather")
def weather_page(state: DisplayState, now: datetime, cfg: Config) -> Frame:
    """The date in the local zone. (The weather is drawn here in a later step.)"""
    return date_frame(cfg, date_text(cfg.local(now).date()))
