"""Tests for the PETLIBRO spike. No real network: a fake PETLIBRO server runs on localhost."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

from arthur import petlibro_spike as ps
from arthur.spikelog import EXIT_AUTH, EXIT_MISSING_ENV, REDACTED

EMAIL = "kyle.secret@example.com"
PASSWORD = "Hunter2-Sekrit!"
PW_HASH = hashlib.md5(PASSWORD.encode()).hexdigest()
TZ = "America/Chicago"

GRANARY_SN = "AF1030XQZ77SN"
FOUNTAIN_SN = "WF1050KPL42SN"
GRANARY_MAC = "AA:BB:CC:11:22:33"
FOUNTAIN_MAC = "AA:BB:CC:44:55:66"
SSID = "ArthursHomeWiFi"
GRANARY_NAME = "Arthur Food Tower"
FOUNTAIN_NAME = "Arthur Water Hole"
SHARE_ID = "share-77ab9q"
TOKEN_PREFIX = "tok-ZXq9secret-"


def granary_listing() -> dict:
    return {
        "deviceSn": GRANARY_SN,
        "productName": "Granary Smart Feeder",
        "productIdentifier": "PLAF103",
        "name": GRANARY_NAME,
        "mac": GRANARY_MAC,
        "softwareVersion": "1.2.7",
        "hardwareVersion": "1.0.0",
        "online": True,
        "deviceShareState": 2,
        "shareId": SHARE_ID,
    }


def fountain_listing() -> dict:
    return {
        "deviceSn": FOUNTAIN_SN,
        "productName": "Dockstream Smart Fountain",
        "productIdentifier": "PLWF105",
        "name": FOUNTAIN_NAME,
        "mac": FOUNTAIN_MAC,
        "softwareVersion": "2.0.1",
        "hardwareVersion": "1.1.0",
        "online": True,
        "deviceShareState": 2,
        "shareId": SHARE_ID,
    }


class FakePetlibro:
    """A fake PETLIBRO API. Tokens are tok-...-1, -2... and only the newest is valid."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict, dict]] = []  # path, headers, body
        self.logins = 0
        self.login_code = 0
        self.login_msg = "bad"
        self.valid: set[str] = set()
        self.devices = [granary_listing(), fountain_listing()]
        self.hooks: dict[str, object] = {}  # path -> fn(sn, server) -> response or None
        self.surplus = True
        self.weight_percent = 62
        self.rssi = -50
        self.online = True

    @property
    def token(self) -> str:
        return f"{TOKEN_PREFIX}{self.logins}"

    def ok(self, data) -> web.Response:
        return web.json_response({"code": 0, "msg": None, "data": data})

    async def handle(self, request: web.Request) -> web.Response:
        path = request.path
        body = await request.json()
        self.requests.append((path, dict(request.headers), body))
        if path == ps.LOGIN_PATH:
            self.logins += 1
            if self.login_code:
                return web.json_response(
                    {"code": self.login_code, "msg": self.login_msg, "data": None}
                )
            self.valid = {self.token}
            return self.ok({"token": self.token, "memberId": 12345})
        if request.headers.get("token") not in self.valid:
            return web.json_response({"code": 1009, "msg": "NOT_YET_LOGIN", "data": None})
        sn = body.get("deviceSn")
        hook = self.hooks.get(path)
        if hook is not None:
            result = hook(sn, self)
            if result is not None:
                return result
        return self.ok(self.data(path, sn))

    def data(self, path: str, sn: str | None):
        if path == ps.LIST_PATH:
            return self.devices
        if path == ps.REAL_INFO_PATH:
            common = {
                "deviceSn": sn, "online": self.online, "wifiRssi": self.rssi, "mac": GRANARY_MAC
            }
            if sn == GRANARY_SN:
                return {
                    **common,
                    "surplusGrain": self.surplus,
                    "grainOutletState": True,
                    "runningState": "IDLE",
                    "batteryState": "normal",
                }
            return {
                **common,
                "weight": 800,
                "weightPercent": self.weight_percent,
                "weightState": "NORMAL",
                "waterBoxStatus": 1,
            }
        if path == ps.DATA_REAL_INFO_PATH:
            return {"lowWater": False, "filterState": "NORMAL", "exceptionMessage": None}
        if path == ps.GRAIN_STATUS_PATH:
            return {
                "todayFeedingTimes": 2,
                "todayFeedingQuantity": 4,
                "todayFeedingQuantities": [2, 2],
                "surplusGrain": self.surplus,
            }
        if path == ps.DRINK_DATA_PATH:
            return {"todayTotalMl": 120, "todayTotalTimes": 6, "avgDrinkDuration": 9.5}
        if path == ps.BASE_INFO_PATH:
            return {
                "deviceSn": sn,
                "name": next(d.get("name") for d in self.devices if d["deviceSn"] == sn),
                "mac": GRANARY_MAC,
                "wifiSsid": SSID,
                "productName": "Granary Smart Feeder",
                "remainingDesiccantDays": 20,
                "ownerEmail": EMAIL,
                "pictureUrl": "https://example.invalid/pic.png",
            }
        if path == ps.WORK_RECORD_PATH:
            return [
                {
                    "day": "2026-11-16",
                    "workRecords": [
                        {"type": "GRAIN_OUTPUT_SUCCESS", "recordTime": 1, "actualGrainNum": 2}
                    ],
                }
            ]
        return None

    def calls(self, path: str) -> list[tuple[str, dict, dict]]:
        return [r for r in self.requests if r[0] == path]


@pytest.fixture
async def fake(aiohttp_server):
    cloud = FakePetlibro()
    app = web.Application()
    app.router.add_post("/{tail:.*}", cloud.handle)
    server = await aiohttp_server(app)
    cloud.url = f"http://{server.host}:{server.port}"
    return cloud


def make_client(fake: FakePetlibro, password: str = PASSWORD) -> ps.PetlibroClient:
    return ps.PetlibroClient(EMAIL, password, TZ, base_url=fake.url)


def make_spike(fake, tmp_path, out=None, err=None, **kw):
    client = make_client(fake)
    spike = ps.PetlibroSpike(
        client, tmp_path, TZ, out=out or io.StringIO(), err=err or io.StringIO(),
        request_gap=0, **kw,
    )
    return client, spike


def log_lines(log_dir: Path) -> list[dict]:
    lines = []
    for path in sorted(Path(log_dir).glob("petlibro-*.jsonl")):
        lines += [json.loads(x) for x in path.read_text().splitlines()]
    return lines


def opts(tmp_path, duration, poll=0.05):
    return SimpleNamespace(duration=duration, poll_seconds=poll, log_dir=tmp_path)


# --------------------------------------------------------------------------------------------
# Pure parts
# --------------------------------------------------------------------------------------------


def test_md5_and_login_body():
    assert ps.md5_hex("abc") == "900150983cd24fb0d6963f7d28e17f72"
    body = ps.login_body("a@b.c", ps.md5_hex("abc"), TZ)
    assert body == {
        "appId": 1,
        "appSn": "c35772530d1041699c87fe62348507a8",
        "country": "US",
        "email": "a@b.c",
        "password": "900150983cd24fb0d6963f7d28e17f72",
        "phoneBrand": "",
        "phoneSystemVersion": "",
        "timezone": TZ,
        "thirdId": None,
        "type": None,
    }


def test_endpoint_selection_by_product():
    assert ps.endpoints_for("PLAF103") == (
        ps.REAL_INFO_PATH, ps.DATA_REAL_INFO_PATH, ps.GRAIN_STATUS_PATH
    )
    assert ps.endpoints_for("PLWF105") == (
        ps.REAL_INFO_PATH, ps.DATA_REAL_INFO_PATH, ps.DRINK_DATA_PATH
    )
    assert ps.endpoints_for("PLXX999") == (ps.REAL_INFO_PATH, ps.DATA_REAL_INFO_PATH)
    assert ps.slow_endpoints_for("PLAF103") == (ps.BASE_INFO_PATH, ps.WORK_RECORD_PATH)
    assert ps.slow_endpoints_for("PLWF106") == (ps.BASE_INFO_PATH,)
    assert ps.slow_endpoints_for("PLXX999") == (ps.BASE_INFO_PATH,)
    every = set(ps.endpoints_for("PLAF1")) | set(ps.endpoints_for("PLWF1"))
    assert every | set(ps.slow_endpoints_for("PLAF1")) | {ps.LIST_PATH} == ps.READ_PATHS


def test_labels_count_per_product_and_stay_stable():
    devices = [
        {"deviceSn": "a", "productIdentifier": "PLAF103"},
        {"deviceSn": "b", "productIdentifier": "PLWF105"},
        {"deviceSn": "c", "productIdentifier": "PLAF103"},
    ]
    labels = ps.assign_labels(devices)
    assert labels == {"a": "PLAF103#1", "b": "PLWF105#1", "c": "PLAF103#2"}
    later = ps.assign_labels([{"deviceSn": "d", "productIdentifier": "PLWF105"}, *devices], labels)
    assert later["d"] == "PLWF105#2" and later["a"] == "PLAF103#1"


def test_flatten_and_diff():
    flat = ps.flatten({"a": 1, "b": {"c": [1, {"d": None}]}, "e": {}, "f": []}, "realInfo")
    assert flat == {
        "realInfo.a": 1,
        "realInfo.b.c[0]": 1,
        "realInfo.b.c[1].d": None,
        "realInfo.e": {},
        "realInfo.f": [],
    }
    old = ps.flat_record({"realInfo": {"weightPercent": 62, "x": 1}})["realInfo"]
    new = ps.flat_record({"realInfo": {"weightPercent": 58, "x": 1}})["realInfo"]
    from arthur.spikelog import diff_records

    assert diff_records(old, new) == {"realInfo.weightPercent": [62, 58]}


def test_field_inventory_collapses_indices_and_collects_types():
    inv: dict = {}
    ps.add_field_types(inv, "grainStatus", {"q": [1, 2], "s": True, "m": None})
    ps.add_field_types(inv, "grainStatus", {"q": [], "s": True, "m": "x", "f": 1.5})
    assert ps.inventory_json(inv) == {
        "grainStatus": {
            "f": ["number"],
            "m": ["null", "string"],
            "q": ["array"],
            "q[]": ["integer"],
            "s": ["boolean"],
        }
    }


def test_key_extraction_prefers_realinfo_and_skips_missing():
    key = ps.extract_key(
        {
            "realInfo": {"surplusGrain": False, "batteryState": "low", "other": 1},
            "grainStatus": {"surplusGrain": True, "todayFeedingTimes": 3},
            "drinkData": None,
        }
    )
    assert key == {"surplusGrain": False, "batteryState": "low", "todayFeedingTimes": 3}


def test_petlibro_redaction():
    data = {
        "deviceSn": "X1",
        "sn": "X2",
        "mac": "m",
        "name": "Arthur",
        "productName": "Granary Smart Feeder",
        "productIdentifier": "PLAF103",
        "jobName": "breakfast",
        "templateName": "t",
        "wifiSsid": "home",
        "shareId": 5,
        "memberId": 6,
        "authCode": "a",
        "rfidTag": "r",
        "avatarUrl": "u",
        "nickName": "n",
        "address": "street",
        "longitude": 1.0,
        "latitude": 2.0,
        "note": "write to kyle@example.com",
        "weightPercent": 50,
        "empty": "",
    }
    out = ps.redact_petlibro(data)
    kept = {"productName", "productIdentifier", "jobName", "templateName", "weightPercent", "empty"}
    for key, value in out.items():
        if key in kept:
            assert value == data[key], key
        else:
            assert value == REDACTED, key


def test_scrubber_replaces_secrets_anywhere():
    s = ps.Scrubber([EMAIL, "AA:BB:CC:11:22:33", "abc", None])
    text = s.text(f"x {EMAIL.upper()} y aabbcc112233 z AA:BB:CC:11:22:33 abc")
    assert EMAIL.lower() not in text.lower() and "aabbcc112233" not in text
    assert "AA:BB" not in text and text.endswith("abc")  # too short to scrub
    assert s.obj({EMAIL: [EMAIL]}) == {REDACTED: [REDACTED]}


def test_timezone_resolution_order(tmp_path):
    link = tmp_path / "localtime"
    link.symlink_to("/var/db/timezone/zoneinfo/Europe/Paris")
    missing = tmp_path / "nothing"
    env = {"PETLIBRO_TIMEZONE": "America/Denver", "TZ": "Asia/Tokyo"}
    assert ps.resolve_timezone(env, link) == "America/Denver"
    assert ps.resolve_timezone({"TZ": "Asia/Tokyo"}, link) == "Asia/Tokyo"
    assert ps.resolve_timezone({"TZ": ":Asia/Tokyo"}, link) == "Asia/Tokyo"
    assert ps.resolve_timezone({"TZ": "EST5EDT"}, link) == "Europe/Paris"  # not Area/City
    assert ps.resolve_timezone({"PETLIBRO_TIMEZONE": ""}, link) == "Europe/Paris"
    with pytest.raises(ps.TimezoneError):
        ps.resolve_timezone({}, missing)
    with pytest.raises(ps.TimezoneError):
        ps.resolve_timezone({"PETLIBRO_TIMEZONE": "Mars/Olympus"}, link)
    plain = tmp_path / "plainfile"
    plain.write_text("TZif")
    with pytest.raises(ps.TimezoneError):
        ps.resolve_timezone({"TZ": "UTC"}, plain)


def test_main_without_credentials_exits_2_and_never_runs(monkeypatch, capsys):
    async def boom(*a, **k):
        raise AssertionError("must not run")

    monkeypatch.setattr(ps, "run", boom)
    assert ps.main([], environ={"PETLIBRO_EMAIL": EMAIL}) == EXIT_MISSING_ENV
    err = capsys.readouterr().err
    assert "PETLIBRO_PASSWORD" in err and "SECOND" in err and "phone app" in err


def test_main_without_timezone_exits_2(monkeypatch, capsys):
    async def boom(*a, **k):
        raise AssertionError("must not run")

    monkeypatch.setattr(ps, "run", boom)
    monkeypatch.setattr(ps, "resolve_timezone", lambda env: (_ for _ in ()).throw(
        ps.TimezoneError("Could not detect the local timezone.")))
    env = {"PETLIBRO_EMAIL": EMAIL, "PETLIBRO_PASSWORD": PASSWORD}
    assert ps.main([], environ=env) == EXIT_MISSING_ENV
    assert "PETLIBRO_TIMEZONE" in capsys.readouterr().err


def test_poll_clamp_and_no_password_in_output(monkeypatch, capsys):
    seen = {}

    async def fake_run(opts, email, password, timezone):
        seen.update(poll=opts.poll_seconds, tz=timezone)
        return 0

    monkeypatch.setattr(ps, "run", fake_run)
    env = {"PETLIBRO_EMAIL": EMAIL, "PETLIBRO_PASSWORD": "x" * 19, "PETLIBRO_TIMEZONE": TZ}
    assert ps.main(["--poll-seconds", "5"], environ=env) == 0
    assert seen == {"poll": 60.0, "tz": TZ}
    err = capsys.readouterr().err
    assert "minimum of 60" in err
    # A long password is not warned about: PETLIBRO accepted one longer than 18 characters.
    assert "password" not in err.lower() and "x" * 19 not in err


def test_help_has_an_example(capsys):
    with pytest.raises(SystemExit):
        ps.main(["--help"], environ={})
    assert "example:" in capsys.readouterr().out


# --------------------------------------------------------------------------------------------
# Client against the fake server
# --------------------------------------------------------------------------------------------


async def test_login_request_and_token_header(fake):
    client = make_client(fake)
    try:
        await client.login()
        await client.call(ps.LIST_PATH, {})
    finally:
        await client.close()
    path, headers, body = fake.requests[0]
    assert path == ps.LOGIN_PATH
    assert body == ps.login_body(EMAIL, PW_HASH, TZ)
    assert body["password"] == PW_HASH
    for name, value in {"source": "ANDROID", "language": "EN", "timezone": TZ,
                        "version": "1.3.45"}.items():
        assert headers[name] == value
    assert headers["Content-Type"].startswith("application/json")
    assert "token" not in {k.lower() for k in headers}
    _, headers2, body2 = fake.requests[1]
    assert headers2["token"] == f"{TOKEN_PREFIX}1" and body2 == {}


async def test_path_allowlist_refuses_before_any_request(fake):
    client = make_client(fake)
    try:
        for path in ("/device/device/manualFeeding", "/member/auth/logout", "/device/x"):
            with pytest.raises(ps.ForbiddenPath):
                await client.call(path, {})
            with pytest.raises(ps.ForbiddenPath):
                await client._post(path, {})
    finally:
        await client.close()
    assert fake.requests == []
    assert client._session is None


@pytest.mark.parametrize(
    ("code", "hint"),
    [(1102, "longer than 18"), (1101, "phone app"), (4321, "code 4321")],
)
async def test_login_rejected_exits_3_with_hint_and_one_request(fake, tmp_path, capsys, code,
                                                                hint):
    fake.login_code = code
    out = io.StringIO()
    rc = await ps.run(opts(tmp_path, 2), EMAIL, PASSWORD, TZ, base_url=fake.url, out=out,
                      request_gap=0)
    assert rc == EXIT_AUTH
    assert fake.logins == 1 and len(fake.requests) == 1
    err = capsys.readouterr().err
    assert hint in err and "Not retrying" in err
    errors = [x for x in log_lines(tmp_path) if x["kind"] == "error"]
    assert errors[0]["where"] == "login" and errors[0]["error"] == "PetlibroAuthFailed"


async def test_1009_logs_in_again_once_and_retries(fake, tmp_path):
    client, spike = make_spike(fake, tmp_path)
    try:
        await client.login()
        await spike.poll_cycle()
        fake.valid = set()  # the token expires
        errors = await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    assert errors == 0 and fake.logins == 2
    assert any(x.get("event") == "relogin" for x in log_lines(tmp_path))
    last = fake.requests[-1]
    assert last[1]["token"] == f"{TOKEN_PREFIX}2"


async def test_repeated_1009_does_not_start_a_login_storm(fake, tmp_path):
    client, spike = make_spike(fake, tmp_path)

    def always_1009(sn, server):
        return web.json_response({"code": 1009, "msg": "NOT_YET_LOGIN", "data": None})

    fake.hooks[ps.LIST_PATH] = always_1009
    try:
        await client.login()
        for _ in range(3):
            with pytest.raises(ps.SessionReplaced):
                await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    assert fake.logins == 1 + 3  # the first login plus at most one per cycle


async def test_session_replaced_is_logged_and_backed_off(fake, tmp_path, capsys):
    def always_1009(sn, server):
        return web.json_response({"code": 1009, "msg": "NOT_YET_LOGIN", "data": None})

    fake.hooks[ps.REAL_INFO_PATH] = always_1009
    rc = await ps.run(opts(tmp_path, 0.5, poll=0.1), EMAIL, PASSWORD, TZ, base_url=fake.url,
                      out=io.StringIO(), request_gap=0)
    assert rc == 0
    errors = [x for x in log_lines(tmp_path) if x["kind"] == "error"]
    assert errors and all(e["where"] == "session" for e in errors)
    assert "session was replaced" in errors[0]["message"]
    # Backoff doubles from the poll interval: 0.1, 0.2, 0.4 ... so few cycles fit in 0.5 s.
    assert [e["retry_in"] for e in errors[:2]] == [0.1, 0.2]
    assert fake.logins <= 1 + len(errors)
    assert "session was replaced" in capsys.readouterr().err


async def test_one_device_failing_does_not_stop_the_other(fake, tmp_path):
    def granary_500(sn, server):
        if sn == GRANARY_SN:
            return web.Response(status=502, text="bad gateway")
        return None

    def bad_json(sn, server):
        return web.Response(text="<html>oops", content_type="text/html")

    fake.hooks[ps.REAL_INFO_PATH] = granary_500
    fake.hooks[ps.DRINK_DATA_PATH] = bad_json
    client, spike = make_spike(fake, tmp_path)
    try:
        await client.login()
        errors = await spike.poll_cycle()
        spike.flush_errors(300)
    finally:
        await client.close()
        spike.close()
    assert errors == 2
    lines = log_lines(tmp_path)
    errs = [x for x in lines if x["kind"] == "error"]
    assert {(e["device"], e["where"], e["error"]) for e in errs} == {
        ("PLAF103#1", "realInfo", "HttpError"),
        ("PLWF105#1", "drinkData", "BadResponse"),
    }
    assert all(e["retry_in"] == 300 for e in errs)
    states = {x["device"]: x for x in lines if x["kind"] == "state"}
    assert set(states) == {"PLAF103#1", "PLWF105#1"}
    assert states["PLAF103#1"]["failed"] == ["realInfo"]
    assert states["PLAF103#1"]["key"]["surplusGrain"] is True  # from grainStatus
    assert states["PLWF105#1"]["key"]["weightPercent"] == 62
    assert "failed" in states["PLWF105#1"]


async def test_endpoints_called_per_device_kind(fake, tmp_path):
    fake.devices.append(
        {"deviceSn": "ODDSN0001", "productIdentifier": "PLXX999", "productName": "Mystery"}
    )
    client, spike = make_spike(fake, tmp_path)
    try:
        await client.login()
        await spike.poll_cycle()
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    by_sn: dict[str, list[str]] = {}
    for path, _, body in fake.requests:
        if "deviceSn" in body:
            by_sn.setdefault(body["deviceSn"], []).append(path)
    poll_g = list(ps.endpoints_for("PLAF103"))
    slow_g = [ps.BASE_INFO_PATH, ps.WORK_RECORD_PATH]
    assert by_sn[GRANARY_SN] == poll_g + slow_g + poll_g
    assert by_sn[FOUNTAIN_SN] == list(ps.endpoints_for("PLWF105")) + [ps.BASE_INFO_PATH] + list(
        ps.endpoints_for("PLWF105")
    )
    assert by_sn["ODDSN0001"] == [ps.REAL_INFO_PATH, ps.DATA_REAL_INFO_PATH, ps.BASE_INFO_PATH,
                                  ps.REAL_INFO_PATH, ps.DATA_REAL_INFO_PATH]
    work = next(b for p, _, b in fake.requests if p == ps.WORK_RECORD_PATH)
    assert work["size"] == 25 and work["type"] == ["GRAIN_OUTPUT_SUCCESS"]
    assert work["endTime"] - work["startTime"] == 7 * 24 * 3600 * 1000
    assert len(fake.calls(ps.LIST_PATH)) == 1  # re-listed hourly, not every poll


async def test_raw_on_first_poll_and_on_change_only(fake, tmp_path):
    out = io.StringIO()
    client, spike = make_spike(fake, tmp_path, out=out)
    try:
        await client.login()
        await spike.poll_cycle()
        await spike.poll_cycle()  # nothing changes
        fake.weight_percent = 58
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    states = [x for x in log_lines(tmp_path) if x["kind"] == "state"]
    fountain = [x for x in states if x["device"] == "PLWF105#1"]
    assert len(fountain) == 3
    assert "raw" in fountain[0] and fountain[0]["changed"] == {}
    assert "raw" not in fountain[1] and fountain[1]["changed"] == {}
    assert fountain[2]["changed"] == {"realInfo.weightPercent": [62, 58]}
    assert fountain[2]["raw"]["realInfo"]["weightPercent"] == 58
    assert fountain[2]["key"]["weightPercent"] == 58
    assert fountain[0]["raw"]["realInfo"]["deviceSn"] == REDACTED
    assert fountain[0]["online"] is True and fountain[0]["product"] == "PLWF105"
    granary = [x for x in states if x["device"] == "PLAF103#1"]
    assert [("raw" in x) for x in granary] == [True, False, False]
    text = out.getvalue()
    assert "PLWF105#1  weightPercent 62 → 58" in text
    assert text.count("PLWF105#1  ") == 2  # first sight and the one change


async def test_empty_device_list_is_reported_and_relisted(fake, tmp_path):
    fake.devices = []
    out = io.StringIO()
    client, spike = make_spike(fake, tmp_path, out=out)
    try:
        await client.login()
        await spike.poll_cycle()
        await spike.poll_cycle()
        fake.devices = [granary_listing()]
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    assert len(fake.calls(ps.LIST_PATH)) == 3
    text = out.getvalue()
    assert text.count("No devices on this PETLIBRO account") == 1
    assert "shared" in text and "PLAF103#1 (Granary Smart Feeder)" in text
    events = [x.get("event") for x in log_lines(tmp_path) if x["kind"] == "info"]
    assert events[:2] == ["connected", "no_devices"] and "devices_changed" in events
    assert any(x["kind"] == "state" for x in log_lines(tmp_path))


async def test_hourly_relist_picks_up_a_new_fountain(fake, tmp_path):
    fake.devices = [granary_listing()]
    client, spike = make_spike(fake, tmp_path, relist_seconds=0)
    try:
        await client.login()
        await spike.poll_cycle()
        fake.devices = [granary_listing(), fountain_listing()]
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    assert [s.label for s in spike.devices.values()] == ["PLAF103#1", "PLWF105#1"]


# --------------------------------------------------------------------------------------------
# A whole run: privacy, inventory, summary
# --------------------------------------------------------------------------------------------


async def test_full_run_never_leaks_secrets(fake, tmp_path, capsys):
    def echo_secrets(sn, server):
        if sn == GRANARY_SN:
            msg = f"device {sn} ({GRANARY_MAC}) rejected token {server.token} for {EMAIL}"
            return web.json_response({"code": 5001, "msg": msg, "data": None})
        return None

    fake.hooks[ps.GRAIN_STATUS_PATH] = echo_secrets
    out = io.StringIO()
    rc = await ps.run(opts(tmp_path, 0.6, poll=0.1), EMAIL, PASSWORD, TZ, base_url=fake.url,
                      out=out, request_gap=0)
    assert rc == 0
    captured = capsys.readouterr()
    files = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    everything = files + out.getvalue() + captured.out + captured.err
    secrets = [EMAIL, PASSWORD, PW_HASH, TOKEN_PREFIX, GRANARY_SN, FOUNTAIN_SN, GRANARY_MAC,
               FOUNTAIN_MAC, "AABBCC112233", SSID, GRANARY_NAME, FOUNTAIN_NAME, SHARE_ID]
    for secret in secrets:
        assert secret.lower() not in everything.lower(), secret
    assert "Granary Smart Feeder" in files and "PLAF103" in files
    assert "Dockstream Smart Fountain" in files and "PLWF105" in files
    errs = [x for x in log_lines(tmp_path) if x["kind"] == "error"]
    assert errs and errs[0]["where"] == "grainStatus" and "code 5001" in errs[0]["message"]
    assert REDACTED in errs[0]["message"]
    assert "code 5001" in captured.err


async def test_run_writes_slow_lines_fields_inventory_and_summary(fake, tmp_path):
    out = io.StringIO()
    rc = await ps.run(opts(tmp_path, 0.4, poll=0.1), EMAIL, PASSWORD, TZ, base_url=fake.url,
                      out=out, request_gap=0)
    assert rc == 0
    lines = log_lines(tmp_path)
    kinds = [x["kind"] for x in lines]
    assert kinds[0] == "info" and lines[0]["event"] == "start"
    connected = next(x for x in lines if x.get("event") == "connected")
    assert connected["devices"] == 2
    assert connected["list"][0] == {
        "device": "PLAF103#1",
        "productName": "Granary Smart Feeder",
        "productIdentifier": "PLAF103",
        "softwareVersion": "1.2.7",
        "hardwareVersion": "1.0.0",
        "online": True,
        "deviceShareState": 2,
    }
    slow = [x for x in lines if x["kind"] == "slow"]
    assert {(x["device"], x["endpoint"]) for x in slow} == {
        ("PLAF103#1", "baseInfo"), ("PLAF103#1", "workRecord"), ("PLWF105#1", "baseInfo")
    }
    base = next(x for x in slow if x["endpoint"] == "baseInfo")["data"]
    assert base["wifiSsid"] == REDACTED and base["pictureUrl"] == REDACTED
    assert base["productName"] == "Granary Smart Feeder" and base["remainingDesiccantDays"] == 20
    fields = {x["device"]: x["fields"] for x in lines if x["kind"] == "fields"}
    assert set(fields) == {"PLAF103#1", "PLWF105#1"}
    assert fields["PLWF105#1"]["realInfo"]["weightPercent"] == ["integer"]
    assert fields["PLAF103#1"]["grainStatus"]["todayFeedingQuantities[]"] == ["integer"]
    assert fields["PLAF103#1"]["workRecord"]["[].workRecords[].actualGrainNum"] == ["integer"]
    assert fields["PLAF103#1"]["deviceList"]["deviceSn"] == ["string"]
    assert fields["PLAF103#1"]["dataRealInfo"]["exceptionMessage"] == ["null"]
    assert lines[-1]["kind"] == "fields"
    assert any(x.get("event") == "exit" for x in lines)
    text = out.getvalue()
    assert "Summary" in text and "PLWF105#1 (Dockstream Smart Fountain)" in text
    assert "weightPercent min 62 max 62" in text
    assert "logs this PETLIBRO account out of the phone app" in text


async def test_summary_counts_key_changes(fake, tmp_path):
    out = io.StringIO()
    client, spike = make_spike(fake, tmp_path, out=out)
    try:
        await client.login()
        await spike.poll_cycle()
        fake.surplus = False
        fake.weight_percent = 40
        await spike.poll_cycle()
        fake.weight_percent = 70
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    text = spike.summary_text()
    assert "PLAF103#1 (Granary Smart Feeder): 3 polls, 0 errors" in text
    assert "surplusGrain 1" in text and "weightPercent 2" in text
    assert "weightPercent min 40 max 70" in text
    assert "surplusGrain True → False" in out.getvalue()


async def test_network_error_at_login_is_retried_with_backoff(tmp_path, capsys):
    # Nothing listens on this port: a connection error, logged and retried, never fatal.
    rc = await ps.run(opts(tmp_path, 0.3, poll=0.1), EMAIL, PASSWORD, TZ,
                      base_url="http://127.0.0.1:9", out=io.StringIO(), request_gap=0)
    assert rc == 0
    errs = [x for x in log_lines(tmp_path) if x["kind"] == "error"]
    assert errs and errs[0]["where"] == "login" and errs[0]["retry_in"] == 0.1
    assert "error (login)" in capsys.readouterr().err


def test_importing_the_module_does_not_import_aiohttp_client_session():
    # The session is created lazily on the first request.
    client = ps.PetlibroClient(EMAIL, PASSWORD, TZ)
    assert client._session is None
    asyncio.run(client.close())


# --------------------------------------------------------------------------------------------
# Endpoints a device does not support, online as a key value, device names
# --------------------------------------------------------------------------------------------


def code_5001(sn, server):
    return web.json_response({"code": 5001, "msg": f"unsupported for {sn}", "data": None})


async def test_endpoint_with_api_errors_is_dropped_without_backoff(fake, tmp_path):
    def granary_only(sn, server):
        return code_5001(sn, server) if sn == GRANARY_SN else None

    fake.hooks[ps.DATA_REAL_INFO_PATH] = granary_only
    out = io.StringIO()
    rc = await ps.run(opts(tmp_path, 0.75, poll=0.1), EMAIL, PASSWORD, TZ, base_url=fake.url,
                      out=out, request_gap=0)
    assert rc == 0
    granary_calls = [b for p, _, b in fake.calls(ps.DATA_REAL_INFO_PATH)
                     if b["deviceSn"] == GRANARY_SN]
    fountain_calls = [b for p, _, b in fake.calls(ps.DATA_REAL_INFO_PATH)
                      if b["deviceSn"] == FOUNTAIN_SN]
    assert len(granary_calls) == 3
    assert len(fountain_calls) >= 5  # polling carried on at the normal interval
    lines = log_lines(tmp_path)
    errs = [x for x in lines if x["kind"] == "error"]
    assert len(errs) == 3 and all(e["error"] == "ApiError" for e in errs)
    assert all(e["retry_in"] == 0.1 for e in errs)  # never backed off
    dropped = [x for x in lines if x.get("event") == "endpoint_dropped"]
    assert len(dropped) == 1
    assert dropped[0]["device"] == "PLAF103#1" and dropped[0]["endpoint"] == "dataRealInfo"
    assert dropped[0]["code"] == 5001 and GRANARY_SN not in dropped[0]["msg"]
    assert REDACTED in dropped[0]["msg"]
    fields = {x["device"]: x for x in lines if x["kind"] == "fields"}
    assert fields["PLAF103#1"]["dropped"] == {"dataRealInfo": 5001}
    assert fields["PLWF105#1"]["dropped"] == {}
    text = out.getvalue()
    assert text.count("dataRealInfo answered code 5001") == 1
    assert "dropped: dataRealInfo (code 5001)" in text


async def test_dropping_applies_to_slow_endpoints(fake, tmp_path):
    fake.hooks[ps.WORK_RECORD_PATH] = code_5001
    client, spike = make_spike(fake, tmp_path, slow_seconds=0)
    try:
        await client.login()
        for _ in range(5):
            assert await spike.poll_cycle() == 0  # API codes alone never call for backoff
    finally:
        await client.close()
        spike.close()
    assert len(fake.calls(ps.WORK_RECORD_PATH)) == 3
    assert len(fake.calls(ps.BASE_INFO_PATH)) == 10  # both devices, every cycle
    assert spike.devices[GRANARY_SN].dropped == {"workRecord": 5001}


async def test_a_success_resets_the_api_error_count(fake, tmp_path):
    answers = iter([False, False, True, False, False, True, False, False, False, False])

    def flaky(sn, server):
        if sn != GRANARY_SN:
            return None
        return None if next(answers) else code_5001(sn, server)

    fake.hooks[ps.GRAIN_STATUS_PATH] = flaky
    client, spike = make_spike(fake, tmp_path)
    try:
        await client.login()
        for _ in range(6):
            await spike.poll_cycle()
        assert spike.devices[GRANARY_SN].dropped == {}
        for _ in range(4):
            await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    assert spike.devices[GRANARY_SN].dropped == {"grainStatus": 5001}
    granary = [b for _, _, b in fake.calls(ps.GRAIN_STATUS_PATH) if b["deviceSn"] == GRANARY_SN]
    assert len(granary) == 9  # dropped after the 7th, 8th and 9th failed in a row


async def test_real_info_is_never_dropped(fake, tmp_path):
    fake.hooks[ps.REAL_INFO_PATH] = code_5001
    client, spike = make_spike(fake, tmp_path)
    try:
        await client.login()
        for _ in range(5):
            assert await spike.poll_cycle() == 0
    finally:
        await client.close()
        spike.close()
    assert len(fake.calls(ps.REAL_INFO_PATH)) == 10  # both devices, all five cycles
    assert all(state.dropped == {} for state in spike.devices.values())


async def test_transport_errors_still_back_off_and_are_never_dropped(fake, tmp_path):
    def granary_502(sn, server):
        return web.Response(status=502) if sn == GRANARY_SN else None

    fake.hooks[ps.REAL_INFO_PATH] = granary_502
    rc = await ps.run(opts(tmp_path, 0.75, poll=0.1), EMAIL, PASSWORD, TZ, base_url=fake.url,
                      out=io.StringIO(), request_gap=0)
    assert rc == 0
    errs = [x for x in log_lines(tmp_path) if x["kind"] == "error"]
    assert [e["retry_in"] for e in errs[:3]] == [0.1, 0.2, 0.4]
    assert not any(x.get("event") == "endpoint_dropped" for x in log_lines(tmp_path))


async def test_online_is_a_key_value(fake, tmp_path):
    out = io.StringIO()
    client, spike = make_spike(fake, tmp_path, out=out)
    try:
        await client.login()
        await spike.poll_cycle()
        fake.online = False
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    states = [x for x in log_lines(tmp_path) if x["kind"] == "state"]
    first = next(x for x in states if x["device"] == "PLAF103#1")
    assert next(iter(first["key"])) == "online" and first["key"]["online"] is True
    assert states[-1]["online"] is False and states[-1]["key"]["online"] is False
    text = out.getvalue()
    assert "PLAF103#1  online True → False" in text
    assert "PLWF105#1  online True → False" in text
    summary = spike.summary_text()
    assert "changes: online 1" in summary


async def test_online_falls_back_to_the_device_list(fake, tmp_path):
    fake.hooks[ps.REAL_INFO_PATH] = code_5001
    fake.devices[0]["online"] = False
    client, spike = make_spike(fake, tmp_path)
    try:
        await client.login()
        await spike.poll_cycle()
    finally:
        await client.close()
        spike.close()
    state = next(x for x in log_lines(tmp_path) if x["kind"] == "state")
    assert state["device"] == "PLAF103#1"
    assert state["online"] is False and state["key"]["online"] is False


async def test_device_named_like_its_product_stays_readable(fake, tmp_path, capsys):
    fake.devices[0]["name"] = "Granary Smart Feeder"
    out = io.StringIO()
    rc = await ps.run(opts(tmp_path, 0.3, poll=0.1), EMAIL, PASSWORD, TZ, base_url=fake.url,
                      out=out, request_gap=0)
    assert rc == 0
    lines = log_lines(tmp_path)
    connected = next(x for x in lines if x.get("event") == "connected")
    assert connected["list"][0]["productName"] == "Granary Smart Feeder"
    base = next(x for x in lines if x["kind"] == "slow" and x["device"] == "PLAF103#1"
                and x["endpoint"] == "baseInfo")["data"]
    assert base["name"] == REDACTED and base["productName"] == "Granary Smart Feeder"
    assert "PLAF103#1  Granary Smart Feeder:" in out.getvalue()
    assert "PLAF103#1 (Granary Smart Feeder)" in out.getvalue()
