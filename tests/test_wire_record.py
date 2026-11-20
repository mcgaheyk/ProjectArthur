"""The wire client's record and history parsing against pylitterbot's own, offline.

pylitterbot builds a ``LitterRobot4`` from raw data without any network, so for every raw
robot here the record ``arthur.spikelog.build_record`` makes from pylitterbot's properties is
compared with the one ``arthur.wire.record.build_record`` makes from the raw data alone. The
raw data is synthetic (``wirefake.robot_raw``), varied over every status input. The real
robot's raw data lives only in Kyle's local logs; ``test_real_logs`` compares those when
``ARTHUR_REAL_LOGS`` points at that folder, and skips otherwise.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pylitterbot.robot.litterrobot4 import LitterRobot4
from pylitterbot.utils import to_timestamp
from wirefake import robot_raw

from arthur import spikelog
from arthur.sources.whisker import latest_weight_since, seeds_from_activity
from arthur.wire import lr4, record, util


def pylitterbot_record(raw: dict) -> dict:
    robot = LitterRobot4(data=copy.deepcopy(raw), account=None)
    return spikelog.build_record(robot)


def ours(raw: dict) -> dict:
    return record.build_record(copy.deepcopy(raw))


def test_lists_match_spikelog():
    assert record.TRACKED_PROPERTIES == spikelog.TRACKED_PROPERTIES
    assert record.TRACKED_RAW_KEYS == spikelog.TRACKED_RAW_KEYS


def test_model_has_pylitterbots_fields():
    from pylitterbot.robot.models import LITTER_ROBOT_4_MODEL

    def words(text):
        return sorted(text.replace("{", " ").replace("}", " ").split())

    assert words(lr4.MODEL) == words(LITTER_ROBOT_4_MODEL)


def test_status_tables_match_pylitterbot():
    from pylitterbot.enums import LitterBoxStatus
    from pylitterbot.robot import litterrobot4 as p

    assert {m.name: (m.value, m.text) for m in LitterBoxStatus} == lr4.STATUS
    assert {k: v.name for k, v in p.LR4_STATUS_MAP.items()} == lr4.ROBOT_STATUS
    assert {k: v.name for k, v in p.CYCLE_STATE_STATUS_MAP.items()} == lr4.CYCLE_STATE
    assert {k: v.name for k, v in p.DISPLAY_CODE_STATUS_MAP.items()} == lr4.DISPLAY_CODE
    assert {k: v.name for k, v in p.FAULT_DISPLAY_CODE_STATUS_MAP.items()} \
        == lr4.FAULT_DISPLAY_CODE
    statuses = {k: v.name for k, v in p.ACTIVITY_STATUS_MAP.items() if not isinstance(v, str)}
    words = {k: v for k, v in p.ACTIVITY_STATUS_MAP.items() if isinstance(v, str)}
    assert statuses == lr4.ACTIVITY
    assert words == lr4.ACTIVITY_WORDS


ROBOT_STATUSES = [*lr4.ROBOT_STATUS, "ROBOT_SOMETHING_NEW"]
CYCLE_STATES = ["CYCLE_STATE_WAIT_ON", "CYCLE_STATE_CAT_DETECT", "CYCLE_STATE_PAUSE",
                "CYCLE_STATE_IDLE"]
DISPLAY_CODES = ["DC_MODE_IDLE", "DC_CAT_DETECT", "DCX_REFRESH", *lr4.FAULT_DISPLAY_CODE, None]


@pytest.mark.parametrize("robot_status", ROBOT_STATUSES)
def test_every_status_combination(robot_status):
    for cycle, code, online, power, full in itertools.product(
            CYCLE_STATES, DISPLAY_CODES, (True, False), ("ON", "OFF"), (False, True)):
        raw = robot_raw(robotStatus=robot_status, robotCycleState=cycle, displayCode=code,
                        isOnline=online, unitPowerStatus=power, isDFIFull=full)
        assert ours(raw) == pylitterbot_record(raw), (robot_status, cycle, code, online, power)


SLEEP_ON = dict(robot_raw()["weekdaySleepModeEnabled"],
                Friday={"sleepTime": 1380, "wakeTime": 420, "isEnabled": True})

VARIANTS = [
    {},
    {"sleepStatus": "SLEEPING"}, {"sleepStatus": None}, {"weekdaySleepModeEnabled": SLEEP_ON},
    {"weekdaySleepModeEnabled": None}, {"weekdaySleepModeEnabled": {}},
    {"litterLevelState": "LOW"}, {"litterLevelState": "empty"}, {"litterLevelState": "NEW"},
    {"litterLevelState": None}, {"litterLevelPercentage": 0.0}, {"litterLevelPercentage": 0.6},
    {"litterLevelPercentage": 1}, {"hopperStatus": "ENABLED"}, {"hopperStatus": "jammed"},
    {"hopperStatus": "SOMETHING"}, {"globeMotorFaultStatus": "FAULT_PINCH"},
    {"globeMotorFaultStatus": "MtrFaultPinch"}, {"globeMotorFaultStatus": "MotorOvertorqueAmp"},
    {"globeMotorFaultStatus": "weird"}, {"globeMotorFaultStatus": ""},
    {"globeMotorFaultStatus": None}, {"globeMotorFaultStatus": "  FAULT_CLEAR "},
    {"lastSeen": None}, {"lastSeen": ""}, {"lastSeen": "2026-11-13T13:31:05Z"},
    {"lastSeen": "2026-11-13T13:31:05.000Z"}, {"lastSeen": "2026-11-13T13:31:05.1234567Z"},
    {"lastSeen": "2026-11-13T13:31:05.7+00:00"}, {"lastSeen": "2026-11-13 13:31:05"},
    {"lastSeen": 1794571865}, {"catWeight": 0}, {"catWeight": 12}, {"catWeight": None},
    {"DFILevelPercent": 100, "isDFIFull": True}, {"isLaserDirty": True},
    {"unitPowerType": "DC"}, {"unitPowerType": None}, {"cleanCycleWaitTime": 15},
    {"odometerCleanCycles": "3479"}, {"DFIFullCounter": 2}, {"isHopperRemoved": False},
    {"isOnline": None}, {"isOnline": "true"}, {"wifiRssi": -70},
]


@pytest.mark.parametrize("variant", VARIANTS, ids=[json.dumps(v)[:40] for v in VARIANTS])
def test_field_variants(variant):
    raw = robot_raw(**variant)
    assert ours(raw) == pylitterbot_record(raw)


MISSING = ["catWeight", "cleanCycleWaitTime", "unitPowerType", "lastSeen",
           "odometerCleanCycles", "DFINumberOfCycles", "DFIFullCounter", "litterLevelPercentage",
           "DFILevelPercent", "hopperStatus", "isOnline", "unitPowerStatus", "sleepStatus",
           "displayCode", "weekdaySleepModeEnabled", "globeMotorFaultStatus"]


@pytest.mark.parametrize("key", MISSING)
def test_missing_field(key):
    raw = robot_raw()
    del raw[key]
    assert ours(raw) == pylitterbot_record(raw)


@pytest.mark.parametrize("key", ["robotStatus", "robotCycleState"])
def test_missing_status_field_is_an_error_string(key):
    """pylitterbot indexes these two directly. A payload without them makes its
    ``_update_data`` raise (it reads ``status`` for ``_update_minimum_cycles_left``), so the
    robot cannot even be built; we keep the record and mark the status fields broken."""
    raw = robot_raw()
    del raw[key]
    with pytest.raises(KeyError):
        LitterRobot4(data=copy.deepcopy(raw), account=None)
    mine = ours(raw)
    assert mine["status"] == mine["status_code"] == mine["status_text"] == "<error: KeyError>"
    assert mine["pet_weight"] == raw["catWeight"]


def test_null_fields_raise_like_pylitterbot():
    raw = robot_raw(litterLevelPercentage=None, odometerCleanCycles=None)
    mine = ours(raw)
    assert mine == pylitterbot_record(raw)
    assert mine["litter_level"] == "<error: TypeError>"
    assert mine["cycle_count"] == "<error: TypeError>"


def test_partial_updates_merge_like_pylitterbot():
    """A subscription message carrying only some keys leaves the others as they were."""
    from arthur.wire.client import WhiskerClient

    base = robot_raw()
    robot = LitterRobot4(data=copy.deepcopy(base), account=None)
    client = WhiskerClient(None)
    client.merge(copy.deepcopy(base))
    for update in ({"robotStatus": "ROBOT_CAT_DETECT", "displayCode": "DC_CAT_DETECT"},
                   {"catWeight": 9.75}, {"robotStatus": "ROBOT_CLEAN", "unitId": "UNIT-1"},
                   {"isOnline": False}):
        robot._update_data(copy.deepcopy(update))
        client.merge(copy.deepcopy(update))
        assert client.record() == spikelog.build_record(robot)


# -- the real robot's raw data, from Kyle's local logs (never copied into the repo) -------------


def _real_log_dir() -> Path | None:
    value = os.environ.get("ARTHUR_REAL_LOGS")
    if value:
        return Path(value)
    here = Path(__file__).resolve().parents[1] / "logs"
    return here if here.is_dir() else None


def test_real_logs():
    folder = _real_log_dir()
    if folder is None or not list(folder.glob("spike-2*.jsonl")):
        pytest.skip("no local spike logs (set ARTHUR_REAL_LOGS to the folder to compare)")
    compared = 0
    for path in sorted(folder.glob("spike-2*.jsonl")):
        for line in path.read_text().splitlines():
            d = json.loads(line)
            if d.get("kind") != "state" or "raw" not in d:
                continue
            compared += 1
            assert record.build_record(d["raw"]) == d["record"], (path.name, d["ts"])
    assert compared > 0


# -- the history -------------------------------------------------------------------------------

ENTRIES = [
    {"timestamp": "2026-11-16T04:22:49.000Z", "value": "robotStatusCatDetect"},
    {"timestamp": "2026-11-16T04:23:57.000Z", "value": "catWeight", "actionValue": 4.67},
    {"timestamp": "2026-11-16T04:23:58", "value": "catWeight", "actionValue": "9.5"},
    {"timestamp": "2026-11-16T04:31:55.5Z", "value": "robotCycleStatusDump"},
    {"timestamp": "2026-11-16T04:34:06.000Z", "value": "robotCycleStatusIdle"},
    {"timestamp": "2026-11-16T04:34:06.000Z", "value": "odometerCleanCycles",
     "actionValue": 3479},
    {"timestamp": "2026-11-16T04:34:07.000Z", "value": "DFILevelPercent", "actionValue": 68},
    {"timestamp": "2026-11-16T04:35:00.000Z", "value": "litterHopperDispensed",
     "actionValue": 2},
    {"timestamp": "2026-11-16T04:36:00.000Z", "value": "powerTypeDC"},
    {"timestamp": "2026-11-16T04:37:00.000Z", "value": "robotCycleStateCatDetect"},
    {"timestamp": "2026-11-16T04:38:00.000Z", "value": "DFIFullFlagOn"},
    {"timestamp": None, "value": "robotStatusCatDetect"},
    {"timestamp": "", "value": "robotStatusCatDetect"},
]


def pylitterbot_activities(entries):
    robot = LitterRobot4(data=robot_raw(), account=None)
    return [(to_timestamp(e["timestamp"]), robot._parse_activity(e)) for e in entries
            if to_timestamp(e["timestamp"]) is not None]


def test_activities_match_pylitterbot():
    mine = lr4.parse_activities(copy.deepcopy(ENTRIES))
    theirs = pylitterbot_activities(copy.deepcopy(ENTRIES))
    assert len(mine) == len(theirs)
    for a, (ts, action) in zip(mine, theirs, strict=True):
        assert a.timestamp == ts
        assert a.ts == ts.timestamp()
        assert a.action.text == spikelog.to_jsonable(getattr(action, "text", None) or action)
        code = action.value if hasattr(action, "value") else None
        assert a.action.value == code


def test_seeds_are_the_same_from_our_activities():
    from pylitterbot.activity import Activity

    mine = lr4.parse_activities(copy.deepcopy(ENTRIES))
    theirs = [Activity(ts, action) for ts, action in pylitterbot_activities(ENTRIES)]
    for mid in (False, True):
        assert seeds_from_activity(mine, mid_visit=mid) == seeds_from_activity(theirs,
                                                                              mid_visit=mid)
    since = datetime(2026, 11, 16, 4, 22, tzinfo=UTC)
    assert latest_weight_since(mine, since) == latest_weight_since(theirs, since)


def test_record_activity_writes_the_same_lines(tmp_path):
    """The feeder's activity log (``SpikeLogger.record_activity``) writes the same lines
    from our activities as from pylitterbot's, ``code`` included (pylitterbot's
    ``LitterBoxStatus.value``, our ``Action.value``)."""
    from pylitterbot.activity import Activity

    def lines(acts, sub):
        logger = spikelog.SpikeLogger(tmp_path / sub, echo=False)
        logger.record_activity(acts)
        logger.close()
        out = []
        for f in sorted((tmp_path / sub).glob("spike-activity-*.jsonl")):
            out += [{k: v for k, v in json.loads(x).items() if k != "fetched"}
                    for x in f.read_text().splitlines()]
        return out

    theirs = [Activity(ts, action) for ts, action in pylitterbot_activities(ENTRIES)]
    mine = lr4.parse_activities(copy.deepcopy(ENTRIES))
    a, b = lines(theirs, "p"), lines(mine, "w")
    assert [x["action"] for x in a] == [x["action"] for x in b]
    assert [x["timestamp"] for x in a] == [x["timestamp"] for x in b]
    assert [x.get("code") for x in a] == [m.action.value for m in mine]
    assert a == b


def test_api_timestamp_rounds_up_like_pylitterbot():
    assert util.api_timestamp(datetime(2026, 11, 16, 4, 22, 49, tzinfo=UTC).timestamp()) \
        == "2026-11-16 04:22:49"
    assert util.api_timestamp(datetime(2026, 11, 16, 4, 22, 49, 1, tzinfo=UTC).timestamp()) \
        == "2026-11-16 04:22:50"
