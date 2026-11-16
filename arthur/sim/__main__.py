"""Start the simulator and studio: ``uv run python -m arthur.sim [--port 7001] [--config PATH]``.

Binds to 127.0.0.1 only. Open http://127.0.0.1:7001/ for the studio. The fake clocks answer at
``/clock/32x8/api/v1/...`` and ``/clock/52x16/api/v1/...``; the bare ``/api/v1/...`` is the
size set in the config.
"""

from __future__ import annotations

import argparse
import logging
import sys

from aiohttp import web

HOST = "127.0.0.1"


def main(argv: list[str] | None = None) -> None:
    # Live reload re-imports the render modules; never trust a .pyc that might be stale when a
    # file is edited twice within its mtime resolution.
    sys.dont_write_bytecode = True

    from arthur.sim.server import create_app

    ap = argparse.ArgumentParser(prog="python -m arthur.sim", description=__doc__)
    ap.add_argument("--port", type=int, default=7001)
    ap.add_argument("--config", default=None, help="display config (default config/display.toml)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    app = create_app(args.config, feeder_base_url=f"http://{HOST}:{args.port}")
    print(f"ProjectArthur studio: http://{HOST}:{args.port}/", flush=True)
    web.run_app(app, host=HOST, port=args.port, print=None)


if __name__ == "__main__":
    main()
