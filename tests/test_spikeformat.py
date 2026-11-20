"""The log format's MicroPython stand-ins answer as CPython's own code does.

On CPython :mod:`arthur.sources.spikeformat` uses regular expressions and ``json``, unchanged
from :mod:`arthur.spikelog`; on MicroPython (no ``re`` features these need) the ``_portable``
versions run instead. Here both are run on CPython over the real logs and many made-up
strings, and must agree. ``tests/test_wire_source_micropython.py`` runs the portable ones
under MicroPython itself.
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from wirefake import robot_raw

from arthur.sources import spikeformat as sf

DATA = Path(__file__).parent / "data"

ALPHABET = "ab@.:-_ \t09fFeyJxX=&'\"Bearer header payload 1.2.3.4 é"


def _strings(n=4000, seed=5):
    rng = random.Random(seed)
    pieces = ["header=", "payload=", "Bearer ", "Bearer\t", "eyJ", "eyJab.cd.", "eyJa.b",
              "@", "a@b.cd", "x@y.z", "@@", "1.2.3.4", "999.1.1.1", "1234.5.6.7", "1.2.3.4.5",
              "aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee", "gaa:bb:cc:dd:ee:ff",
              "aa:bb:cc:dd:ee:ff0", ".", " ", "&", "'", '"', "_", "Z", "e9", "é", "\n"]
    out = []
    for _ in range(n):
        k = rng.randrange(1, 8)
        s = ""
        for _ in range(k):
            s += rng.choice(pieces) if rng.random() < 0.6 else rng.choice(ALPHABET)
        out.append(s)
    return out


FIXED = [
    "", "plain", "kyle@example.com", "x a@b.co y", "a@b.c", "@b.com", "a@.com", "a@b.c0m",
    "192.168.86.41", "v192.168.86.41", "192.168.86.41x", "192.168.86.41.", "1.2.3",
    "10:00:00:00:00:00", "de:ad:be:ef:00:01 ok", "wss://x/realtime?header=eyJabc&payload=e30=",
    "Bearer abc.def-ghi", "Bearer", "Bearer  ", "header=", "header=&", "payload='x'",
    "token eyJhbGciOi.eyJzdWIi.sig and eyJx.y.", "eyJ.a.b", "eyJa..b", "headerheader=x",
    "payload=header=abc", "header=payload=abc", "x header=a&payload=b y",
]


@pytest.mark.parametrize("seed", [5, 6])
def test_scrub_portable_equals_the_regular_expressions(seed):
    for s in FIXED + _strings(seed=seed):
        assert sf._scrub_portable(s) == sf.scrub(s), repr(s)


@pytest.mark.parametrize("seed", [7, 8])
def test_value_redaction_portable_equals_the_regular_expression(seed):
    for s in FIXED + _strings(seed=seed):
        mine = sf._has_email(s) or sf._has_ipv4(s) or sf._has_mac(s)
        assert mine == bool(sf._REDACT_VALUE.search(s)), repr(s)


def test_key_redaction_portable_equals_the_regular_expression():
    keys = list(robot_raw()) + ["id", "ID", "Id", "unit_id", "macAddress", "MAC", "mac",
                                "ipAddress", "IP", "userId", "username", "Owner", "phone",
                                "wifiSsid", "accountNumber", "sessionId", "catDetect",
                                "wifiRssi", "videoid", "amac", "macx", "xmacaddress", "x"]
    for k in keys:
        assert sf._redact_key(k) == bool(sf._REDACT_KEY.search(k)), k


def _real_raws():
    out = []
    for path in sorted(DATA.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            obj = json.loads(line)
            if isinstance(obj, dict) and isinstance(obj.get("raw"), dict):
                out.append(obj["raw"])
    return out


def test_redact_portable_equals_redact_on_raw_data():
    raws = [*_real_raws(), robot_raw(), robot_raw(name="kyle@example.com", wifiRssi=-50.5),
            {"nested": {"serial": "X", "list": ["10.0.0.1", 3, None, 1.5]}, "": "",
             "token": None, "email": ""}]
    assert raws
    for raw in raws:
        assert sf._redact_portable(raw) == sf.redact(raw)


VALUES = [None, True, False, 0, -41, 3478, 0.0, -41.0, 0.07, 4.67, 82.0, 1e-07, 1e22, 1.5e300,
          "", "a\"b\\c\n\t\r\b\f\x01\x1f\x7f é ✓", [1, [2.5, None]], (3, 4),
          {"b": 1, "a": {"x": [0.1, 0.2]}}, {1: "int key", 2.5: "float key"},
          datetime(2026, 11, 20, tzinfo=UTC)]


def test_dumps_portable_equals_json_dumps():
    for v in VALUES:
        assert sf._dumps_portable(v) == sf.dumps(v), v
        assert sf._dumps_portable(v, sort_keys=True) == sf.dumps(v, sort_keys=True), v
    for v in (math.nan, math.inf, -math.inf):
        assert sf._dumps_portable(v) == json.dumps(v)
    for raw in _real_raws()[:20]:
        assert sf._dumps_portable(raw) == sf.dumps(raw)


def test_utc_iso_ms_portable():
    for dt in (datetime(2026, 11, 20, 1, 2, 3, 456789, tzinfo=UTC),
               datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5))),
               datetime(2026, 11, 20, 23, 59, 59, 999999, tzinfo=UTC)):
        assert sf._utc_iso_ms_portable(dt) == sf.utc_iso_ms(dt)


def test_round3_portable_on_milliseconds():
    for ms in range(0, 3_000_000, 997):
        x = ms / 1000
        assert sf._round3_portable(x) == round(x, 3)


def test_memory_sink_keeps_the_newest():
    sink = sf.MemorySink(limit=3)
    for i in range(5):
        sink.state({"i": i, "f": 0.07})
    assert [json.loads(x)["i"] for x in sink.states] == [2, 3, 4]
    assert sink.states[-1] == '{"i": 4, "f": 0.07}'
