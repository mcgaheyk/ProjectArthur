"""The record ``arthur.spikelog.build_record`` makes from a pylitterbot ``LitterRobot4``, made
here from the robot's raw data, so the tracker, the mapping and the ``logs/spike-*.jsonl``
format stay as they are.

Each property is derived the way pylitterbot 2025.8.0 derives it (``robot/litterrobot4.py``,
``robot/litterrobot.py``, ``enums.py``, ``utils.py``; cited per field in
``docs/reference/whisker-wire.md``), including the failures: a property that would raise in
pylitterbot is recorded as ``"<error: TypeName>"``, as ``build_record`` does.
"""

from arthur.wire import lr4, util

#: ``arthur.spikelog.TRACKED_PROPERTIES`` (a test checks the two stay equal).
TRACKED_PROPERTIES = (
    "status", "status_code", "status_text", "is_online", "is_on", "is_sleeping",
    "sleep_mode_enabled", "waste_drawer_level", "is_waste_drawer_full",
    "is_drawer_full_indicator_triggered", "litter_level", "litter_level_state", "pet_weight",
    "clean_cycle_wait_time_minutes", "is_laser_dirty", "power_type", "last_seen",
    "cycle_count", "cycle_capacity", "cycles_after_drawer_full", "globe_motor_fault_status",
    "hopper_status", "is_hopper_removed",
)

#: ``arthur.spikelog.TRACKED_RAW_KEYS``.
TRACKED_RAW_KEYS = (
    "robotStatus", "robotCycleState", "robotCycleStatus", "displayCode", "catDetect",
    "isCatDetectPending", "weightSensor", "pinchStatus", "isDFIFull", "isDFIPartialFull",
    "DFILevelPercent", "litterLevel", "litterLevelPercentage", "sleepStatus",
    "unitPowerStatus", "isBonnetRemoved", "wifiRssi", "globeMotorFaultStatus",
)

LITTER_LEVEL_STATES = ("OVERFILL", "OPTIMAL", "REFILL", "LOW", "EMPTY")
HOPPER_STATUSES = ("ENABLED", "DISABLED", "MOTOR_FAULT_SHORT", "MOTOR_OT_AMPS",
                   "MOTOR_DISCONNECTED", "EMPTY", "LITTER_LOW", "READY", "JAMMED", "OFFLINE")
GLOBE_MOTOR_FAULTS = ("NONE", "FAULT_CLEAR", "FAULT_TIMEOUT", "FAULT_DISCONNECT",
                      "FAULT_UNDERVOLTAGE", "FAULT_OVERTORQUE_AMP", "FAULT_OVERTORQUE_SLOPE",
                      "FAULT_PINCH", "FAULT_ALL_SENSORS", "FAULT_UNKNOWN")
DAYS = ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")


def to_jsonable(value):
    """``arthur.spikelog.to_jsonable`` for the values raw data can hold."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        out = {}
        for k in value:
            out[str(k)] = to_jsonable(value[k])
        return out
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return str(value)


def to_enum(value, members):
    """``pylitterbot.utils.to_enum`` for a string Enum whose names equal its values: the
    member name, matched exactly or ignoring case, else None."""
    if value is None:
        return None
    if isinstance(value, str):
        if value in members:
            return value
        upper = value.upper()
        for m in members:
            if m.upper() == upper:
                return m
    return None


def globe_motor_fault(raw):
    """``GlobeMotorFaultStatus.from_raw``."""
    if raw is None:
        return "NONE"
    value = raw.strip()  # AttributeError for a non-string, as in pylitterbot
    if value == "":
        return "NONE"
    if value in GLOBE_MOTOR_FAULTS:
        return value
    out = ""
    for i in range(len(value)):  # re.sub(r"(?<!^)(?=[A-Z])", "_", value)
        ch = value[i]
        if i > 0 and "A" <= ch <= "Z":
            out += "_"
        out += ch
    value = out.upper().replace("MTR_", "").replace("MOTOR_", "")
    if not value.startswith("FAULT_") and value != "NONE":
        value = "FAULT_" + value
    return value if value in GLOBE_MOTOR_FAULTS else "FAULT_UNKNOWN"


def status_name(data):
    """``LitterRobot4.status``."""
    if data.get("isOnline") is not True:
        return "OFFLINE"
    if data.get("unitPowerStatus", "") != "ON":
        return "OFF"
    display_code = data.get("displayCode", "")
    fault = lr4.FAULT_DISPLAY_CODE.get(display_code)
    if fault:
        return fault
    cycle = lr4.CYCLE_STATE.get(data["robotCycleState"])
    if cycle:
        return cycle
    status = lr4.ROBOT_STATUS.get(data["robotStatus"], "UNKNOWN")
    if status == "READY":
        shown = lr4.DISPLAY_CODE.get(display_code)
        if shown:
            return shown
        if data.get("isDFIFull") is True:
            return "DRAWER_FULL"
    return status


def sleep_mode_enabled(data):
    """``LitterRobot.sleep_mode_enabled`` through ``SleepSchedule.parse``: any day on."""
    raw = data.get("weekdaySleepModeEnabled") or {}
    if isinstance(raw, dict):
        enabled = False
        for name in raw:
            day = raw[name]
            if name.upper() not in DAYS:
                raise KeyError(name)
            day["sleepTime"], day["wakeTime"]  # parse() reads them: they must exist
            if day["isEnabled"]:
                enabled = True
        return enabled
    if isinstance(raw, list):
        enabled = False
        for day in raw:
            day["dayOfWeek"], day["sleepTime"], day["wakeTime"]  # likewise
            if day["isEnabled"]:
                enabled = True
        return enabled
    raise TypeError("Unsupported schedule format")


def _status(data):
    return status_name(data)


def _prop(name, data):
    if name == "status":
        return _status(data)
    if name == "status_code":
        s = _status(data)
        return lr4.STATUS[s][0] if s != "UNKNOWN" else data.get("robotStatus")
    if name == "status_text":
        return lr4.STATUS[_status(data)][1]
    if name == "is_online":
        return data.get("isOnline") is True
    if name == "is_on":
        return data.get("unitPowerStatus", "") == "ON"
    if name == "is_sleeping":
        return data.get("sleepStatus", "WAKE") != "WAKE"
    if name == "sleep_mode_enabled":
        return sleep_mode_enabled(data)
    if name == "waste_drawer_level":
        return data.get("DFILevelPercent", 0)
    if name in ("is_waste_drawer_full", "is_drawer_full_indicator_triggered"):
        return data.get("isDFIFull") is True
    if name == "litter_level":
        return data.get("litterLevelPercentage", 0) * 100
    if name == "litter_level_state":
        return to_enum(data.get("litterLevelState"), LITTER_LEVEL_STATES)
    if name == "pet_weight":
        return data.get("catWeight", 0)
    if name == "clean_cycle_wait_time_minutes":
        return data.get("cleanCycleWaitTime", 7)
    if name == "is_laser_dirty":
        return data.get("isLaserDirty") is True
    if name == "power_type":
        return data.get("unitPowerType", "NC")
    if name == "last_seen":
        return util.timestamp_iso(data.get("lastSeen"))
    if name == "cycle_count":
        return int(data.get("odometerCleanCycles", 0))
    if name == "cycle_capacity":
        return int(data.get("DFINumberOfCycles", 30))
    if name == "cycles_after_drawer_full":
        return int(data.get("DFIFullCounter", 0))
    if name == "globe_motor_fault_status":
        return globe_motor_fault(data.get("globeMotorFaultStatus"))
    if name == "hopper_status":
        return to_enum(data.get("hopperStatus"), HOPPER_STATUSES)
    if name == "is_hopper_removed":
        return data.get("isHopperRemoved") is True
    raise AttributeError(name)


def build_record(data):
    """The flat record: tracked properties, then the tracked raw keys."""
    record = {}
    for name in TRACKED_PROPERTIES:
        try:
            record[name] = to_jsonable(_prop(name, data))
        except Exception as exc:  # noqa: BLE001 - one broken property must not lose the record
            record[name] = "<error: " + type(exc).__name__ + ">"
    for key in TRACKED_RAW_KEYS:
        record[key] = to_jsonable(data.get(key))
    return record
