"""Our SRP arithmetic against pycognito's, byte for byte, offline."""

from __future__ import annotations

import datetime as dt
import random
import types

import pytest
from pycognito import aws_srp

from arthur.wire import srp, util


@pytest.mark.parametrize("window", [1, 4, 16, 64])
async def test_slow_pow_is_pow(window):
    rng = random.Random(7)
    for _ in range(5):
        base = rng.getrandbits(3000)
        exp = rng.getrandbits(rng.choice([1, 17, 300, 1100]))
        assert await srp.slow_pow(base, exp, srp.N, window) == pow(base, exp, srp.N)


def test_constants_match_pycognito():
    assert srp.N == aws_srp.hex_to_long(aws_srp.N_HEX)
    assert srp.K == aws_srp.hex_to_long(aws_srp.hex_hash("00" + aws_srp.N_HEX + "0"
                                                          + aws_srp.G_HEX))
    for value in (0x7F, 0x80, 0xABC, srp.N - 1, 12345):
        assert srp.pad_hex(value) == aws_srp.pad_hex(value)
    assert srp.hkdf(b"abc", b"salt") == aws_srp.compute_hkdf(bytearray(b"abc"),
                                                               bytearray(b"salt"))


@pytest.mark.parametrize("when", ["2026-11-05T09:04:03", "2026-11-20T23:59:59",
                                  "2027-02-28T00:00:00", "2024-02-29T12:00:00"])
def test_timestamp_matches_pycognito(when):
    t = dt.datetime.fromisoformat(when)
    epoch = t.replace(tzinfo=dt.UTC).timestamp()
    assert srp.cognito_timestamp(epoch) == aws_srp.AWSSRP.get_cognito_formatted_timestamp(t)


async def test_challenge_answer_matches_pycognito(monkeypatch):
    when = dt.datetime(2026, 11, 20, 12, 5, 9)
    monkeypatch.setattr(aws_srp, "datetime", types.SimpleNamespace(
        datetime=types.SimpleNamespace(utcnow=lambda: when)))
    small_a = random.Random(3).getrandbits(1024) % srp.N
    theirs = aws_srp.AWSSRP("user@example.com", "pw ünïcode", "us-east-1_rjhNnZVAm",
                            "client", client=object())
    theirs.small_a_value = small_a
    theirs.large_a_value = theirs.calculate_a()
    ours = srp.SrpClient("us-east-1_rjhNnZVAm", small_a=small_a)
    assert await ours.start() == aws_srp.long_to_hex(theirs.large_a_value)
    params = {"USER_ID_FOR_SRP": "f00-sub", "SALT": "0a1b2c", "SECRET_BLOCK": "c2VjcmV0",
              "SRP_B": format(pow(5, 999, srp.N), "x"), "USERNAME": "f00-sub"}
    expected = theirs.process_challenge(params, {"USERNAME": "user@example.com"})
    epoch = when.replace(tzinfo=dt.UTC).timestamp()
    got = await ours.respond(params, "user@example.com", "pw ünïcode", epoch)
    assert got == expected


def test_hmac_matches_the_standard_library():
    import hashlib
    import hmac

    for key in (b"", b"k", b"x" * 64, b"y" * 100):
        assert util.hmac_sha256(key, b"msg") == hmac.new(key, b"msg", hashlib.sha256).digest()
