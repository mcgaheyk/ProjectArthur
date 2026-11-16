"""Print screens as text (and optionally write PNGs).

    uv run python -m arthur.render.preview --all
    uv run python -m arthur.render.preview waiting --size 52x16 --variant bar --stale
    uv run python -m arthur.render.preview --all --png /tmp/screens

A positional name may be a display kind (uses the fixture of the same name) or any fixture name
from ``arthur.fixtures`` (e.g. ``ready_badges``). One character per pixel; ``.`` is off; the
legend says which character is which palette role.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arthur.config import Config, format_size, load_config
from arthur.fixtures import FIXTURES, Fixture
from arthur.mapping import to_display_state
from arthur.render.frame import RGB, Frame, overlay_stale
from arthur.render.screens import list_kinds, list_sizes, list_variants, render


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


def render_fixture(
    fx: Fixture, cfg: Config, size: tuple[int, int], variant: str | None, stale: bool
) -> Frame:
    state = to_display_state(fx.snapshot, fx.health, fx.now, cfg)
    frame = render(state, fx.now, cfg, size=size, variant=variant)
    return overlay_stale(frame, cfg.rgb("stale")) if stale else frame


def _sizes(arg: str) -> list[tuple[int, int]]:
    if arg == "both":
        return list(list_sizes())
    w, h = (int(p) for p in arg.lower().split("x"))
    if (w, h) not in list_sizes():
        raise SystemExit(f"--size must be 32x8, 52x16 or both, not {arg!r}")
    return [(w, h)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m arthur.render.preview", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", nargs="?", help="display kind or fixture name")
    p.add_argument("--all", action="store_true", help="every display kind")
    p.add_argument("--fixtures", action="store_true", help="every fixture, not just one per kind")
    p.add_argument("--size", default="both", help="32x8, 52x16 or both (default)")
    p.add_argument("--variant", help="variant name; 'all' shows every variant")
    p.add_argument("--stale", action="store_true", help="overlay the clock's stale border")
    p.add_argument("--png", metavar="DIR", help="also write PNGs (scale 12) into DIR")
    p.add_argument("--config", help="path to display.toml")
    p.add_argument("--list", action="store_true", help="list kinds, variants and fixtures")
    args = p.parse_args(argv)

    cfg = load_config(args.config)

    if args.list:
        for kind in list_kinds():
            vs = "; ".join(
                f"{format_size(s)}: {', '.join(list_variants(kind, s))}" for s in list_sizes()
            )
            print(f"{kind:15} {vs}")
        print("\nfixtures: " + ", ".join(FIXTURES))
        return 0

    if args.fixtures:
        names = list(FIXTURES)
    elif args.all:
        names = list(list_kinds())
    elif args.name:
        if args.name not in FIXTURES:
            print(f"unknown kind or fixture {args.name!r}; try --list", file=sys.stderr)
            return 2
        names = [args.name]
    else:
        p.print_help()
        return 2

    sizes = _sizes(args.size)
    png_dir = Path(args.png) if args.png else None
    if png_dir:
        png_dir.mkdir(parents=True, exist_ok=True)

    print(legend_text(cfg))
    for name in names:
        fx = FIXTURES[name]
        kind = fx.kind
        for size in sizes:
            available = list_variants(kind, size)
            if args.variant == "all":
                variants: list[str | None] = list(available)
            elif args.variant and args.variant in available:
                variants = [args.variant]
            else:
                variants = [available[0]]
            for v in variants:
                frame = render_fixture(fx, cfg, size, v, args.stale)
                tag = f"{name} [{kind}] {format_size(size)} variant={v}"
                if len(available) > 1:
                    tag += f" (of {', '.join(available)})"
                if args.stale:
                    tag += " +stale"
                print(f"\n{tag}")
                print(frame_text(frame, cfg))
                if png_dir:
                    fn = f"{name}__{format_size(size)}__{v}{'__stale' if args.stale else ''}.png"
                    (png_dir / fn).write_bytes(frame.to_png(12, gap=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
