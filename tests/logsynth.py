"""Synthetic spike logs for tests. Shaped like arthur.spikelog's lines; no real data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

CODES = {"READY": "RDY", "CAT_DETECTED": "CD", "CAT_SENSOR_TIMING": "CST",
         "CLEAN_CYCLE": "CCP", "CAT_SENSOR_INTERRUPTED": "CSI", "OFFLINE": "OFFLINE"}
ROBOT_STATUS = {"READY": "ROBOT_IDLE", "CAT_DETECTED": "ROBOT_CAT_DETECT",
                "CAT_SENSOR_TIMING": "ROBOT_CAT_DETECT_DELAY", "CLEAN_CYCLE": "ROBOT_CLEAN"}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def record(status: str, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "status": status,
        "status_code": CODES.get(status, status),
        "is_online": True,
        "is_on": True,
        "is_sleeping": False,
        "waste_drawer_level": 40,
        "litter_level": 80.0,
        "litter_level_state": "OPTIMAL",
        "pet_weight": 9.5,
        "clean_cycle_wait_time_minutes": 8,
        "is_laser_dirty": False,
        "power_type": "AC",
        "last_seen": "2026-03-01T11:00:00+00:00",
        "robotStatus": ROBOT_STATUS.get(status, "ROBOT_IDLE"),
        "robotCycleState": "CYCLE_STATE_WAIT_ON",
        "robotCycleStatus": "CYCLE_IDLE",
        "displayCode": "DC_MODE_IDLE",
        "catDetect": "CAT_DETECT_CLEAR",
    }
    r.update(kw)
    return r


def state(seconds: float, status: str, source: str = "ws", **kw: Any) -> dict[str, Any]:
    return {"ts": iso(at(seconds)), "t": float(seconds), "kind": "state", "source": source,
            "status": status, "status_code": CODES.get(status, status), "changed": {},
            "record": record(status, **kw)}


def info(seconds: float, event: str) -> dict[str, Any]:
    return {"ts": iso(at(seconds)), "t": float(seconds), "kind": "info", "event": event}


def error(seconds: float, where: str, retry_in: float | None = 30.0) -> dict[str, Any]:
    return {"ts": iso(at(seconds)), "t": float(seconds), "kind": "error", "where": where,
            "error": "ClientError", "message": "boom", "retry_in": retry_in}


def activity(seconds: float, action: str) -> dict[str, Any]:
    return {"fetched": iso(at(seconds)), "kind": "activity",
            "timestamp": at(seconds).isoformat(timespec="seconds"), "action": action}


def write(path: Path, objs: list[dict[str, Any]], tail: str = "", mode: str = "w") -> None:
    with open(path, mode, encoding="utf-8") as fh:
        fh.writelines(json.dumps(o) + "\n" for o in objs)
        fh.write(tail)


def visit_lines(start: float = 100.0, *, wait: bool = True) -> list[dict[str, Any]]:
    """ready → CAT_DETECTED 60 s → (CAT_SENSOR_TIMING 420 s) → CLEAN_CYCLE 130 s → READY.

    The pet weight changes (the cat has left) 60 s after detection: on the CAT_SENSOR_TIMING
    line, or on a CAT_DETECTED line of its own when ``wait`` is false.
    """
    lines = [state(0, "READY", source="start"), state(start, "CAT_DETECTED")]
    t = start + 60
    if wait:
        lines.append(state(t, "CAT_SENSOR_TIMING", pet_weight=9.7))
        t += 420
    else:
        lines.append(state(t, "CAT_DETECTED", pet_weight=9.7))
        t += 420
    lines.append(state(t, "CLEAN_CYCLE", pet_weight=9.7))
    lines.append(state(t + 130, "READY", pet_weight=9.7))
    return lines


#: Seconds from the clean cycle's start to each change in the one interruption logged live
#: (2026-11-21): interrupted 40.1 s in, cycling again 25.27 s later, ready 85.13 s after that.
INTERRUPTION_OFFSETS = (("CAT_SENSOR_INTERRUPTED", 40.1), ("CLEAN_CYCLE", 65.37),
                        ("READY", 150.5))


def interrupted_visit_lines(start: float = 100.0) -> list[dict[str, Any]]:
    """As :func:`visit_lines`, but the clean cycle is interrupted the way the real one was
    (:data:`INTERRUPTION_OFFSETS`) and then carries on to READY."""
    lines = visit_lines(start)[:-1]  # drop the READY 130 s after the clean start
    clean = start + 60 + 420
    lines += [state(clean + dt, status, pet_weight=9.7) for status, dt in INTERRUPTION_OFFSETS]
    return lines
