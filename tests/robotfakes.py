"""Fakes that look like pylitterbot's account and LitterRobot4, for tests. No network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pylitterbot.enums import LitterBoxStatus

T0 = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)


def raw_data(**overrides):
    data = {
        "unitId": "UNIT-123",
        "serial": "LR4C000001",
        "userId": "USER-9",
        "name": "Arthur's box",
        "sessionId": "abc",
        "robotStatus": "ROBOT_IDLE",
        "robotCycleState": "CYCLE_STATE_NONE",
        "robotCycleStatus": "CYCLE_IDLE",
        "displayCode": "DC_MODE_IDLE",
        "catDetect": "CAT_DETECT_CLEAR",
        "isCatDetectPending": False,
        "weightSensor": 0.0,
        "pinchStatus": "CLEAR",
        "isDFIFull": False,
        "isDFIPartialFull": False,
        "DFILevelPercent": 42,
        "litterLevel": 451,
        "litterLevelPercentage": 0.8,
        "sleepStatus": "WAKE",
        "unitPowerStatus": "ON",
        "isBonnetRemoved": False,
        "wifiRssi": -55,
        "globeMotorFaultStatus": "FAULT_CLEAR",
        "weekdaySleepModeEnabled": {"Monday": {"isEnabled": False, "sleepTime": 0}},
        "ownerEmail": "kyle@example.com",
        "lanIp": "192.168.1.20",
    }
    data.update(overrides)
    return data


class FakeRobot:
    """Looks like pylitterbot's LitterRobot4 for the properties the logger reads."""

    def __init__(self, **raw) -> None:
        self._data = raw_data(**raw)
        self.status = LitterBoxStatus.READY
        self.status_code = "RDY"
        self.status_text = "Ready"
        self.is_online = True
        self.is_on = True
        self.is_sleeping = False
        self.sleep_mode_enabled = False
        self.waste_drawer_level = 42.0
        self.is_waste_drawer_full = False
        self.is_drawer_full_indicator_triggered = False
        self.litter_level = 80.0
        self.litter_level_state = None
        self.pet_weight = 9.5
        self.clean_cycle_wait_time_minutes = 7
        self.is_laser_dirty = False
        self.power_type = "AC"
        self.last_seen = T0
        self.cycle_count = 1234
        self.cycle_capacity = 30
        self.cycles_after_drawer_full = 0
        self.globe_motor_fault_status = None
        self.hopper_status = None
        self.is_hopper_removed = False
        self.name = "Arthur's box"
        self.serial = "LR4C000001"

    def to_dict(self):
        return self._data

    def set_status(self, status: LitterBoxStatus, robot_status: str) -> None:
        self.status = status
        self.status_code = status.value
        self.status_text = status.text
        self._data["robotStatus"] = robot_status


@dataclass
class Act:
    timestamp: datetime
    action: object


class FakeAccount:
    def __init__(self, robot) -> None:
        self.robot = robot
        self.pets = []
        self.disconnected = False

    def get_robots(self, cls):
        return [self.robot]

    async def disconnect(self):
        self.disconnected = True


class LiveFakeRobot(FakeRobot):
    def __init__(self) -> None:
        super().__init__()
        self.listeners = []
        self.refreshes = 0
        self.activities = [Act(T0, LitterBoxStatus.CLEAN_CYCLE_COMPLETE)]
        self.activity_calls = []  # (limit, start) of each get_activity_history call

    def on(self, event, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def push(self, status, robot_status):
        self.set_status(status, robot_status)
        for cb in list(self.listeners):
            cb()

    async def refresh(self):
        self.refreshes += 1
        for cb in list(self.listeners):
            cb()

    async def get_activity_history(self, limit=100, start=None):
        self.activity_calls.append((limit, start))
        return [a for a in self.activities if start is None or a.timestamp >= start]
