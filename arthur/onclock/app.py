"""The program on the clock: robot source → mapping → drawing → AWTRIX, on the beat (D-0029).

:class:`ClockApp` is :class:`arthur.feeder.Feeder` for the TC002 itself, under MicroPython
1.29 (and CPython, for the tests). On every beat of the wall clock (8 a second, lined up with
whole seconds, :mod:`arthur.timebase`; a beat that runs long lands on a later one, never
queued) it asks the source what it knows, maps it with the schedule's cue and the weather,
draws the frame and pushes it when it changed or the heartbeat is due, with ``lifetimeMs``
from ``ttl_s`` and ``lifetimeExpiry: "mark"``, so AWTRIX marks the frame stale by itself if
this program stops (D-0004).

What it keeps from the Mac's feeder, rule for rule:

- A mapping or drawing error is logged once and nothing is pushed: the frame goes stale on
  the clock, which is the designed failure. No fallback frame is drawn.
- A decoration never stops the display (D-0019): if asking the weather fails the beat goes on
  without it; if drawing a page or scene fails, the same state is drawn without its cue, which
  is the home page. Each is logged once.
- The source and the weather run as tasks beside the loop; one that dies of a bug is logged
  and started again after :data:`SOURCE_RESTART_S`.
- Contact with Whisker lost and regained, the display changing, the clock unreachable and
  reachable again: one console line each.

What it adds for the clock:

- **At start** it loads the fonts, the art and the TLS certificates first (the biggest pieces
  of memory, while the heap is still in one piece), then waits for AWTRIX's API to answer,
  retrying without end, before it starts anything else.
- **The scene's plan is built ahead** (:data:`PLAN_LEAD_S` before its slot begins, on a
  home-page beat, after that beat's push): the clock would otherwise spend 150 to 170 ms on
  the scene's first body frame, more than a beat.
- **Memory is collected on a schedule**: after a beat's push, once :data:`GC_EVERY_BYTES`
  have been allocated since the last collection, so the pause falls in the beat's idle time
  rather than in the middle of a drawing.
- **Pause and resume** (for the update door): paused, it pushes nothing and its source is
  stopped (Whisker allows us one connection); resume starts both again.
- **Its console lines go to the log ring** (:class:`~arthur.onclock.clocklog.ClockLog`),
  which the source also writes the robot's lines to.

Times passed to mapping and render are aware UTC.
"""

import asyncio
import gc
import sys
from datetime import timedelta

from arthur.compat import replace
from arthur.config import load_config_json
from arthur.mapping import to_display_state
from arthur.onclock import VERSION
from arthur.onclock.awtrix import ClockTransport, TransportError
from arthur.onclock.clock import Clock, since_us, stamp_us
from arthur.onclock.clocklog import ClockLog
from arthur.onclock.pusher import Pusher
from arthur.onclock.weather import WeatherService
from arthur.render import idle_show as show
from arthur.render import render
from arthur.schedule import idle_cue
from arthur.timebase import frames_since, until_next_tick
from arthur.wire import util
from arthur.wire.net import Tls

IS_MPY = util.IS_MPY

EXIT_STOP = 0
EXIT_RESTART = 42
#: After a service's task dies of a bug, start it again after this long.
SOURCE_RESTART_S = 30.0
#: How long a service gets to stop before it is cancelled.
SERVICE_STOP_S = 10.0
#: While AWTRIX does not answer at start: wait 1 s, doubling to this, between tries.
AWTRIX_WAIT_MAX_S = 10.0
#: Build a scene's plan on the first beat this long before its slot begins. The home page
#: changes only on whole seconds and slots begin on whole seconds, so the beats a late build
#: covers draw the same frame.
PLAN_LEAD_S = 0.5
#: Collect once this much has been allocated since the last collection (MicroPython only).
GC_EVERY_BYTES = 256 * 1024
#: The config file under ``root``.
CONFIG_NAME = "config.json"

_tls = {}


def tls_for(ca_file):
    """One :class:`arthur.wire.net.Tls` per CA file, shared by every user in the program:
    loading the clock's bundle takes 0.4 s and 435 KB of heap there."""
    t = _tls.get(ca_file)
    if t is None:
        t = Tls(ca_file=ca_file)
        _tls[ca_file] = t
    return t


def trace_text(exc):
    """The traceback of ``exc`` as text, on either Python."""
    if IS_MPY:
        import io

        buf = io.StringIO()
        sys.print_exception(exc, buf)
        return buf.getvalue()
    import traceback

    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _error_text(exc):
    return type(exc).__name__ + ": " + str(exc)


class _Service:
    """A long-running task beside the loop, restarted if it dies."""

    def __init__(self, name, label, start):
        self.name = name
        self.label = label
        self.start = start
        self.task = None
        self.stop = None
        self.done = False
        self.error = None
        self.stopping = False  # stopped on purpose (pause); not a death
        self.restart_at = None


class _Stats:
    """Microseconds of work per beat, by what the beat drew."""

    def __init__(self):
        self.parts = {}

    def add(self, part, us):
        s = self.parts.get(part)
        if s is None:
            self.parts[part] = [1, us, us]
        else:
            s[0] += 1
            s[1] += us
            s[2] = max(s[2], us)

    def summary(self):
        """``{part: {"beats", "mean_ms", "worst_ms"}}``."""
        out = {}
        for part in sorted(self.parts):
            n, total, worst = self.parts[part]
            out[part] = {"beats": n, "mean_ms": round(total / n / 1000, 3),
                         "worst_ms": round(worst / 1000, 3)}
        return out


class ClockApp:
    """The loop. ``source`` has the feeder's ``Source`` protocol: ``view(now) ->
    (RobotSnapshot | None, FeederHealth)``, ``async run(stop)``, ``update_config(cfg)``.

    ``root`` holds ``config.json`` (``python -m arthur.tools.export_config``); ``ca_file`` is
    the CA bundle for TLS (on the clock ``/tmp/awtrix-release/share/ca-certificates.crt``).
    ``wall_fn`` (aware UTC datetimes), ``sleep`` and ``time_fn`` (monotonic seconds; derived
    from ``wall_fn`` when only that is given) are for tests.
    """

    def __init__(self, source, *, root, base_url="http://127.0.0.1", ca_file, log=None,
                 wall_fn=None, sleep=None, time_fn=None):
        self.source = source
        self.root = root.rstrip("/") if len(root) > 1 else root
        self.config_path = self.root + "/" + CONFIG_NAME
        self.log = log if log is not None else ClockLog()
        self.clock = Clock(wall_fn, sleep, time_fn)
        self.echo = True  # console lines also go to stdout
        self.cfg = load_config_json(self.config_path)
        self.tls = tls_for(ca_file)
        self.transport = ClockTransport(base_url, time_fn=self.clock.mono)
        self.pusher = Pusher(self.transport, time_fn=self.clock.mono)
        self.weather = WeatherService(self.cfg, self.tls, self.clock, log=self.console,
                                      quiet_fn=self._quiet)
        #: Called after every successful push as ``trace(now, frame, state, reason)``; tests.
        self.trace = None
        self.paused = False
        self.shown = None  # (kind, detail) last drawn
        self.state = None  # the last mapped state
        self.health = None  # the source's last FeederHealth
        self._exit = asyncio.Event()
        self._exit_code = None
        self._started = self.clock.mono()
        self._whisker_problem = None
        self._whisker_reported = False
        self._draw_error = None
        self._weather_error = None
        self._cue_failed = None
        self._cue_error = None
        self._failed_pushes = 0
        self._prepared = None  # (cue.at, cue.n, cfg) of the scene plan built ahead
        self._prepare_error = None
        self._scene_on = False  # the last beat was in a scene slot
        self._services = None
        self._gc_at = 0
        self.stats = _Stats()
        self.gc_pauses = _Stats()
        self.late_beats = 0

    # -- the console ---------------------------------------------------------------------

    def console(self, level, msg, exc=None):
        """One console line: into the log ring (and stdout while ``echo``)."""
        line = {"kind": "console", "ts": util.utc_iso_ms(self.clock.epoch()), "level": level,
                "msg": msg}
        if exc is not None:
            line["trace"] = trace_text(exc)[-2000:]
        self.log.append(line)
        if self.echo:
            print(line["ts"], level.upper(), msg)
            if exc is not None:
                print(line["trace"])

    # -- control (the update door calls these) -------------------------------------------

    def pause(self):
        """Stop pushing and stop the source; :meth:`resume` starts both again."""
        if self.paused:
            return
        self.paused = True
        svc = self._service("source")
        if svc is not None and svc.task is not None and svc.stop is not None:
            svc.stopping = True
            svc.stop.set()
        self.console("info", "paused: no pushes, the source stopped")

    def resume(self):
        if not self.paused:
            return
        self.paused = False
        self.pusher.forget()
        self.console("info", "resumed")

    def reload_config(self):
        """Read ``<root>/config.json`` again. A file that does not load is logged and the
        last good config kept. True if the new one was taken."""
        try:
            cfg = load_config_json(self.config_path)
        except Exception as exc:  # noqa: BLE001 - keep the last good config
            self.console("error", "config " + self.config_path + " not reloaded, keeping the "
                         "last good one: " + _error_text(exc))
            return False
        self.cfg = cfg
        for target in (self.source, self.weather):
            try:
                target.update_config(cfg)
            except Exception as exc:  # noqa: BLE001
                self.console("error", "could not pass the new config on: " + _error_text(exc))
        self._prepared = None
        self.console("info", "config reloaded from " + self.config_path)
        return True

    def request_restart(self):
        """:meth:`run` returns 42 at the next beat."""
        if self._exit_code is None:
            self._exit_code = EXIT_RESTART
        self._exit.set()

    def request_stop(self):
        """:meth:`run` returns 0 at the next beat."""
        if self._exit_code is None:
            self._exit_code = EXIT_STOP
        self._exit.set()

    def status(self):
        """Small and JSON-able."""
        now = self.clock.wall()
        mono = self.clock.mono()
        st = self.state
        health = self.health
        contact = health.last_contact_at if health is not None else None
        last_push = self.pusher.last_push_t
        out = {
            "version": VERSION,
            "uptime_s": round(mono - self._started, 1),
            "kind": st.kind if st is not None else None,
            "detail": st.detail if st is not None else None,
            "health": None if health is None else {
                "error": health.error, "ws_connected": health.ws_connected},
            "since_contact_s": None if contact is None else round(
                (now - contact).total_seconds(), 1),
            "since_push_s": None if last_push is None else round(mono - last_push, 1),
            "pushes": self.pusher.pushes,
            "push_failures": self.pusher.failures,
            "reachable": self.pusher.reachable,
            "weather_error": self.weather.error,
            "heap_free": gc.mem_free() if IS_MPY else None,
            "heap_alloc": gc.mem_alloc() if IS_MPY else None,
            "paused": self.paused,
            "log_first_seq": self.log.first_seq,
            "log_last_seq": self.log.last_seq,
        }
        return out

    # -- start ---------------------------------------------------------------------------

    def warm(self):
        """Load the fonts, the art and the CA certificates now, while the heap is in one
        piece, and draw once. Anything that fails here is logged and left to fail later in
        the open."""
        import os

        from arthur.render import font, icons

        t0 = stamp_us()
        try:
            font.small_font()
            font.large_font()
            font.condensed_small_font()
            folder = str(icons.ICON_DIR)
            for f in sorted(os.listdir(folder)):
                parts = f.split(".")
                if len(parts) == 3 and parts[2] == "txt" and parts[1].isdigit():
                    icons._load(parts[0], int(parts[1]))
            show._dissolve_order(show.W * show.H)
        except Exception as exc:  # noqa: BLE001
            self.console("error", "could not load the fonts and art: " + _error_text(exc), exc)
        try:
            self.tls.context()
        except Exception as exc:  # noqa: BLE001 - the weather will say so too
            self.console("warning", "could not load the CA certificates: " + _error_text(exc))
        gc.collect()
        if IS_MPY:
            self._gc_at = gc.mem_alloc() + GC_EVERY_BYTES
        self.console("info", "loaded in " + str(since_us(t0) // 1000) + " ms"
                     + ("" if not IS_MPY else ", heap in use " + str(gc.mem_alloc())))

    async def wait_for_awtrix(self):
        """Until AWTRIX's API answers (also turning ``enlargeApps`` off). False if asked to
        stop meanwhile."""
        delay = 1.0
        waited = False
        t0 = self.clock.mono()
        while not self._exit.is_set():
            try:
                await self.transport.check()
            except TransportError as exc:
                if not waited:
                    self.console("warning", "waiting for AWTRIX at " + self.transport.conn.base
                                 + ": " + str(exc))
                    waited = True
                if await self.clock.wait(self._exit, delay, step=0.5):
                    return False
                delay = min(delay * 2, AWTRIX_WAIT_MAX_S)
                continue
            if waited:
                self.console("info", "AWTRIX answered after "
                             + str(round(self.clock.mono() - t0, 1)) + " s")
            return True
        return False

    # -- services ------------------------------------------------------------------------

    def _service(self, name):
        for svc in self._services or ():
            if svc.name == name:
                return svc
        return None

    async def _guard(self, svc):
        try:
            await svc.start(svc.stop)
        except Exception as exc:  # noqa: BLE001 - logged by _supervise
            svc.error = exc
        finally:
            svc.done = True

    def _start(self, svc):
        svc.stop = asyncio.Event()
        svc.done = False
        svc.error = None
        svc.stopping = False
        svc.task = asyncio.create_task(self._guard(svc))

    def _supervise(self):
        """Start any service not running (the source only while not paused, and never while
        its last task is still stopping); restart one that died after
        :data:`SOURCE_RESTART_S`."""
        for svc in self._services:
            if svc.task is None:
                if svc.name == "source" and self.paused:
                    continue
                if svc.restart_at is not None:
                    if self.clock.mono() < svc.restart_at:
                        continue
                    svc.restart_at = None
                    self.console("info", "restarting the " + svc.label)
                self._start(svc)
            elif svc.done:
                if svc.stopping:  # stopped for a pause: start again on resume
                    svc.task = None
                    continue
                exc = svc.error
                self.console("error", "the " + svc.label + " stopped unexpectedly ("
                             + (_error_text(exc) if exc is not None else "it returned")
                             + "); restarting in " + str(SOURCE_RESTART_S) + " s",
                             exc)
                svc.task = None
                svc.restart_at = self.clock.mono() + SOURCE_RESTART_S

    async def _stop_services(self):
        for svc in self._services or ():
            if svc.stop is not None:
                svc.stop.set()
        for svc in self._services or ():
            if svc.task is None or svc.done:
                continue
            try:
                await asyncio.wait_for(svc.task, SERVICE_STOP_S)
            except Exception as exc:  # noqa: BLE001
                self.console("error", "the " + svc.label + " did not stop cleanly: "
                             + _error_text(exc))

    # -- one beat ------------------------------------------------------------------------

    def _note_state(self, kind, detail):
        shown = (kind, detail if kind == "no_data" else None)
        if shown != self.shown:
            new = kind + (" (" + detail + ")" if kind == "no_data" and detail else "")
            if self.shown is None:
                self.console("info", "display: " + new)
            else:
                old = self.shown[0] + (" (" + self.shown[1] + ")" if self.shown[1] else "")
                self.console("info", "display: " + old + " -> " + new)
            self.shown = shown
        # Whisker contact, from the mapping's own verdict. Waiting for the first data at
        # start is not a loss.
        problem = detail if kind == "no_data" else None
        if problem == "no_snapshot" and not self._whisker_reported:
            problem = None
        if problem != self._whisker_problem:
            if problem is not None:
                self.console("warning", "Whisker contact lost (" + problem + ")")
            elif self._whisker_reported:
                self.console("info", "Whisker contact regained")
            self._whisker_reported = True
            self._whisker_problem = problem

    def _weather_view(self, now):
        try:
            reading = self.weather.view(now)
        except Exception as exc:  # noqa: BLE001 - a decoration must not stop the display
            msg = _error_text(exc)
            if msg != self._weather_error:
                self.console("error", "could not read the weather; showing none until it can",
                             exc)
                self._weather_error = msg
            return None
        if self._weather_error is not None:
            self.console("info", "weather readable again")
            self._weather_error = None
        return reading

    def _draw(self, state, now, cfg):
        """Render ``state``; if that fails while a cue is set, render it without the cue (the
        home page). Logged once per cue: at ERROR the first time an error is seen, at DEBUG
        for later cues failing the same way."""
        try:
            return render(state, now, cfg)
        except Exception as exc:
            cue = state.cue
            if cue is None:
                raise
            if cue != self._cue_failed:
                self._cue_failed = cue
                msg = _error_text(exc)
                what = (cue.kind + " " + (cue.name or "")).strip()
                if msg != self._cue_error:
                    self._cue_error = msg
                    self.console("error", "could not draw the idle show's " + what
                                 + "; showing the home page instead", exc)
                else:
                    self.console("debug", "could not draw the idle show's " + what + " ("
                                 + msg + "); home page instead")
            return render(replace(state, cue=None), now, cfg)

    def _part(self, state, now):
        """What a beat drew, for the timing figures."""
        if state.kind != "ready":
            return state.kind
        cue = state.cue
        if cue is None:
            return "home"
        if cue.kind == "page":
            return "page"
        part = show.part_of(cue, frames_since(cue.at, now))
        if part == "body":
            return "scene " + str(show.scene_name(cue))
        if part == "home":
            return "home"
        return "scene wipe"

    async def tick(self):
        """One beat."""
        if self.paused:
            return
        t0 = stamp_us()
        cfg = self.cfg
        now = self.clock.wall()
        weather = self._weather_view(now)
        try:
            snapshot, health = self.source.view(now)
            self.health = health
            state = to_display_state(snapshot, health, now, cfg, weather=weather)
            frame = self._draw(state, now, cfg)
        except Exception as exc:  # noqa: BLE001 - nothing is pushed; the clock goes stale
            msg = _error_text(exc)
            if msg != self._draw_error:
                self.console("error", "could not map or draw the screen; nothing pushed until "
                             "it can", exc)
                self._draw_error = msg
            return
        if self._draw_error is not None:
            self.console("info", "drawing again")
            self._draw_error = None
        self.state = state
        self._note_state(state.kind, state.detail)
        drawn_us = since_us(t0)

        was = self.pusher.reachable
        p0 = stamp_us()
        result = await self.pusher.offer(frame, cfg.timings.ttl_s, cfg.timings.heartbeat_s)
        if result is not None:
            self.stats.add("push" if result.error is None else "push failed", since_us(p0))
            if result.error is not None:
                self._failed_pushes += 1
                if was is not False:
                    self.console("warning", "clock unreachable: " + str(result.error)
                                 + " (retrying every " + str(self.pusher.retry_after_s) + " s)")
            else:
                if was is not True:
                    if was is None:
                        self.console("info", "first frame pushed to AWTRIX")
                    else:
                        self.console("info", "clock reachable again (after "
                                     + str(self._failed_pushes) + " failed pushes)")
                    self._failed_pushes = 0
                if self.trace is not None:
                    self.trace(now, frame, state, result.reason)
        self.stats.add(self._part(state, now), drawn_us)
        p0 = stamp_us()
        if self._plan_ahead(state, now, cfg):
            self.stats.add("plan built ahead", since_us(p0))

    def _quiet(self):
        """For the weather: False while a scene slot is on or about to begin."""
        st = self.state
        if st is None or st.kind != "ready":
            return True
        if st.cue is not None and st.cue.kind == "scene":
            return False
        now = self.clock.wall()
        nxt = idle_cue(st.since, now + _seconds(5), self.cfg)
        return nxt is None or nxt.kind != "scene"

    def _plan_ahead(self, state, now, cfg):
        """Build the coming scene's plan if its slot begins within :data:`PLAN_LEAD_S`, and
        let the last scene's plan go once its slot is over. True if a plan was built."""
        in_scene = (state.kind == "ready" and state.cue is not None
                    and state.cue.kind == "scene")
        if self._scene_on and not in_scene:
            show.forget_plan()  # free its memory until the next scene
        self._scene_on = in_scene
        if state.kind != "ready" or in_scene:
            return False
        nxt = idle_cue(state.since, now + _seconds(PLAN_LEAD_S), cfg)
        if nxt is None or nxt.kind != "scene" or not nxt.at > now:
            return False
        key = (nxt.at, nxt.n, cfg)
        if self._prepared is not None and self._prepared[0] == key[0] \
                and self._prepared[1] == key[1] and self._prepared[2] is cfg:
            return False
        self._prepared = key
        try:
            return show.prepare_scene(nxt, cfg)
        except Exception as exc:  # noqa: BLE001 - the scene's own frame will fall back
            msg = _error_text(exc)
            if msg != self._prepare_error:
                self._prepare_error = msg
                self.console("error", "could not plan the coming scene ahead", exc)
            return False

    def _collect(self):
        """Collect when :data:`GC_EVERY_BYTES` have been allocated since the last time."""
        if not IS_MPY or gc.mem_alloc() < self._gc_at:
            return
        t0 = stamp_us()
        gc.collect()
        self.gc_pauses.add("collect", since_us(t0))
        self._gc_at = gc.mem_alloc() + GC_EVERY_BYTES

    # -- the service ---------------------------------------------------------------------

    async def run(self):
        """Run until :meth:`request_stop` (returns 0) or :meth:`request_restart` (42)."""
        self.console("info", "arthur on the clock " + VERSION + " starting; config "
                     + self.config_path + ", AWTRIX " + self.transport.conn.base)
        self.warm()
        try:
            if not await self.wait_for_awtrix():
                return self._exit_code
            try:
                self.source.update_config(self.cfg)
            except Exception as exc:  # noqa: BLE001
                self.console("error", "could not pass the config to the source: "
                             + _error_text(exc))
            self._services = [_Service("source", "Whisker source", self.source.run),
                              _Service("weather", "weather source", self.weather.run)]
            while not self._exit.is_set():
                self._supervise()
                beat = self.clock.wall()
                await self.tick()
                self._collect()
                if self._exit.is_set():
                    break
                now = self.clock.wall()
                if (now - beat).total_seconds() > 0.125:
                    self.late_beats += 1
                # Wake on the next beat of the wall clock. A beat that ran long lands on a
                # later beat; the ones it covered are dropped.
                await self.clock.sleep(until_next_tick(now))
        finally:
            await self._stop_services()
            try:
                await self.transport.close()
            except Exception:  # noqa: BLE001, S110 - closing must not raise
                pass
            self.console("info", "stopped (exit " + str(self._exit_code) + "); the last "
                         "frame goes stale on the clock by itself")
        return self._exit_code


def _seconds(s):
    return timedelta(microseconds=round(s * 1_000_000))
