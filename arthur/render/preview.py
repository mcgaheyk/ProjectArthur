"""Print screens as text (and optionally write PNGs).

    uv run python -m arthur.render.preview --all
    uv run python -m arthur.render.preview in_use --variant door --stale
    uv run python -m arthur.render.preview --all --png /tmp/screens
    uv run python -m arthur.render.preview --sheet /tmp/sheets             # every fixture
    uv run python -m arthur.render.preview in_use --sheet /tmp/s --frames 8 --step 1
    uv run python -m arthur.render.preview idle_rat --at 20                # 20 s into it
    uv run python -m arthur.render.preview idle_bird --sheet /tmp/s --frames 16 --step 0.125
    uv run python -m arthur.render.preview idle_page --weather 61,day,41,47,35
    uv run python -m arthur.render.preview idle_page --weather glyphs --sheet /tmp/s --frames 4

``--sheet DIR`` writes a contact sheet instead of text: one PNG with every fixture x variant
(or just the named one), each tile labelled in the project's own font. With
``--frames N`` each tile becomes a strip of N frames ``--step`` seconds apart, so an
animation can be read side by side. ``--at SECONDS`` shows (or starts the strip at) that many
seconds after the fixture's own time; fractions work, so the idle show's fixtures
(``idle_page``, ``idle_rat``, ``idle_bird``) can be stepped at their 8 frames a second.

``--weather SPEC`` replaces the fixture's weather reading (repeat it to show several; each is
read as if fetched at the fixture's own time). SPEC is ``none`` (no fresh reading),
``glyphs`` (one reading per weather glyph, day and night), or
``CODE[,day|night[,TEMP[,HIGH,LOW]]]``: a WMO code, then day (default) or night, the
temperature (default 54) and today's high and low (default 61 and 48; ``-`` for missing).

A positional name may be a display kind (uses the fixture of the same name) or any fixture name
from ``arthur.fixtures`` (e.g. ``ready_laser_dirty``). One character per pixel; ``.`` is off; the
legend says which character is which palette role.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from arthur.config import Config, format_size, load_config
from arthur.fixtures import FIXTURES, Fixture
from arthur.model import Weather
from arthur.render.font import small_font
from arthur.render.frame import RGB, Frame, overlay_stale
from arthur.render.screens import list_kinds, list_variants, render


def legend_for(cfg: Config) -> dict[RGB, str]:
    """Colour → character, from ``[palette_chars]``. Off is ``.``."""
    out: dict[RGB, str] = {}
    for role, ch in cfg.palette.chars.items():
        out.setdefault(cfg.rgb(role), ch)
    out[cfg.rgb("off")] = "."
    return out


def legend_text(cfg: Config) -> str:
    return "legend: " + "  ".join(f"{c}={r}" for r, c in cfg.palette.chars.items())


def frame_text(frame: Frame, cfg: Config) -> str:
    return frame.to_ascii(legend_for(cfg))


def names_text(cfg: Config) -> str:
    """What ``--list`` prints: each display kind with its variants, then every fixture name."""
    lines = [f"{kind:15} {', '.join(list_variants(kind, cfg.size))}" for kind in list_kinds()]
    return "\n".join([*lines, "", "fixtures: " + ", ".join(FIXTURES)])


WEATHER_DEFAULTS = (54.0, 61.0, 48.0)


def parse_weather(spec: str, fx: Fixture) -> list[tuple[str, Weather | None]]:
    """``--weather SPEC`` as labelled readings fetched at the fixture's time (see the module
    docstring). Raises ValueError on a bad SPEC."""
    from arthur.render.pages import GLYPH_CODES

    spec = spec.strip().lower()
    if spec == "none":
        return [("no weather", None)]
    if spec == "glyphs":
        out: list[tuple[str, Weather | None]] = []
        for is_day in (True, False):
            for codes in GLYPH_CODES.values():
                out += parse_weather(f"{codes[0]},{'day' if is_day else 'night'}", fx)
        return out
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) not in (1, 2, 3, 5):
        raise ValueError(f"--weather {spec!r}: expected CODE[,day|night[,TEMP[,HIGH,LOW]]]")
    code = int(parts[0])
    if len(parts) > 1 and parts[1] not in ("day", "night"):
        raise ValueError(f"--weather {spec!r}: the second part is day or night")
    is_day = len(parts) < 2 or parts[1] == "day"
    temp, high, low = WEATHER_DEFAULTS
    if len(parts) > 2:
        temp = float(parts[2])
    if len(parts) > 3:
        high = None if parts[3] == "-" else float(parts[3])
        low = None if parts[4] == "-" else float(parts[4])
    label = f"{code} {'day' if is_day else 'night'} {temp:g}"
    if high is not None and low is not None:
        label += f" {high:g} {low:g}"
    return [(label, Weather(temp_f=temp, code=code, is_day=is_day, high_f=high, low_f=low,
                            fetched_at=fx.now))]


def with_weather(fx: Fixture, specs: list[str] | None) -> list[tuple[str, Fixture]]:
    """The fixture once per ``--weather`` reading (or once, unchanged, without any), with a
    label suffix saying which reading it carries."""
    if not specs:
        return [("", fx)]
    return [(f" [{label}]", replace(fx, weather=w))
            for spec in specs for label, w in parse_weather(spec, fx)]


def render_fixture(
    fx: Fixture, cfg: Config, size: tuple[int, int] | None, variant: str | None, stale: bool,
    at: float = 0.0,
) -> Frame:
    """The fixture mapped at its own time and drawn ``at`` seconds later."""
    from datetime import timedelta

    state = fx.display_state(cfg)
    frame = render(state, fx.now + timedelta(seconds=at), cfg, size=size, variant=variant)
    return overlay_stale(frame, cfg.rgb("stale")) if stale else frame


SHEET_BG: RGB = (0x1C, 0x1A, 0x18)
SHEET_GAP: RGB = (0x10, 0x10, 0x10)
SHEET_INK: RGB = (0xE0, 0xD8, 0xD0)
SHEET_SUB: RGB = (0x90, 0x88, 0x80)


def _led_blit(canvas: Frame, frame: Frame, x0: int, y0: int, led: int) -> None:
    """Draw ``frame`` onto ``canvas`` with each pixel as a ``led``-px LED and a 1-px gap."""
    gap = 1 if led >= 3 else 0
    canvas.rect(x0, y0, frame.width * led, frame.height * led, SHEET_GAP)
    for y in range(frame.height):
        for x in range(frame.width):
            canvas.rect(x0 + x * led, y0 + y * led, led - gap, led - gap,
                        frame.pixels[y * frame.width + x])


def contact_sheet(
    tiles: list[tuple[str, list[tuple[str, Frame]]]], led: int, cols: int, label_scale: int = 2
) -> Frame:
    """Tiles of labelled frame strips on one canvas.

    Each tile is ``(label, [(sub_label, frame), ...])``: the frames sit side by side with
    their sub-labels under them and the tile label above.
    """
    font = small_font()
    pad = 6 * label_scale
    lab_h = 7 * label_scale
    fw, fh = tiles[0][1][0][1].size
    n = max(len(t[1]) for t in tiles)
    has_sub = any(sub for _, strip in tiles for sub, _ in strip)
    tile_w = n * (fw * led) + (n - 1) * pad
    tile_h = lab_h + fh * led + (lab_h if has_sub else 0)
    cols = max(1, min(cols, len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    canvas = Frame(pad + cols * (tile_w + pad), pad + rows * (tile_h + pad), SHEET_BG)
    for i, (label, strip) in enumerate(tiles):
        tx = pad + (i % cols) * (tile_w + pad)
        ty = pad + (i // cols) * (tile_h + pad)
        font.draw(canvas, tx, ty, label, SHEET_INK, scale=label_scale)
        for j, (sub, frame) in enumerate(strip):
            fx = tx + j * (fw * led + pad)
            _led_blit(canvas, frame, fx, ty + lab_h, led)
            if sub:
                font.draw(canvas, fx, ty + lab_h + fh * led + label_scale, sub, SHEET_SUB,
                          scale=label_scale)
    return canvas


def write_sheet(
    names: list[str], cfg: Config, out: Path, *,
    variant: str | None, frames: int, step: float, stale: bool, led: int | None,
    at: float = 0.0, weather: list[str] | None = None,
) -> Path:
    """Write one contact sheet; returns its path."""
    from datetime import timedelta

    out.mkdir(parents=True, exist_ok=True)
    tiles: list[tuple[str, list[tuple[str, Frame]]]] = []
    for name in names:
        for suffix_w, fx in with_weather(FIXTURES[name], weather):
            available = list_variants(fx.kind, cfg.size)
            vs = available if variant in (None, "all") else [variant if variant in available
                                                            else available[0]]
            state = fx.display_state(cfg)
            for v in vs:
                strip = []
                for k in range(frames):
                    t = at + k * step
                    fr = render(state, fx.now + timedelta(seconds=t), cfg, variant=v)
                    if stale:
                        fr = overlay_stale(fr, cfg.rgb("stale"))
                    strip.append((f"+{t:g}s" if frames > 1 else "", fr))
                tiles.append((f"{name} / {v}{suffix_w}", strip))
    cols = 1 if frames > 1 else 3
    sheet = contact_sheet(tiles, led or 6, cols)
    tag = names[0] if len(names) == 1 else "all"
    suffix = f"__frames{frames}" if frames > 1 else ""
    path = out / f"sheet__{tag}__{format_size(cfg.size)}{suffix}.png"
    path.write_bytes(sheet.to_png(1))
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m arthur.render.preview", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", nargs="?", help="display kind or fixture name")
    p.add_argument("--all", action="store_true", help="every display kind")
    p.add_argument("--fixtures", action="store_true", help="every fixture, not just one per kind")
    p.add_argument("--variant", help="variant name; 'all' shows every variant")
    p.add_argument("--stale", action="store_true", help="overlay the clock's stale border")
    p.add_argument("--png", metavar="DIR", help="also write PNGs (scale 12) into DIR")
    p.add_argument("--sheet", metavar="DIR", help="write contact-sheet PNGs into DIR")
    p.add_argument("--frames", type=int, default=1, help="frames per tile in a sheet")
    p.add_argument("--step", type=float, default=1.0, help="seconds between sheet frames")
    p.add_argument("--at", type=float, default=0.0, metavar="SECONDS",
                   help="show the fixture this many seconds after its own time (fractions "
                   "allowed); a sheet's strip starts there")
    p.add_argument("--weather", action="append", metavar="SPEC",
                   help="replace the fixture's weather: none, glyphs, or "
                   "CODE[,day|night[,TEMP[,HIGH,LOW]]] (repeatable)")
    p.add_argument("--led", type=int, help="pixels per LED in a sheet (default 6)")
    p.add_argument("--config", help="path to display.toml")
    p.add_argument("--list", action="store_true", help="list kinds, variants and fixtures")
    args = p.parse_args(argv)

    cfg = load_config(args.config)

    if args.list:
        print(names_text(cfg))
        return 0

    if args.fixtures:
        names = list(FIXTURES)
    elif args.all:
        names = list(list_kinds())
    elif args.sheet and not args.name:
        names = list(FIXTURES)
    elif args.name:
        if args.name not in FIXTURES:
            print(f"unknown kind or fixture {args.name!r}; try --list", file=sys.stderr)
            return 2
        names = [args.name]
    else:
        p.print_help()
        return 2

    if args.weather:
        try:
            for spec in args.weather:
                parse_weather(spec, FIXTURES[names[0]])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2

    if args.sheet:
        print(write_sheet(names, cfg, Path(args.sheet), variant=args.variant,
                          frames=max(1, args.frames), step=args.step, stale=args.stale,
                          led=args.led, at=args.at, weather=args.weather))
        return 0
    png_dir = Path(args.png) if args.png else None
    if png_dir:
        png_dir.mkdir(parents=True, exist_ok=True)

    print(legend_text(cfg))
    size = format_size(cfg.size)
    for name, suffix_w, fx in (
        (n, sw, f) for n in names for sw, f in with_weather(FIXTURES[n], args.weather)
    ):
        kind = fx.kind
        available = list_variants(kind, cfg.size)
        if args.variant == "all":
            variants: list[str | None] = list(available)
        elif args.variant and args.variant in available:
            variants = [args.variant]
        else:
            variants = [available[0]]
        for v in variants:
            frame = render_fixture(fx, cfg, None, v, args.stale, at=args.at)
            tag = f"{name}{suffix_w} [{kind}] {size} variant={v}"
            if args.at:
                tag += f" at +{args.at:g}s"
            if len(available) > 1:
                tag += f" (of {', '.join(available)})"
            if args.stale:
                tag += " +stale"
            print(f"\n{tag}")
            print(frame_text(frame, cfg))
            if png_dir:
                wtag = "__" + "_".join(suffix_w.strip(" []").split()) if suffix_w else ""
                fn = f"{name}{wtag}__{size}__{v}{'__stale' if args.stale else ''}.png"
                (png_dir / fn).write_bytes(frame.to_png(12, gap=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
