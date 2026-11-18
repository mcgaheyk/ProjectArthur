"""The feeder: Whisker → mapping → render → the clock, as one long-running service.

    uv run --env-file .env python -m arthur.feeder [--clock URL] [--config PATH]
                                                   [--log-dir DIR] [--app NAME]

Pipeline: ``source → RobotSnapshot → mapping → DisplayState → render → Frame → transport``.
The source (:class:`~arthur.sources.whisker.WhiskerSource`) holds the Whisker connection in a
task of its own and writes the spike's log files; this loop asks it what it knows on every
beat of the wall clock (:data:`~arthur.timebase.SHOW_FPS` a second, lined up with whole
seconds), maps and draws the frame (52x16, each kind's default variant, as the studio does)
and pushes it when it changed or the heartbeat is due (:class:`~arthur.pusher.Pusher`),
with ``lifetimeMs`` = ``ttl_s`` so the clock marks the frame stale by itself if the feeder
stops (D-0004).

- The clock's address is ``--clock`` or else the ``CLOCK_URL`` environment variable; a bare
  host gets ``http://``. Neither: exit status 2.
- ``config/display.toml`` (or ``--config``) is reloaded when its modification time changes; a
  broken edit is logged and the last good config kept.
- A mapping or render error is logged and nothing is pushed for that tick: the clock then
  marks the frame stale, which is the designed failure. No fallback frame is drawn.
- SIGINT/SIGTERM stop it: the Whisker account is disconnected, the transport closed and the
  log's exit line written. The clock is not cleared; the last frame goes stale by itself.
- Times passed to mapping and render are aware UTC, as the studio's feeder passes them.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TextIO

from arthur.config import DEFAULT_PATH, Config, ConfigError, load_config
from arthur.mapping import to_display_state
from arthur.model import FeederHealth, RobotSnapshot
from arthur.pusher import Pusher
from arthur.pushtest import clock_url
from arthur.render import render
from arthur.spikelog import ScrubFilter, read_credentials, scrub
from arthur.timebase import until_next_tick
from arthur.transports import AwtrixNgTransport
from arthur.transports.base import DisplayTransport

log = logging.getLogger("arthur.feeder")

EXIT_OK = 0
EXIT_USAGE = 2
DEFAULT_APP = "litter"
#: How often the config file's modification time is checked.
CONFIG_CHECK_S = 1.0
#: After the source task dies of a bug, start it again after this long.
SOURCE_RESTART_S = 30.0


class Source(Protocol):
    def view(self, now: datetime) -> tuple[RobotSnapshot | None, FeederHealth]: ...

    async def run(self, stop: asyncio.Event) -> None: ...

    def update_config(self, cfg: Config) -> None: ...


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m arthur.feeder",
        description="Show what the Litter-Robot is doing on the clock.",
        epilog="example:\n  uv run --env-file .env python -m arthur.feeder\n\n"
               "Needs WHISKER_USERNAME and WHISKER_PASSWORD in the environment, and the clock "
               "in --clock or CLOCK_URL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--clock", metavar="URL",
                   help="the clock's address (default: the CLOCK_URL environment variable)")
    p.add_argument("--config", type=Path, default=None,
                   help="path to display.toml (default: config/display.toml)")
    p.add_argument("--log-dir", type=Path, default=Path("logs"),
                   help="where the robot's log files go (default: logs)")
    p.add_argument("--app", default=DEFAULT_APP,
                   help=f"pushed app name on the clock (default {DEFAULT_APP})")
    return p


class _ScrubFormatter(logging.Formatter):
    """Scrubs the whole formatted line, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


def setup_logging(stream: TextIO | None = None) -> logging.Handler:
    """INFO from our own loggers, WARNING from libraries, to stderr, tokens scrubbed."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_ScrubFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(ScrubFilter())
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logging.getLogger("arthur").setLevel(logging.INFO)
    return handler


class ConfigWatcher:
    """Reloads the config file when its modification time changes."""

    def __init__(self, path: Path, cfg: Config) -> None:
        self.path = Path(path)
        self.cfg = cfg
        self._mtime = self._stat()

    def _stat(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def check(self) -> Config | None:
        """The new config if the file changed and loads; None otherwise."""
        mtime = self._stat()
        if mtime == self._mtime:
            return None
        self._mtime = mtime
        try:
            cfg = load_config(self.path)
        except (OSError, ConfigError, ValueError) as exc:
            log.error("config %s not reloaded, keeping the last good one: %s: %s",
                      self.path, type(exc).__name__, exc)
            return None
        self.cfg = cfg
        log.info("config reloaded from %s", self.path)
        return cfg


class Feeder:
    """The loop. ``source.run`` is started beside it as a task."""

    def __init__(
        self,
        source: Source,
        transport: DisplayTransport,
        cfg: Config,
        *,
        config_path: Path | None = None,
        clock_label: str = "",
        time_fn: Callable[[], float] = time.monotonic,
        wall_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.source = source
        self.transport = transport
        self.cfg = cfg
        self.watcher = ConfigWatcher(config_path, cfg) if config_path is not None else None
        self.clock_label = clock_label
        self.time_fn = time_fn
        self.wall_fn = wall_fn
        self.sleep = sleep
        self.pusher = Pusher(transport, time_fn=time_fn)
        self.shown: tuple[str, str | None] | None = None  # (kind, detail) last drawn
        self._whisker_problem: str | None = None  # the no_data reason being reported
        self._whisker_reported = False
        self._draw_error: str | None = None
        self._next_config_check = time_fn()
        self._failed_pushes = 0

    # -- one tick ---------------------------------------------------------------------------

    def _reload(self) -> None:
        if self.watcher is None or self.time_fn() < self._next_config_check:
            return
        self._next_config_check = self.time_fn() + CONFIG_CHECK_S
        cfg = self.watcher.check()
        if cfg is not None:
            self.cfg = cfg
            with contextlib.suppress(Exception):
                self.source.update_config(cfg)

    def _note_state(self, kind: str, detail: str | None) -> None:
        shown = (kind, detail if kind == "no_data" else None)
        if shown != self.shown:
            new = kind + (f" ({detail})" if kind == "no_data" and detail else "")
            if self.shown is None:
                log.info("display: %s", new)
            else:
                old = self.shown[0] + (f" ({self.shown[1]})" if self.shown[1] else "")
                log.info("display: %s → %s", old, new)
            self.shown = shown
        # Whisker contact, from the mapping's own verdict. Waiting for the first data at
        # start is not a loss.
        problem = detail if kind == "no_data" else None
        if problem == "no_snapshot" and not self._whisker_reported:
            problem = None
        if problem != self._whisker_problem:
            if problem is not None:
                log.warning("Whisker contact lost (%s)", problem)
            elif self._whisker_reported:
                log.info("Whisker contact regained")
            self._whisker_reported = True
            self._whisker_problem = problem

    async def tick(self) -> None:
        self._reload()
        cfg = self.cfg
        now = self.wall_fn()
        try:
            snapshot, health = self.source.view(now)
            state = to_display_state(snapshot, health, now, cfg)
            frame = render(state, now, cfg)
        except Exception as exc:  # nothing is pushed; the clock goes stale
            msg = f"{type(exc).__name__}: {exc}"
            if msg != self._draw_error:
                log.exception("could not map or draw the screen; nothing pushed until it can")
                self._draw_error = msg
            return
        if self._draw_error is not None:
            log.info("drawing again")
            self._draw_error = None
        self._note_state(state.kind, state.detail)

        was = self.pusher.reachable
        result = await self.pusher.offer(frame, cfg.timings.ttl_s, cfg.timings.heartbeat_s)
        if result is None:
            return
        if result.error is not None:
            self._failed_pushes += 1
            if was is not False:
                log.warning("clock unreachable: %s (retrying every %g s)",
                            result.error, self.pusher.retry_after_s)
        elif was is not True:
            if was is None:
                log.info("first frame pushed to %s", self.clock_label or "the clock")
            else:
                log.info("clock reachable again (after %d failed pushes)", self._failed_pushes)
            self._failed_pushes = 0

    # -- the service ------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        source_task = asyncio.create_task(self.source.run(stop))
        restart_at: float | None = None
        try:
            while not stop.is_set():
                if source_task.done() and not stop.is_set():
                    if restart_at is None:
                        exc = None if source_task.cancelled() else source_task.exception()
                        log.error("the Whisker source stopped unexpectedly (%s); restarting "
                                  "in %g s", exc and f"{type(exc).__name__}: {exc}",
                                  SOURCE_RESTART_S)
                        restart_at = self.time_fn() + SOURCE_RESTART_S
                    elif self.time_fn() >= restart_at:
                        source_task = asyncio.create_task(self.source.run(stop))
                        restart_at = None
                await self.tick()
                if stop.is_set():
                    break
                # Wake on the next beat of the wall clock. A tick that ran long (a slow push
                # is awaited inside it) lands on a later beat; the ones it covered are dropped.
                await self.sleep(until_next_tick(self.wall_fn()))
        finally:
            stop.set()
            try:
                await asyncio.wait_for(source_task, timeout=10)
            except Exception as exc:  # noqa: BLE001
                log.error("Whisker source did not stop cleanly: %s: %s", type(exc).__name__,
                          exc)
            with contextlib.suppress(Exception):
                await self.transport.close()


async def run(
    opts: argparse.Namespace,
    cfg: Config,
    base_url: str,
    credentials: tuple[str, str] | None,
    *,
    source: Source | None = None,
    transport: DisplayTransport | None = None,
    stop: asyncio.Event | None = None,
    time_fn: Callable[[], float] = time.monotonic,
    wall_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    install_signals: bool = True,
) -> int:
    """Run the feeder until SIGINT/SIGTERM (or ``stop``)."""
    stop = stop or asyncio.Event()
    if install_signals:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
    if source is None:
        from arthur.sources.whisker import WhiskerSource

        source = WhiskerSource(cfg, credentials=credentials, log_dir=opts.log_dir,
                               wall_fn=wall_fn)
    if transport is None:
        transport = AwtrixNgTransport(base_url, app_name=opts.app, time_fn=time_fn)
    config_path = Path(opts.config) if opts.config else DEFAULT_PATH
    log.info("feeder started: clock %s, app %s, config %s, logs %s", base_url, opts.app,
             config_path, opts.log_dir)
    feeder = Feeder(source, transport, cfg, config_path=config_path, clock_label=base_url,
                    time_fn=time_fn, wall_fn=wall_fn, sleep=sleep)
    try:
        await feeder.run(stop)
    finally:
        log.info("feeder stopped; the last frame goes stale on the clock by itself")
    return EXIT_OK


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None,
         **run_kw: Any) -> int:
    opts = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    raw = opts.clock or (env.get("CLOCK_URL") or "").strip()
    if not raw:
        print("feeder: no clock address: pass --clock URL or set CLOCK_URL", file=sys.stderr)
        return EXIT_USAGE
    try:
        cfg = load_config(opts.config)
    except (OSError, ConfigError, ValueError) as exc:
        print(f"feeder: cannot load the config: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    setup_logging()
    creds = read_credentials(env)
    credentials = creds if isinstance(creds, tuple) else None
    try:
        return asyncio.run(run(opts, cfg, clock_url(raw), credentials, **run_kw))
    except KeyboardInterrupt:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
