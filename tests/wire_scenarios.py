"""Scripted runs of the fake Whisker for the two live sources (CPython only; the scenarios
are handed to the MicroPython run as JSON).

Each scenario is a dict:

- ``robots``: the account's robots (raw), ``history``: entries ``[t, value, actionValue]``
  already in the robot's history at the start (``t`` in seconds from the start, negative for
  the past), ``token``: ``fresh``, ``expired`` or ``none`` (the saved token file),
  ``password``: ``right`` or ``wrong``, ``reject_refresh``.
- ``events``: what happens when, for :mod:`wire_source_driver` (``push``, ``set``,
  ``history``, ``revoke``, ``drop``, ``silent``, ``fail``, ``password``, ``robots``,
  ``asleep``, ``probe``).
- ``stop_at``: when the run ends.

Times are whole milliseconds, so both Pythons date every line the same.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from wire_source_driver import probes_every
from wirefake import robot_raw
from wirehelp import real_lines, real_raws

START = datetime(2026, 11, 15, 13, 0, 0, tzinfo=UTC)
START_US = int(START.timestamp()) * 1_000_000


def _ms(t: float) -> float:
    return round(t * 1000) / 1000


def _finish(name, robots, events, stop_at, *, history=(), token="fresh", password="right",
            reject_refresh=False, probe_step=10, expect_wire=None):
    events = list(events)
    for ev in list(events):
        if ev["do"] == "push":
            events.append({"t": ev["t"], "do": "probe"})
    events += probes_every(probe_step, stop_at)
    for ev in events:
        ev["t"] = _ms(ev["t"])
    out = {"name": name, "start_us": START_US, "robots": robots, "history": list(history),
           "token": token, "password": password, "reject_refresh": reject_refresh,
           "events": events, "stop_at": stop_at}
    return json.loads(json.dumps(out))  # plain JSON, as the MicroPython run gets it


def push(t, **update):
    return {"t": t, "do": "push", "update": update}


def status(name: str) -> dict:
    """Raw fields for a status."""
    return {
        "idle": {"robotStatus": "ROBOT_IDLE", "displayCode": "DC_MODE_IDLE",
                 "robotCycleState": "CYCLE_STATE_WAIT_ON", "robotCycleStatus": "CYCLE_IDLE"},
        "detect": {"robotStatus": "ROBOT_CAT_DETECT", "displayCode": "DC_CAT_DETECT"},
        "timing": {"robotStatus": "ROBOT_CAT_DETECT_DELAY", "displayCode": "DC_MODE_IDLE"},
        "clean": {"robotStatus": "ROBOT_CLEAN", "robotCycleState": "CYCLE_STATE_PROCESS",
                  "robotCycleStatus": "CYCLE_DUMP", "displayCode": "DC_MODE_CYCLE"},
    }[name]


def visit(t0, weight=None, cycles=None, *, timing=True, clean_after=480, history=False):
    """A watched visit from ``t0``: detection, timing at +6 s, the weight at +60 s (if
    given), the clean at +60+``clean_after``, ready 130 s later. With ``history`` the
    robot's history gets the detection and the weight as they happen."""
    ev = [push(t0, **status("detect"))]
    if timing:
        ev.append(push(t0 + 6, **status("timing")))
    if weight is not None:
        ev.append(push(t0 + 60, catWeight=weight))
    clean = t0 + 60 + clean_after
    up = dict(status("clean"))
    if cycles is not None:
        up["odometerCleanCycles"] = cycles
    ev.append(push(clean, **up))
    ev.append(push(clean + 130, **status("idle")))
    if history:
        ev.append({"t": t0 + 1, "do": "history", "add": [[t0, "robotStatusCatDetect", None]]})
        if weight is not None:
            ev.append({"t": t0 + 61, "do": "history", "add": [[t0 + 60, "catWeight", weight]]})
        ev.append({"t": clean + 1, "do": "history",
                   "add": [[clean, "robotCycleStatusDump", None]]})
    return ev


EARLIER = [[-5 * 3600, "robotStatusCatDetect", None], [-5 * 3600 + 60, "catWeight", 9.5],
           [-5 * 3600 + 540, "robotCycleStatusDump", None],
           [-5 * 3600 + 670, "robotCycleStatusIdle", None]]


# =============================================================================================
# The scenarios
# =============================================================================================


def real_visits():
    """The spike's real visits (tests/data/real-cycles.jsonl), pushed with their own timing
    (idle stretches cut to 30 minutes), and their detections and weights in the history."""
    lines, raws = real_lines(), real_raws()
    events = []
    t = 60.0
    prev = None
    for i, (line, raw) in enumerate(zip(lines[1:], raws[1:], strict=True), start=1):
        ts = datetime.fromisoformat(line["ts"])
        if prev is not None:
            t += min((ts - prev).total_seconds(), 1800.0)
        prev = ts
        events.append({"t": t, "do": "push", "update": raw})
        changed = line["changed"]
        # A detection the robot weighed is a visit in its history; the third cycle's (a clean
        # 5 s after it, no weight) is not, as in the real history (D-0025).
        weighed = any("pet_weight" in x["changed"] for x in lines[i:i + 4])
        if "status" in changed and line["status"] == "CAT_DETECTED" and weighed:
            events.append({"t": t + 1, "do": "history", "add": [[t, "robotStatusCatDetect",
                                                                  None]]})
        if "pet_weight" in changed:
            events.append({"t": t + 1, "do": "history",
                           "add": [[t, "catWeight", line["record"]["pet_weight"]]]})
    return _finish("real_visits", [raws[0]], events, t + 400, history=EARLIER)


def no_weight():
    """Two visits with no weight pushed: the first's weight is in the history by the time the
    fallback reads it (waiting, counted from it); the second's never is (waiting, no
    count)."""
    events = [push(100, **status("detect")),
              {"t": 130, "do": "history", "add": [[100, "robotStatusCatDetect", None],
                                                  [125, "catWeight", 4.67]]},
              push(700, **status("clean"), odometerCleanCycles=3479),
              push(830, **status("idle")),
              push(2000, **status("detect")),
              push(2600, **status("clean"), odometerCleanCycles=3480),
              push(2730, **status("idle"))]
    return _finish("no_weight", [robot_raw()], events, 3000, history=EARLIER)


def gap_missed_visit():
    """The night of 2026-11-18: contact, the machine asleep for an hour with a visit in it,
    then a poll that shows the clean it caused (D-0025)."""
    events = [{"t": 700, "do": "asleep", "s": 3620},
              {"t": 700, "do": "history", "add": [
                  [1500, "robotStatusCatDetect", None], [1560, "catWeight", 9.6],
                  [1980, "robotCycleStatusDump", None], [2110, "robotCycleStatusIdle", None]]},
              {"t": 700, "do": "set", "update": {"odometerCleanCycles": 3479,
                                                 "catWeight": 9.6}}]
    return _finish("gap_missed_visit", [robot_raw()], events, 4700, history=EARLIER)


def gap_mid_visit():
    """A gap that ends mid-visit: its start and when he left come from the history."""
    events = [{"t": 700, "do": "asleep", "s": 3620},
              {"t": 700, "do": "history", "add": [[4250, "robotStatusCatDetect", None],
                                                  [4290, "catWeight", 9.7]]},
              {"t": 700, "do": "set", "update": dict(status("timing"), catWeight=9.7)},
              push(4400, **status("clean"), odometerCleanCycles=3479),
              push(4530, **status("idle"))]
    return _finish("gap_mid_visit", [robot_raw()], events, 4700, history=EARLIER)


def clean_no_visit():
    """A clean cycle with no visit seen (a manual clean, or a detection we missed)."""
    events = [push(1000, **status("clean"), odometerCleanCycles=3479),
              push(1130, **status("idle")),
              {"t": 1100, "do": "history", "add": [[990, "robotStatusCatDetect", None]]}]
    return _finish("clean_no_visit", [robot_raw()], events, 1600)


def watched_visits():
    """Two visits watched whole, with their weights pushed; the routine read six hours on."""
    events = (visit(100, 9.7, 3479, history=True) + visit(1500, 9.8, 3480, timing=False,
                                                           history=True)
              + [{"t": 3000, "do": "history", "add": [[2900, "robotStatusCatDetect", None]]}])
    return _finish("watched_visits", [robot_raw()], events, 6 * 3600 + 100, history=EARLIER,
                   probe_step=60)


def drawer_full():
    events = [push(100, isDFIFull=True, DFILevelPercent=100, DFIFullCounter=1),
              push(400, isDFIFull=False, DFILevelPercent=0, DFIFullCounter=0)]
    return _finish("drawer_full", [robot_raw()], events, 700)


def fault():
    events = [push(100, displayCode="DC_MTR_FAULT_PINCH", globeMotorFaultStatus="FAULT_PINCH"),
              push(300, displayCode="DC_MODE_IDLE", globeMotorFaultStatus="FAULT_CLEAR"),
              push(400, robotCycleState="CYCLE_STATE_PAUSE"),
              push(450, robotCycleState="CYCLE_STATE_WAIT_ON")]
    return _finish("fault", [robot_raw()], events, 700)


def sleeping():
    events = [push(100, sleepStatus="SLEEPING"), push(400, sleepStatus="WAKE")]
    return _finish("sleeping", [robot_raw()], events, 700)


def offline():
    events = [push(100, isOnline=False),
              {"t": 200, "do": "set", "update": {"isOnline": True}},  # the poll at 300 sees it
              push(900, unitPowerStatus="OFF"), push(1000, unitPowerStatus="ON")]
    return _finish("offline", [robot_raw()], events, 1300)


def dropped_socket():
    events = [{"t": 200, "do": "drop"}] + visit(400, 9.7, 3479)
    return _finish("dropped_socket", [robot_raw()], events, 1300)


def stale_socket():
    events = [{"t": 200, "do": "silent"}] + visit(400, 9.7, 3479)
    return _finish("stale_socket", [robot_raw()], events, 1300)


def poll_401():
    """Every token issued so far is refused from 250 s: the poll at 300 gets a 401."""
    events = [{"t": 250, "do": "revoke"}] + visit(400, 9.7, 3479)
    return _finish("poll_401", [robot_raw()], events, 1300)


def poll_500():
    events = [{"t": 290, "do": "fail", "op": "graphql", "action": ["status", 500]}]
    return _finish("poll_500", [robot_raw()], events, 800)


def rejected_refresh():
    """The saved token has expired and its renewal is refused: one password login."""
    return _finish("rejected_refresh", [robot_raw()], visit(100, 9.7, 3479), 900,
                   token="expired", reject_refresh=True)


def login_rejected():
    """No saved token and a wrong password: NO DATA (auth), retried after 600 s and 1200 s;
    the password is put right at 1000 s."""
    events = [{"t": 1000, "do": "password", "value": "right"}]
    return _finish("login_rejected", [robot_raw()], events, 2000, token="none",
                   password="wrong", probe_step=60)


def no_robots():
    events = [{"t": 50, "do": "robots", "robots": [robot_raw()]}]
    return _finish("no_robots", [], events, 400)


def two_robots():
    return _finish("two_robots", [robot_raw(), robot_raw(serial="LR4C000002",
                                                         unit_id="UNIT-2")],
                   [push(100, **status("detect"))], 400)


def partial_update():
    """A visit whose weight comes in a push carrying only the changed keys."""
    events = [push(100, **status("detect")),
              {"t": 160, "do": "push", "update": {"catWeight": 9.9}, "partial": True},
              push(640, **status("clean"), odometerCleanCycles=3479),
              push(770, **status("idle"))]
    return _finish("partial_update", [robot_raw()], events, 1000)


def an_hour():
    """An hour with three visits and the routine traffic: the on-clock heap measure."""
    raws = real_raws()
    events = (visit(120, 9.7, 3479, history=True) + visit(1500, 9.8, 3480, history=True)
              + visit(2900, 9.9, 3481, timing=False, history=True)
              + [push(3500, wifiRssi=-60.0, weightSensor=0.4)])
    return _finish("an_hour", [raws[0]], events, 3600, history=EARLIER, probe_step=60)


SCENARIOS = {f.__name__: f for f in (
    real_visits, no_weight, gap_missed_visit, gap_mid_visit, clean_no_visit, watched_visits,
    drawer_full, fault, sleeping, offline, dropped_socket, stale_socket, poll_401, poll_500,
    rejected_refresh, login_rejected, no_robots, two_robots, partial_update, an_hour)}
