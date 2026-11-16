"""The studio's in-process feeder: source → mapping → render → AwtrixNgTransport → fake clock.

It runs the real pipeline end to end. The source is one of:

- ``scenario``: a timed scenario from :mod:`arthur.fixtures`, played on its own simulated
  clock (``start + t``) with play/pause, seek and speed;
- ``fixture``: one named fixture, frozen at the fixture's ``now`` except that the seconds
  alternate (``now`` and ``now + 1 s``) so blinking states blink;
- ``manual``: a snapshot built from hand-set fields at the real current time.

It renders a 52x16 frame and pushes it over HTTP through the real
:class:`~arthur.transports.awtrix_ng.AwtrixNgTransport` when the frame changes or when the
heartbeat is due, with ``lifetimeMs`` = ttl. Switched "dead", it stops pushing, so the clock's
own stale mark appears once the lifetime runs out. The fake clock's lifetime runs on real time.

Variants, push bookkeeping and frames are keyed by the size string ("52x16"), which has one
entry; the control API takes variants as ``{"52x16": {kind: variant}}``.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from arthur.sim.pipeline import ReloadingPipeline, size_key
from arthur.transports.awtrix_ng import AwtrixNgTransport
from arthur.transports.base import TransportError

MODES = ("scenario", "fixture", "manual")
SPEEDS = (1, 10, 60)
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
    "wait_time_minutes": 7,
}
LITTER_STATES = (None, "OVERFILL", "OPTIMAL", "REFILL", "LOW", "EMPTY")
POWER_TYPES = ("AC", "DC", "NC")
WAIT_MINUTES = (3, 7, 15, 25, 30)
_FAULT_CODES = {"PINCH_DETECT": "PD", "OVER_TORQUE_FAULT": "OTF", "CAT_SENSOR_FAULT": "CSF",
                "UNKNOWN": "UNK"}


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

        self.manual: dict[str, Any] = dict(MANUAL_DEFAULTS)
        now = wall_fn()
        self._manual_since = now - timedelta(hours=2)
        self._manual_prev: str | None = "CLEAN_CYCLE"
        self._offline_since: datetime | None = None

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
                 "variants", "feeder_alive", "fast_expiry"}
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
        for k in ("playing", "feeder_alive", "fast_expiry"):
            if k in ctl and not isinstance(ctl[k], bool):
                raise ControlError(f"{k} must be true or false")
        manual = self._check_manual(ctl.get("manual"))
        variants = self._check_variants(ctl.get("variants"))

        # Apply.
        if "mode" in ctl and ctl["mode"] != self.mode:
            self.mode = ctl["mode"]
            self._fixture_t0 = self.time_fn()
        if "scenario" in ctl and ctl["scenario"] != self.scenario:
            self.scenario = ctl["scenario"]
            self._pos, self._anchor = 0.0, self.time_fn()
        if "fixture" in ctl:
            self.fixture = ctl["fixture"]
            self._fixture_t0 = self.time_fn()
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
                if v not in WAIT_MINUTES:
                    raise ControlError(f"wait_time_minutes must be one of {WAIT_MINUTES}")
            elif not isinstance(v, bool):
                raise ControlError(f"{k} must be true or false")
            out[k] = v
        return out

    def _apply_manual(self, m: dict[str, Any]) -> None:
        now = self.wall_fn()
        if "status" in m and m["status"] != self.manual["status"]:
            self._manual_prev = self.manual["status"]
            self._manual_since = now
        if "is_online" in m and m["is_online"] != self.manual["is_online"]:
            self._offline_since = None if m["is_online"] else now
        self.manual.update(m)

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
        p = self.pipe.current
        if self.mode == "scenario":
            sc = self._scenario_obj()
            t = self.position()
            snap, health, now = sc.at(t)
            return snap, health, now, sc.step_at(t).label
        if self.mode == "fixture":
            fxs = p.fixtures.FIXTURES
            if self.fixture not in fxs:
                self.fixture = next(iter(fxs))
            fx = fxs[self.fixture]
            tick = int(self.time_fn() - self._fixture_t0) % 2
            return fx.snapshot, fx.health, fx.now + timedelta(seconds=tick), fx.name
        return (*self._manual_source(), "manual")

    def _manual_source(self) -> tuple[Any, Any, datetime]:
        p = self.pipe.current
        m = self.manual
        now = self.wall_fn()
        status = m["status"]
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
            last_seen=self._offline_since if not m["is_online"] and self._offline_since else now,
            pet_weight_lbs=10.4,
            status_since=self._manual_since,
            previous_status=self._manual_prev,
            last_visit_at=now - timedelta(hours=2),
            visits_today=3,
        )
        health = p.model.FeederHealth(
            last_contact_at=now, ws_connected=m["ws_connected"], error=m["health_error"]
        )
        return snap, health, now

    def compute(self) -> dict[str, Any]:
        """Map and render the current source."""
        p = self.pipe.current
        snap, health, now, label = self.source()
        state = p.state(snap, health, now)
        frames = {}
        for size in p.sizes():
            sk = size_key(size)
            variant = p.resolve_variant(state.kind, size, self.variants.get(sk, {}).get(state.kind))
            frames[sk] = {"frame": p.render(state, now, size, variant), "variant": variant}
        return {
            "kind": state.kind,
            "detail": state.detail,
            "badges": sorted(state.badges),
            "sim_time": now.isoformat(),
            "label": label,
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
        return {
            "mode": self.mode,
            "fixture": self.fixture,
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
        }
