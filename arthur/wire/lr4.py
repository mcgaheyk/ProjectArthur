"""The Litter-Robot 4's GraphQL: field list, queries, subscription messages, statuses and the
activity history. Everything here is read in pylitterbot 2025.8.0 ``robot/litterrobot4.py``,
``robot/models.py`` and ``enums.py``; ``docs/reference/whisker-wire.md`` cites each piece.
"""

import json

from arthur.wire import util
from arthur.wire.const import LR4_HOST, LR4_URL

#: ``LITTER_ROBOT_4_MODEL`` (``robot/models.py``), the same fields, whitespace squeezed.
_SLEEP = "{sleepTime wakeTime isEnabled}"
MODEL = (
    "{unitId name serial userId espFirmware picFirmwareVersion picFirmwareVersionHex "
    "laserBoardFirmwareVersion laserBoardFirmwareVersionHex wifiRssi unitPowerType catWeight "
    "displayCode unitTimezone unitTime cleanCycleWaitTime isKeypadLockout nightLightMode "
    "nightLightBrightness isPanelSleepMode panelSleepTime panelWakeTime "
    "weekdaySleepModeEnabled {Sunday " + _SLEEP + " Monday " + _SLEEP + " Tuesday " + _SLEEP
    + " Wednesday " + _SLEEP + " Thursday " + _SLEEP + " Friday " + _SLEEP + " Saturday "
    + _SLEEP + "} "
    "unitPowerStatus sleepStatus robotStatus globeMotorFaultStatus pinchStatus catDetect "
    "isBonnetRemoved isNightLightLEDOn odometerPowerCycles odometerCleanCycles "
    "panelBrightnessHigh panelBrightnessLow smartWeightEnabled odometerEmptyCycles "
    "odometerFilterCycles isDFIResetPending DFINumberOfCycles DFILevelPercent isDFIFull "
    "DFIFullCounter DFITriggerCount litterLevel DFILevelMM isCatDetectPending "
    "globeMotorRetractFaultStatus robotCycleStatus robotCycleState weightSensor isOnline "
    "isOnboarded isProvisioned isDebugModeActive lastSeen sessionId setupDateTime "
    "isFirmwareUpdateTriggered firmwareUpdateStatus wifiModeStatus isUSBPowerOn "
    "USBFaultStatus isDFIPartialFull isLaserDirty surfaceType hopperStatus scoopsSavedCount "
    "isHopperRemoved optimalLitterLevel litterLevelPercentage litterLevelState}"
)

#: pylitterbot ``LitterBoxStatus`` (``enums.py``): name -> (code, text).
STATUS = {
    "BONNET_REMOVED": ("BR", "Bonnet Removed"),
    "CLEAN_CYCLE_COMPLETE": ("CCC", "Clean Cycle Complete"),
    "CLEAN_CYCLE": ("CCP", "Clean Cycle In Progress"),
    "CAT_DETECTED": ("CD", "Cat Detected"),
    "CAT_SENSOR_FAULT": ("CSF", "Cat Sensor Fault"),
    "CAT_SENSOR_INTERRUPTED": ("CSI", "Cat Sensor Interrupted"),
    "CAT_SENSOR_TIMING": ("CST", "Cat Sensor Timing"),
    "DRAWER_FULL_1": ("DF1", "Drawer Almost Full - 2 Cycles Left"),
    "DRAWER_FULL_2": ("DF2", "Drawer Almost Full - 1 Cycle Left"),
    "DRAWER_FULL": ("DFS", "Drawer Full"),
    "DUMP_HOME_POSITION_FAULT": ("DHF", "Dump + Home Position Fault"),
    "DUMP_POSITION_FAULT": ("DPF", "Dump Position Fault"),
    "EMPTY_CYCLE": ("EC", "Empty Cycle"),
    "HOME_POSITION_FAULT": ("HPF", "Home Position Fault"),
    "OFF": ("OFF", "Off"),
    "OFFLINE": ("OFFLINE", "Offline"),
    "OVER_TORQUE_FAULT": ("OTF", "Over Torque Fault"),
    "PAUSED": ("P", "Clean Cycle Paused"),
    "PINCH_DETECT": ("PD", "Pinch Detect"),
    "POWER_DOWN": ("PWRD", "Powering Down"),
    "POWER_UP": ("PWRU", "Powering Up"),
    "READY": ("RDY", "Ready"),
    "STARTUP_CAT_SENSOR_FAULT": ("SCF", "Cat Sensor Fault At Startup"),
    "STARTUP_DRAWER_FULL": ("SDF", "Drawer Full At Startup"),
    "STARTUP_PINCH_DETECT": ("SPF", "Pinch Detect At Startup"),
    "UNKNOWN": (None, "Unknown"),
}

#: ``LR4_STATUS_MAP``: raw ``robotStatus`` -> status name.
ROBOT_STATUS = {
    "ROBOT_BONNET": "BONNET_REMOVED",
    "ROBOT_CAT_DETECT": "CAT_DETECTED",
    "ROBOT_CAT_DETECT_DELAY": "CAT_SENSOR_TIMING",
    "ROBOT_CLEAN": "CLEAN_CYCLE",
    "ROBOT_EMPTY": "EMPTY_CYCLE",
    "ROBOT_FIND_DUMP": "CLEAN_CYCLE",
    "ROBOT_IDLE": "READY",
    "ROBOT_POWER_DOWN": "POWER_DOWN",
    "ROBOT_POWER_OFF": "OFF",
    "ROBOT_POWER_UP": "POWER_UP",
}
#: ``CYCLE_STATE_STATUS_MAP``.
CYCLE_STATE = {"CYCLE_STATE_CAT_DETECT": "CAT_SENSOR_INTERRUPTED",
               "CYCLE_STATE_PAUSE": "PAUSED"}
#: ``DISPLAY_CODE_STATUS_MAP``.
DISPLAY_CODE = {"DC_CAT_DETECT": "CAT_DETECTED"}
#: ``FAULT_DISPLAY_CODE_STATUS_MAP`` (new in 2025.8.0, checked before everything else).
FAULT_DISPLAY_CODE = {
    "DC_CAT_DETECT_30M": "CAT_SENSOR_FAULT",
    "DC_MTR_FAULT_OT_AMP": "OVER_TORQUE_FAULT",
    "DC_MTR_FAULT_OT_SLP": "OVER_TORQUE_FAULT",
    "DC_MTR_FAULT_PINCH": "PINCH_DETECT",
}
#: ``ACTIVITY_STATUS_MAP``: an activity's raw ``value`` -> status name, or words.
ACTIVITY = {
    "bonnetRemovedYes": "BONNET_REMOVED",
    "catDetectStuckLaser": "CAT_SENSOR_FAULT",
    "DFIFullFlagOn": "DRAWER_FULL",
    "robotCycleStateCatDetect": "CAT_SENSOR_INTERRUPTED",
    "robotCycleStatusDump": "CLEAN_CYCLE",
    "robotCycleStatusIdle": "CLEAN_CYCLE_COMPLETE",
    "robotStatusCatDetect": "CAT_DETECTED",
}
ACTIVITY_WORDS = {
    "catWeight": "Pet Weight Recorded",
    "litterHopperDispensed": "Litter Dispensed",
    "odometerCleanCycles": "Clean Cycles",
    "powerTypeDC": "Battery Backup",
}


# -- requests --------------------------------------------------------------------------------


def robots_query(user_id):
    """Every LR4 on the account (``LitterRobot4.fetch_for_account``)."""
    return {
        "query": "query GetLR4($userId: String!) { getLitterRobot4ByUser(userId: $userId) "
                 + MODEL + " }",
        "variables": {"userId": user_id},
    }


def robot_query(serial):
    """One LR4 by serial (``LitterRobot4.refresh``)."""
    return {
        "query": "query GetLR4($serial: String!) { getLitterRobot4BySerial(serial: $serial) "
                 + MODEL + " }",
        "variables": {"serial": serial},
    }


ACTIVITY_FIELDS = ("serial measure timestamp value actionValue originalHex valueString "
                   "stateString consumer commandSource")


def activity_query(serial, limit, start=None, end=None):
    """The history (``LitterRobot4.get_activity_history``). ``start`` and ``end`` are epoch
    seconds, sent as "YYYY-MM-DD HH:MM:SS" UTC, the start rounded up to a whole second.
    pylitterbot never sends ``endTimestamp``; we send it only when paging (see the note)."""
    variables = {"serial": serial, "limit": limit, "consumer": "app"}
    if start is not None:
        variables["startTimestamp"] = util.api_timestamp(start)
    if end is not None:
        variables["endTimestamp"] = util.api_timestamp(end)
    return {
        "query": "query GetLR4Activity($serial: String!, $startTimestamp: String, "
                 "$endTimestamp: String, $limit: Int, $consumer: String) { "
                 "getLitterRobot4Activity(serial: $serial, startTimestamp: $startTimestamp, "
                 "endTimestamp: $endTimestamp, limit: $limit, consumer: $consumer) { "
                 + ACTIVITY_FIELDS + " } }",
        "variables": variables,
    }


def ws_url(bearer, base=LR4_URL):
    """The realtime URL: ``<endpoint>/realtime?header=<b64 json>&payload=<b64 {}>``
    (``LitterRobot4._ws_config_factory``). The token is in it: never log it."""
    header = json.dumps({"Authorization": bearer, "host": LR4_HOST})
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return (base + "/realtime?header=" + util.b64encode(header)
            + "&payload=" + util.b64encode("{}"))


def start_message(sub_id, bearer, user_id):
    """The subscription ``start`` (``LitterRobot4._ws_subscribe``): the GraphQL is a JSON
    string inside ``payload.data``, the token again in ``payload.extensions``."""
    data = json.dumps({
        "query": "subscription GetLR4ByUser($userId: String!) { "
                 "litterRobot4StateSubscriptionByUser(userId: $userId) { robots " + MODEL
                 + " } }",
        "variables": {"userId": user_id},
    })
    return json.dumps({
        "id": sub_id,
        "payload": {
            "data": data,
            "extensions": {"authorization": {"Authorization": bearer, "host": LR4_HOST}},
        },
        "type": "start",
    })


def stop_message(sub_id):
    return json.dumps({"id": sub_id, "type": "stop"})


def robots_in_message(message):
    """The robot dicts in a ``data`` message (``parse_websocket_message``), else None."""
    if not isinstance(message, dict) or message.get("type") != "data":
        return None
    try:
        robots = message["payload"]["data"]["litterRobot4StateSubscriptionByUser"]["robots"]
    except (KeyError, TypeError):
        return None
    return robots if isinstance(robots, list) else None


# -- activity history ------------------------------------------------------------------------


class Action:
    """An activity's action: ``text`` as the history shows it (pylitterbot's
    ``LitterBoxStatus.text`` or its words), ``value`` the status code when it is a status (the
    ``code`` the feeder's activity log writes), ``name`` the status name."""

    def __init__(self, text, value=None, name=None):
        self.text = text
        self.value = value
        self.name = name

    def __str__(self):
        return self.text

    def __repr__(self):
        return "Action(" + repr(self.text) + ")"

    def __eq__(self, other):
        return isinstance(other, Action) and other.text == self.text \
            and other.value == self.value

    def __hash__(self):
        return hash(self.text)


def action_for(entry):
    """``LitterRobot4._parse_activity``."""
    value = entry["value"]
    name = ACTIVITY.get(value)
    if name is not None:
        code, text = STATUS[name]
        return Action(text, code, name)
    words = ACTIVITY_WORDS.get(value)
    if words is None:
        return Action(str(value))
    if value in ("catWeight",):
        return Action(words + ": " + util.value_text(entry["actionValue"]) + " lbs")
    if value in ("odometerCleanCycles", "litterHopperDispensed"):
        return Action(words + ": " + util.value_text(entry["actionValue"]))
    return Action(words)


class Activity:
    """One history entry: ``ts`` in epoch seconds UTC and an :class:`Action`. ``timestamp`` is
    an aware ``datetime`` on CPython (what ``arthur.sources.whisker`` reads) and the epoch on
    MicroPython, which has no ``datetime``."""

    def __init__(self, ts, action, iso=None):
        self.ts = ts
        self.action = action
        self.iso = iso or util.iso_utc(ts)

    @property
    def timestamp(self):
        if util.IS_MPY:
            return self.ts
        from datetime import datetime
        return datetime.fromisoformat(self.iso)

    def __repr__(self):
        return "Activity(" + self.iso + ", " + repr(self.action.text) + ")"


def parse_activities(entries):
    """Entries of ``getLitterRobot4Activity`` -> Activities, skipping entries with no
    timestamp as pylitterbot does (``if (timestamp := to_timestamp(...)) is not None``)."""
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        iso = util.timestamp_iso(entry.get("timestamp"))
        if iso is None:
            continue
        out.append(Activity(util.parse_iso(iso), action_for(entry), iso))
    return out
