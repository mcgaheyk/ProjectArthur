"""The studio's in-process feeder: source → mapping → render → AwtrixNgTransport → fake clock.

It runs the real pipeline end to end. The source is one of:

- ``scenario``: a timed scenario from :mod:`arthur.fixtures`, played on its own simulated
  clock (``start + t``) with play/pause, seek and speed;
- ``fixture``: one named fixture, played on a loop: the time is the fixture's ``now`` plus
  the whole seconds since it was selected, modulo :data:`~arthur.sim.pipeline.LOOP_S`, so
  blinks, glances and timers play as in the gallery. A fixture with its own ``fps`` (the idle
  show's) steps at that rate over its own ``loop_s`` (``Fixture.time_at``). Selecting a
  fixture restarts its loop. A fixture plays with its own weather and cue;
- ``manual``: a snapshot built from hand-set fields at the real current time;
- ``log``: the spike's logs of the real robot (:mod:`arthur.sources.logfile`), either
  *followed* live at the real current time, or one recorded visit *replayed* on its own
  simulated clock with play/pause, seek and speed. The studio never talks to Whisker itself.

It renders a 52x16 frame and pushes it over HTTP through the real
:class:`~arthur.transports.awtrix_ng.AwtrixNgTransport` when the frame changes or when the
heartbeat is due, with ``lifetimeMs`` = ttl. Switched "dead", it stops pushing, so the clock's
own stale mark appears once the lifetime runs out. The fake clock's lifetime runs on real time.

In every mode but ``fixture``, mapping gets a sample :class:`~arthur.model.Weather` set in the
studio (condition, day or night, temperature, or no reading at all), dated ten minutes before
the source's time so it is always fresh. The **idle show** switch (off by default) maps and
renders with the intended shares (:data:`SHOW_SHARES`) in memory instead of the config's, so
scenario, manual and log modes play the real rotation; ``config/display.toml`` is never
written.

Variants, push bookkeeping and frames are keyed by the size string ("52x16"), which has one
entry; the control API takes variants as ``{"52x16": {kind: variant}}``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from arthur.sim.pipeline import LOOP_S, ReloadingPipeline, size_key
from arthur.sources.logfile import (
    FRESH_FOR_S,
    SPIKE_COMMAND,
    LogView,
    RobotLog,
    Visit,
    follow_view,
    is_fresh,
    replay_view,
)
from arthur.timebase import until_next_tick
from arthur.transports.awtrix_ng import AwtrixNgTransport
from arthur.transports.base import TransportError

MODES = ("scenario", "fixture", "manual", "log")
SPEEDS = (1, 10, 60)
#: Sub-modes of the "log" source.
LOG_SUB_MODES = ("follow", "replay")
LOG_CONTROL_KEYS = ("sub_mode", "visit", "position", "speed", "playing")
#: Look for new log lines at most this often (seconds of time_fn).
LOG_REFRESH_S = 1.0
DEFAULT_LOG_DIR = Path("logs")
#: Heartbeat and ttl with "fast expiry" on: the stale mark shows 6 s after the feeder dies.
FAST_HEARTBEAT_S = 2.0
FAST_TTL_S = 6.0
#: After a failed push, wait this long before trying the clock again.
RETRY_AFTER_S = 1.0

MANUAL_DEFAULTS: dict[str, Any] = {
    "status": "READY",
    "waste_drawer_level": 42.0,
    "litter_level": 78.0,
    "litter_level_state": "OPTIMAL",
    "is_sleeping": False,
    "is_laser_dirty": False,
    "power_type": "AC",
    "is_online": True,
    "is_on": True,
    "ws_connected": True,
    "health_error": None,
    "wait_time_minutes": 8,
    # Feeder-observed: the pet weight has arrived, so the cat has left (dated when switched).
    "cat_left": False,
}
LITTER_STATES = (None, "OVERFILL", "OPTIMAL", "REFILL", "LOW", "EMPTY")
POWER_TYPES = ("AC", "DC", "NC")
#: Manual mode accepts any whole-minute wait in this range: the robot reports values outside
#: the documented 3, 7, 15, 25 and 30 (a real one reports 8).
WAIT_MINUTES_RANGE = (1, 60)
#: Statuses during which a visit is under way (see arthur/sources/tracker.py).
_VISIT_STATUSES = frozenset({"CAT_DETECTED", "CAT_SENSOR_TIMING"})
_FAULT_CODES = {"PINCH_DETECT": "PD", "OVER_TORQUE_FAULT": "OTF", "CAT_SENSOR_FAULT": "CSF",
                "UNKNOWN": "UNK"}

#: The idle show's intended shares of a round (D-0019), which the studio's switch applies in
#: memory while the repository config keeps them at 0.
SHOW_SHARES: dict[str, float] = {"page_pct": 30, "scene_pct": 20}
#: The sample weather's conditions: WMO weather codes, as Open-Meteo reports them.
WEATHER_CODES: tuple[tuple[int, str], ...] = (
    (0, "clear"),
    (2, "partly cloudy"),
    (3, "overcast"),
    (45, "fog"),
    (63, "rain"),
    (73, "snow"),
    (95, "thunder"),
)
#: The sample weather's temperature range, Fahrenheit.
WEATHER_TEMP_RANGE = (-40, 120)
#: How old the sample reading is at the source's time: well inside ``max_age_s``.
WEATHER_AGE_S = 600.0
#: The sample weather when the fixtures carry none; else ``fixtures.SAMPLE_WEATHER`` wins.
WEATHER_DEFAULTS: dict[str, Any] = {"reading": True, "code": 3, "is_day": True, "temp_f": 41.0}
WEATHER_KEYS = tuple(WEATHER_DEFAULTS)
#: The day's high and low are the temperature plus and minus this (the sample's 47 and 35
#: around 41).
WEATHER_SPREAD_F = 6.0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ControlError(ValueError):
    """A bad value sent to the control endpoint."""


class _Push:
    """Push bookkeeping for the clock."""

    def __init__(self) -> None:
        self.last_bytes: bytes | None = None
        self.last_push_t: float | None = None
        self.last_attempt_t: float | None = None
        self.last_error: str | None = None
        self.push_count = 0
        self.kind: str | None = None
        self.variant: str | None = None


class StudioFeeder:
    def __init__(
        self,
        pipe: ReloadingPipeline,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        wall_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        base_url: str | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        self.pipe = pipe
        self.time_fn = time_fn
        self.wall_fn = wall_fn
        self.base_url = base_url
        self.lock = asyncio.Lock()

        p = pipe.current
        self.mode = "scenario"
        self.scenario = next(iter(p.fixtures.SCENARIOS))
        self.fixture = "ready" if "ready" in p.fixtures.FIXTURES else next(iter(p.fixtures.FIXTURES))
        self.speed = 10
        self.playing = True
        self._pos = 0.0
        self._anchor = time_fn()
        self._fixture_t0 = time_fn()
        self._fixture_phase = 0.0  # seconds from selection to the feeder's next beat

        self.idle_show = False
        self.weather: dict[str, Any] = _sample_weather(p.fixtures)
        self.last_state: Any = None  # the DisplayState of the last compute()

        self.manual: dict[str, Any] = dict(MANUAL_DEFAULTS)
        now = wall_fn()
        self._manual_since = now - timedelta(hours=2)
        self._manual_prev: str | None = "CLEAN_CYCLE"
        self._offline_since: datetime | None = None
        self._visit_start: datetime | None = None
        self._cat_left_at: datetime | None = None

        # "log" source. The log is read only once the mode is first used.
        self.log_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
        self.robot_log: RobotLog | None = None
        self._log_refresh_t: float | None = None
        self.log_sub = "follow"
        self.log_visit: str | None = None
        self.log_speed = 10
        self.log_playing = False
        self._log_pos = 0.0
        self._log_anchor = time_fn()
        self._log_view: LogView | None = None

        self.variants: dict[str, dict[str, str]] = {size_key(s): {} for s in p.sizes()}
        self.alive = True
        self.fast_expiry = False

        self._push: dict[str, _Push] = {size_key(s): _Push() for s in p.sizes()}
        self.errors: deque[dict[str, Any]] = deque(maxlen=20)
        self._session: aiohttp.ClientSession | None = None
        self._transports: dict[str, AwtrixNgTransport] = {}
        self.showing: dict[str, Any] = {}

    # --- timing ----------------------------------------------------------------------

    @property
    def heartbeat_s(self) -> float:
        return FAST_HEARTBEAT_S if self.fast_expiry else float(self.pipe.current.cfg.timings.heartbeat_s)

    @property
    def ttl_s(self) -> float:
        return FAST_TTL_S if self.fast_expiry else float(self.pipe.current.cfg.timings.ttl_s)

    def _scenario_obj(self) -> Any:
        scs = self.pipe.current.fixtures.SCENARIOS
        if self.scenario not in scs:
            self.scenario = next(iter(scs))
        return scs[self.scenario]

    def position(self) -> float:
        """Seconds into the scenario. Stops (and pauses) at the end."""
        dur = float(self._scenario_obj().duration_s)
        pos = self._pos
        if self.playing:
            pos += (self.time_fn() - self._anchor) * self.speed
        if pos >= dur:
            pos = dur
            if self.playing:
                self.playing = False
                self._pos = dur
        return max(0.0, pos)

    def _fixture_obj(self) -> Any:
        fxs = self.pipe.current.fixtures.FIXTURES
        if self.fixture not in fxs:
            self.fixture = next(iter(fxs))
        return fxs[self.fixture]

    def fixture_position(self) -> float:
        """Seconds into the fixture's loop.

        An ordinary fixture: whole seconds, 0 .. LOOP_S - 1. A fixture with its own ``fps``:
        whole frames of ``1 / fps`` over its own loop. Frame ``k`` starts half a frame before
        the feeder's ``k``-th beat after the selection, so a tick on the beat (give or take a
        few milliseconds) gets each frame exactly once.
        """
        p = self.pipe.current
        name = self._fixture_obj().name
        elapsed = max(0.0, self.time_fn() - self._fixture_t0)
        fps = p.fixture_fps(name)
        if fps == 1 and not p.fixture(name).fps:
            return int(elapsed) % LOOP_S
        n = max(1, round(p.fixture_loop_s(name) * fps))
        k = max(0, math.floor((elapsed - self._fixture_phase) * fps + 0.5))
        return (k % n) / fps

    def _select_fixture(self) -> None:
        """Start the fixture's loop over, lined up with the feeder's beat."""
        self._fixture_t0 = self.time_fn()
        fps = self.pipe.current.fixture_fps(self._fixture_obj().name)
        self._fixture_phase = until_next_tick(self.wall_fn(), fps)

    def _freeze(self) -> None:
        self._pos = self.position()
        self._anchor = self.time_fn()

    # --- control ---------------------------------------------------------------------

    def apply(self, ctl: dict[str, Any]) -> None:
        """Apply a partial control update. Validates everything before changing anything."""
        if not isinstance(ctl, dict):
            raise ControlError("control body must be a JSON object")
        p = self.pipe.current
        known = {"mode", "fixture", "scenario", "speed", "position", "playing", "manual",
                 "variants", "feeder_alive", "fast_expiry", "log", "idle_show", "weather"}
        unknown = set(ctl) - known
        if unknown:
            raise ControlError(f"unknown control field(s): {', '.join(sorted(unknown))}")
        if "mode" in ctl and ctl["mode"] not in MODES:
            raise ControlError(f"mode must be one of {', '.join(MODES)}")
        if "fixture" in ctl and ctl["fixture"] not in p.fixtures.FIXTURES:
            raise ControlError(f"unknown fixture {ctl['fixture']!r}")
        if "scenario" in ctl and ctl["scenario"] not in p.fixtures.SCENARIOS:
            raise ControlError(f"unknown scenario {ctl['scenario']!r}")
        if "speed" in ctl and ctl["speed"] not in SPEEDS:
            raise ControlError(f"speed must be one of {SPEEDS}")
        if "position" in ctl and not (
            isinstance(ctl["position"], (int, float)) and not isinstance(ctl["position"], bool)
        ):
            raise ControlError("position must be a number of seconds")
        for k in ("playing", "feeder_alive", "fast_expiry", "idle_show"):
            if k in ctl and not isinstance(ctl[k], bool):
                raise ControlError(f"{k} must be true or false")
        manual = self._check_manual(ctl.get("manual"))
        variants = self._check_variants(ctl.get("variants"))
        log_ctl = self._check_log(ctl.get("log"))
        weather = _check_weather(ctl.get("weather"))

        # Apply.
        if "fixture" in ctl:
            self.fixture = ctl["fixture"]
        if "mode" in ctl and ctl["mode"] != self.mode:
            self.mode = ctl["mode"]
            self._select_fixture()
        if "scenario" in ctl and ctl["scenario"] != self.scenario:
            self.scenario = ctl["scenario"]
            self._pos, self._anchor = 0.0, self.time_fn()
        if "fixture" in ctl:
            self._select_fixture()
        if "idle_show" in ctl:
            self.idle_show = ctl["idle_show"]
        if weather:
            self.weather.update(weather)
        if "speed" in ctl:
            self._freeze()
            self.speed = ctl["speed"]
        if "position" in ctl:
            dur = float(self._scenario_obj().duration_s)
            self._pos = min(max(0.0, float(ctl["position"])), dur)
            self._anchor = self.time_fn()
        if "playing" in ctl:
            self._freeze()
            if ctl["playing"] and self._pos >= float(self._scenario_obj().duration_s):
                self._pos = 0.0  # play again from the start
            self.playing = ctl["playing"]
            self._anchor = self.time_fn()
        if manual:
            self._apply_manual(manual)
        if log_ctl or ctl.get("mode") == "log":
            self._apply_log(log_ctl)
        for sk, kinds in variants.items():
            self.variants.setdefault(sk, {}).update(kinds)
        if "feeder_alive" in ctl and ctl["feeder_alive"] != self.alive:
            self.alive = ctl["feeder_alive"]
            self.force_push()
        if "fast_expiry" in ctl and ctl["fast_expiry"] != self.fast_expiry:
            self.fast_expiry = ctl["fast_expiry"]
            self.force_push()  # so the new lifetime applies now

    def _check_manual(self, m: Any) -> dict[str, Any]:
        if m is None:
            return {}
        if not isinstance(m, dict):
            raise ControlError("manual must be an object")
        p = self.pipe.current
        out: dict[str, Any] = {}
        for k, v in m.items():
            if k not in MANUAL_DEFAULTS:
                raise ControlError(f"unknown manual field {k!r}")
            if k == "status":
                if v not in p.mapping.KNOWN_STATUSES:
                    raise ControlError(f"unknown status {v!r}")
            elif k in ("waste_drawer_level", "litter_level"):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                    raise ControlError(f"{k} must be a number from 0 to 100")
                v = float(v)
            elif k == "litter_level_state":
                if v not in LITTER_STATES:
                    raise ControlError(f"litter_level_state must be one of {LITTER_STATES}")
            elif k == "power_type":
                if v not in POWER_TYPES:
                    raise ControlError(f"power_type must be one of {POWER_TYPES}")
            elif k == "health_error":
                if v is not None and v not in p.model.HEALTH_ERRORS:
                    raise ControlError(f"health_error must be null or one of "
                                       f"{tuple(p.model.HEALTH_ERRORS)}")
            elif k == "wait_time_minutes":
                lo, hi = WAIT_MINUTES_RANGE
                if (isinstance(v, bool) or not isinstance(v, (int, float))
                        or v != int(v) or not lo <= v <= hi):
                    raise ControlError(
                        f"wait_time_minutes must be a whole number from {lo} to {hi}")
                v = int(v)
            elif not isinstance(v, bool):
                raise ControlError(f"{k} must be true or false")
            out[k] = v
        return out

    def _apply_manual(self, m: dict[str, Any]) -> None:
        now = self.wall_fn()
        m = dict(m)
        if "status" in m and m["status"] != self.manual["status"]:
            was_visit = self.manual["status"] in _VISIT_STATUSES
            now_visit = m["status"] in _VISIT_STATUSES
            if now_visit != was_visit:
                # A visit starts or ends: either way the cat-left switch starts off.
                self._visit_start = now if now_visit else None
                self._cat_left_at = None
                m.setdefault("cat_left", False)
                if m["cat_left"] and now_visit:
                    self._cat_left_at = now
            self._manual_prev = self.manual["status"]
            self._manual_since = now
        if "cat_left" in m and m["cat_left"] != self.manual["cat_left"]:
            self._cat_left_at = now if m["cat_left"] else None
        if "is_online" in m and m["is_online"] != self.manual["is_online"]:
            self._offline_since = None if m["is_online"] else now
        self.manual.update(m)

    def _check_log(self, m: Any) -> dict[str, Any]:
        if m is None:
            return {}
        if not isinstance(m, dict):
            raise ControlError("log must be an object")
        unknown = set(m) - set(LOG_CONTROL_KEYS)
        if unknown:
            raise ControlError(f"unknown log field(s): {', '.join(sorted(unknown))}")
        if "sub_mode" in m and m["sub_mode"] not in LOG_SUB_MODES:
            raise ControlError(f"log sub_mode must be one of {', '.join(LOG_SUB_MODES)}")
        if "speed" in m and m["speed"] not in SPEEDS:
            raise ControlError(f"log speed must be one of {SPEEDS}")
        if "position" in m and not (
            isinstance(m["position"], (int, float)) and not isinstance(m["position"], bool)
        ):
            raise ControlError("log position must be a number of seconds")
        if "playing" in m and not isinstance(m["playing"], bool):
            raise ControlError("log playing must be true or false")
        if "visit" in m:
            if not isinstance(m["visit"], str):
                raise ControlError("log visit must be a visit id")
            if self._robot_log(force=True).timeline.visit(m["visit"]) is None:
                raise ControlError(f"unknown visit {m['visit']!r}")
        return dict(m)

    def _apply_log(self, m: dict[str, Any]) -> None:
        self._robot_log(force=True)
        if "sub_mode" in m:
            self.log_sub = m["sub_mode"]
        if "visit" in m and m["visit"] != self.log_visit:
            self.log_visit = m["visit"]
            self._log_pos, self._log_anchor = 0.0, self.time_fn()
        if "speed" in m:
            self._log_freeze()
            self.log_speed = m["speed"]
        if "position" in m:
            v = self._log_visit_obj()
            dur = v.window_s if v else 0.0
            self._log_pos = min(max(0.0, float(m["position"])), dur)
            self._log_anchor = self.time_fn()
        if "playing" in m:
            self._log_freeze()
            v = self._log_visit_obj()
            if m["playing"] and v is not None and self._log_pos >= v.window_s:
                self._log_pos = 0.0  # play again from the start
            self.log_playing = m["playing"]
            self._log_anchor = self.time_fn()

    # --- the robot log ----------------------------------------------------------------

    def _robot_log(self, force: bool = False) -> RobotLog:
        """The log, refreshed if it is time (or ``force``). Created on first use."""
        jc = float(self.pipe.current.cfg.timings.just_cleaned_s)
        vm = float(self.pipe.current.cfg.timings.visit_min_s)
        if self.robot_log is None:
            self.robot_log = RobotLog(self.log_dir, just_cleaned_s=jc, visit_min_s=vm)
        self.robot_log.timeline.set_just_cleaned_s(jc)
        self.robot_log.timeline.set_visit_min_s(vm)
        now_t = self.time_fn()
        if force or self._log_refresh_t is None or now_t - self._log_refresh_t >= LOG_REFRESH_S:
            self.robot_log.refresh()
            # A reset makes a new timeline.
            self.robot_log.timeline.set_just_cleaned_s(jc)
            self.robot_log.timeline.set_visit_min_s(vm)
            self._log_refresh_t = now_t
        return self.robot_log

    def _log_visit_obj(self) -> Visit | None:
        """The chosen visit; the newest one when none is chosen or it is gone."""
        if self.robot_log is None:
            return None
        tl = self.robot_log.timeline
        v = tl.visit(self.log_visit) if self.log_visit else None
        if v is None:
            visits = tl.visits()
            v = visits[-1] if visits else None
            self.log_visit = v.id if v else None
        return v

    def log_position(self) -> float:
        """Seconds into the chosen visit's replay window. Stops (and pauses) at the end."""
        v = self._log_visit_obj()
        dur = v.window_s if v else 0.0
        pos = self._log_pos
        if self.log_playing:
            pos += (self.time_fn() - self._log_anchor) * self.log_speed
        if pos >= dur:
            pos = dur
            if self.log_playing:
                self.log_playing = False
                self._log_pos = dur
        return max(0.0, pos)

    def _log_freeze(self) -> None:
        self._log_pos = self.log_position()
        self._log_anchor = self.time_fn()

    def _log_source(self) -> tuple[Any, Any, datetime, str]:
        p = self.pipe.current
        tl = self._robot_log().timeline
        if self.log_sub == "follow":
            view = follow_view(tl, self.wall_fn())
            label = "follow" if view.fresh else "follow (log not fresh)"
        else:
            v = self._log_visit_obj()
            if v is None:
                now = self.wall_fn()
                view = LogView(None, p.model.FeederHealth(last_contact_at=now), now)
                label = "replay (no visits)"
            else:
                view = replay_view(tl, v, self.log_position())
                label = f"replay {v.id}"
        self._log_view = view
        snap = _rebuild(view.snapshot, p.model.RobotSnapshot)
        health = _rebuild(view.health, p.model.FeederHealth)
        return snap, health, view.now, label

    def _log_block(self) -> dict[str, Any]:
        rl = self.robot_log
        block: dict[str, Any] = {
            "dir": str(self.log_dir),
            "loaded": rl is not None,
            "sub_mode": self.log_sub,
            "spike_command": SPIKE_COMMAND,
            "fresh_for_s": FRESH_FOR_S,
        }
        if rl is None:
            return block
        tl = rl.timeline
        wall = self.wall_fn()
        newest = tl.newest
        visit = self._log_visit_obj()
        view = self._log_view
        cur = rl.current_file
        block.update(
            has_files=rl.has_files,
            file=cur.name if cur else None,
            files=[f.name for f in rl.state_files],
            lines=rl.line_count,
            skipped=rl.skipped,
            newest_ts=newest.ts.isoformat() if newest else None,
            newest_kind=newest.kind if newest else None,
            newest_age_s=(wall - newest.ts).total_seconds() if newest else None,
            fresh=is_fresh(tl, wall),
            contact_error=tl.contact_error(),
            visits=[_visit_json(tl, v) for v in reversed(tl.visits())],
            visit=visit.id if visit else None,
            position=self.log_position(),
            duration=visit.window_s if visit else 0.0,
            playing=self.log_playing,
            speed=self.log_speed,
            log_time=view.now.isoformat() if view else None,
            line_ts=view.line.ts.isoformat() if (view and view.line) else None,
            raw=view.raw if view else None,
            observed=_observed_json(view.snapshot if view else None),
        )
        return block

    def _check_variants(self, v: Any) -> dict[str, dict[str, str]]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ControlError("variants must be an object {size: {kind: variant}}")
        p = self.pipe.current
        out: dict[str, dict[str, str]] = {}
        sizes = {size_key(s): s for s in p.sizes()}
        for sk, kinds in v.items():
            if sk not in sizes:
                raise ControlError(f"unknown size {sk!r}")
            if not isinstance(kinds, dict):
                raise ControlError("variants must be an object {size: {kind: variant}}")
            for kind, name in kinds.items():
                names = p.variants(kind, sizes[sk])
                if not names:
                    raise ControlError(f"unknown kind {kind!r}")
                if name not in names:
                    raise ControlError(f"{kind} at {sk} has no variant {name!r}")
                out.setdefault(sk, {})[kind] = name
        return out

    def force_push(self) -> None:
        for p in self._push.values():
            p.last_push_t = None
            p.last_attempt_t = None

    # --- source and frames ----------------------------------------------------------

    def source(self) -> tuple[Any, Any, datetime, str]:
        """``(snapshot, health, now, label)`` for the current mode."""
        if self.mode == "scenario":
            sc = self._scenario_obj()
            t = self.position()
            snap, health, now = sc.at(t)
            return snap, health, now, sc.step_at(t).label
        if self.mode == "log":
            return self._log_source()
        if self.mode == "fixture":
            fx = self._fixture_obj()
            return fx.snapshot, fx.health, fx.time_at(self.fixture_position()), fx.name
        return (*self._manual_source(), "manual")

    def show_cfg(self) -> Any:
        """The config to map and render with: the config file's, or with the switch on, the
        same with the idle show's intended shares (in memory only)."""
        cfg = self.pipe.current.cfg
        if not self.idle_show:
            return cfg
        return dataclasses.replace(cfg, idle=dataclasses.replace(cfg.idle, **SHOW_SHARES))

    def sample_weather(self, now: datetime) -> Any:
        """The studio's sample reading, fetched :data:`WEATHER_AGE_S` before ``now``; None for
        "no reading"."""
        w = self.weather
        if not w["reading"]:
            return None
        t = float(w["temp_f"])
        return self.pipe.current.model.Weather(
            temp_f=t, code=int(w["code"]), is_day=bool(w["is_day"]),
            high_f=t + WEATHER_SPREAD_F, low_f=t - WEATHER_SPREAD_F,
            fetched_at=now - timedelta(seconds=WEATHER_AGE_S))

    def show_part(self, state: Any, now: datetime, cfg: Any) -> dict[str, Any] | None:
        """Where idle's show is at ``now``: ``home``, ``page`` or ``scene``, with the page or
        scene's name, the part of its slot (``glance``, ``open``, ``body`` ...) and the
        seconds left in it. On home, the seconds to the next page or scene slot of the round
        (which may not play: too soon after idle began, or a scene at night). None when the
        state is not ready."""
        if state.kind != "ready":
            return None
        p = self.pipe.current
        cue = getattr(state, "cue", None)
        if cue is None:
            out: dict[str, Any] = {"part": "home", "name": None, "phase": None, "left_s": None}
            if p.schedule is None:
                return out
            r, slots = p.schedule.slots(cfg)
            if r <= 0 or not slots:
                return out
            pos = (now - _EPOCH).total_seconds() % r
            starts = sorted(start for _, start, _ in slots)
            nxt = next((s for s in starts if s > pos), starts[0] + r)
            out.update(left_s=nxt - pos, next=next(k for k, s, _ in slots if s == nxt % r))
            return out
        show = p.render_mod.idle_show
        if cue.kind == "scene":
            name = show.scene_name(cue)
        else:
            name = cue.name or show.DEFAULT_PAGE
        k = p.timebase.frames_since(cue.at, now) if p.timebase else 0
        left = (cue.at - now).total_seconds() + float(cue.length_s)
        return {"part": cue.kind, "name": name, "phase": show.part_of(cue, k),
                "left_s": max(0.0, left)}

    def _manual_source(self) -> tuple[Any, Any, datetime]:
        p = self.pipe.current
        m = self.manual
        now = self.wall_fn()
        status = m["status"]
        since, prev = self._manual_since, self._manual_prev
        if not m["is_online"] and self._offline_since is not None:
            since, prev = self._offline_since, status  # went offline when switched
        visit = status in _VISIT_STATUSES
        snap = p.model.RobotSnapshot(
            status=status,
            status_code=_FAULT_CODES.get(status, status),
            is_online=m["is_online"],
            is_on=m["is_on"],
            waste_drawer_level=m["waste_drawer_level"],
            litter_level=m["litter_level"],
            litter_level_state=m["litter_level_state"],
            is_sleeping=m["is_sleeping"],
            is_laser_dirty=m["is_laser_dirty"],
            power_type=m["power_type"],
            wait_time_minutes=m["wait_time_minutes"],
            last_seen=now,
            pet_weight_lbs=10.4,
            status_since=since,
            previous_status=prev,
            last_visit_at=(self._visit_start if visit and self._visit_start
                           else now - timedelta(hours=2)),
            visits_today=3,
            visit_started_at=self._visit_start if visit else None,
            cat_left_at=self._cat_left_at if visit else None,
        )
        health = p.model.FeederHealth(
            last_contact_at=now, ws_connected=m["ws_connected"], error=m["health_error"]
        )
        return snap, health, now

    def compute(self) -> dict[str, Any]:
        """Map and render the current source."""
        p = self.pipe.current
        snap, health, now, label = self.source()
        if self.mode == "fixture":
            # A fixture is one state: mapped at its own time with its own weather and cue,
            # and only the render clock runs, as in the gallery, so a short-lived state does
            # not turn into the next.
            cfg = p.cfg
            state = p.fixture_state(self.fixture)
        else:
            cfg = self.show_cfg()
            state = p.state(snap, health, now, cfg=cfg, weather=self.sample_weather(now))
        self.last_state = state
        frames = {}
        for size in p.sizes():
            sk = size_key(size)
            variant = p.resolve_variant(state.kind, size, self.variants.get(sk, {}).get(state.kind))
            frames[sk] = {"frame": p.render(state, now, size, variant, cfg), "variant": variant}
        try:
            show = self.show_part(state, now, cfg)
        except Exception as exc:  # noqa: BLE001 - an account of the show never stops a frame
            show = {"part": "?", "name": None, "phase": None, "left_s": None,
                    "error": f"{exc.__class__.__name__}: {exc}"}
        return {
            "kind": state.kind,
            "detail": state.detail,
            "sim_time": now.isoformat(),
            "label": label,
            "show": show,
            "frames": frames,
        }

    # --- pushing ---------------------------------------------------------------------

    def _transport(self, sk: str) -> AwtrixNgTransport:
        assert self.base_url is not None
        base = self.base_url.rstrip("/")
        t = self._transports.get(sk)
        if t is None or t.base_url != base:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            t = AwtrixNgTransport(base, session=self._session, time_fn=self.time_fn)
            self._transports[sk] = t
        return t

    async def tick(self) -> dict[str, Any]:
        """Compute frames and push the ones that are due. Returns what is showing."""
        async with self.lock:
            try:
                shown = self.compute()
            except Exception as exc:  # noqa: BLE001 - a render bug must not stop the loop
                self._record_error("render", f"{exc.__class__.__name__}: {exc}")
                return self.showing
            self.showing = shown
            if not self.alive or not self.base_url:
                return shown
            jobs = []
            now_t = self.time_fn()
            for sk, info in shown["frames"].items():
                frame = info["frame"]
                bk = self._push.setdefault(sk, _Push())
                data = frame.to_rgb888()
                due = bk.last_push_t is None or now_t - bk.last_push_t >= self.heartbeat_s
                if not (due or data != bk.last_bytes):
                    continue
                if bk.last_error and bk.last_attempt_t is not None and (
                    now_t - bk.last_attempt_t < RETRY_AFTER_S
                ):
                    continue
                jobs.append(self._push_one(sk, frame, data, shown["kind"], info["variant"]))
            if jobs:
                await asyncio.gather(*jobs)
            return shown

    async def _push_one(self, sk: str, frame: Any, data: bytes, kind: str, variant: str) -> None:
        bk = self._push[sk]
        bk.last_attempt_t = self.time_fn()
        try:
            await self._transport(sk).show(frame, self.ttl_s)
        except TransportError as exc:
            bk.last_error = str(exc)
            self._record_error(sk, str(exc), exc.status)
            return
        bk.last_error = None
        bk.last_bytes = data
        bk.last_push_t = self.time_fn()
        bk.push_count += 1
        bk.kind, bk.variant = kind, variant

    def _record_error(self, where: str, message: str, status: int | None = None) -> None:
        self.errors.append({"at": time.time(), "where": where, "status": status,
                            "message": message})

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # --- state for the studio -------------------------------------------------------

    def state(self) -> dict[str, Any]:
        now_t = self.time_fn()
        sc = self._scenario_obj()
        pushes = {}
        for sk, bk in self._push.items():
            pushes[sk] = {
                "push_count": bk.push_count,
                "last_push_age_s": None if bk.last_push_t is None else now_t - bk.last_push_t,
                "kind": bk.kind,
                "variant": bk.variant,
                "last_error": bk.last_error,
            }
        showing = {k: v for k, v in self.showing.items() if k != "frames"}
        showing["variants"] = {sk: f["variant"] for sk, f in self.showing.get("frames", {}).items()}
        p = self.pipe.current
        idle = p.cfg.idle
        return {
            "mode": self.mode,
            "fixture": self.fixture,
            "fixture_position": self.fixture_position(),
            "loop_s": p.fixture_loop_s(self._fixture_obj().name),
            "fixture_fps": p.fixture_fps(self._fixture_obj().name),
            "idle_show": self.idle_show,
            "idle_show_shares": dict(SHOW_SHARES),
            "config_shares": {"page_pct": idle.page_pct, "scene_pct": idle.scene_pct},
            "weather": dict(self.weather),
            "scenario": self.scenario,
            "playing": self.playing,
            "speed": self.speed,
            "position": self.position(),
            "duration": float(sc.duration_s),
            "manual": dict(self.manual),
            "variants": {sk: dict(v) for sk, v in self.variants.items()},
            "feeder_alive": self.alive,
            "fast_expiry": self.fast_expiry,
            "heartbeat_s": self.heartbeat_s,
            "ttl_s": self.ttl_s,
            "base_url": self.base_url,
            "showing": showing,
            "pushes": pushes,
            "push_count": sum(b.push_count for b in self._push.values()),
            "errors": list(self.errors)[-10:],
            "log": self._log_block(),
        }


def _sample_weather(fixtures: Any) -> dict[str, Any]:
    """The weather control's starting values: the show fixtures' sample reading if the
    fixtures module has one, else :data:`WEATHER_DEFAULTS`."""
    out = dict(WEATHER_DEFAULTS)
    w = getattr(fixtures, "SAMPLE_WEATHER", None)
    if w is not None:
        out.update(code=int(w.code), is_day=bool(w.is_day), temp_f=float(w.temp_f))
    return out


def _check_weather(w: Any) -> dict[str, Any]:
    if w is None:
        return {}
    if not isinstance(w, dict):
        raise ControlError("weather must be an object")
    unknown = set(w) - set(WEATHER_KEYS)
    if unknown:
        raise ControlError(f"unknown weather field(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, v in w.items():
        if k == "code":
            codes = tuple(c for c, _ in WEATHER_CODES)
            if isinstance(v, bool) or v not in codes:
                raise ControlError(f"weather code must be one of {codes}")
            v = int(v)
        elif k == "temp_f":
            lo, hi = WEATHER_TEMP_RANGE
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ControlError(f"weather temp_f must be a number from {lo} to {hi}")
            v = float(v)
        elif not isinstance(v, bool):
            raise ControlError(f"weather {k} must be true or false")
        out[k] = v
    return out


def _rebuild(obj: Any, cls: Any) -> Any:
    """``obj`` as an instance of ``cls`` (the reloaded model class), copying shared fields."""
    if obj is None or isinstance(obj, cls):
        return obj
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)
                  if f.name in names})


#: Feeder-observed snapshot fields shown beside the robot's raw ones in the studio.
OBSERVED_FIELDS = ("status_since", "previous_status", "visit_started_at", "cat_left_at",
                   "last_visit_at")


def _observed_json(snap: Any) -> dict[str, Any] | None:
    if snap is None:
        return None
    out: dict[str, Any] = {}
    for k in OBSERVED_FIELDS:
        v = getattr(snap, k, None)
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


def _visit_json(tl: Any, v: Visit) -> dict[str, Any]:
    return {
        "id": v.id,
        "start": v.start.isoformat(),
        "end": v.end.isoformat() if v.end else None,
        "finished": v.finished,
        "duration_s": v.duration_s,
        "window_start": v.window_start.isoformat(),
        "window_s": v.window_s,
        "statuses": list(v.statuses),
        "changes": [{"at_s": (ts - v.window_start).total_seconds(), "status": s}
                    for ts, s in tl.changes(v)],
    }
