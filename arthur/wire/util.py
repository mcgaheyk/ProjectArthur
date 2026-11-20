"""Small helpers both Pythons can run: base64, JWT claims, HMAC-SHA256, UTC time.

MicroPython 1.29 has no ``base64``, ``hmac`` or ``datetime`` and no ``str.ljust``, so they are
written here. Times are plain floats: seconds since 1970-01-01 UTC.
"""

import binascii
import hashlib
import json
import sys
import time

IS_MPY = sys.implementation.name == "micropython"


# -- base64 and JWT --------------------------------------------------------------------------


def b64encode(data):
    """Standard base64 of bytes, as str, no newline."""
    if isinstance(data, str):
        data = data.encode()
    return binascii.b2a_base64(data).decode().strip()


def b64decode(text):
    """Standard base64 (str or bytes) to bytes. Missing padding is added."""
    if isinstance(text, bytes):
        text = text.decode()
    text = text.strip()
    text += "=" * (-len(text) % 4)
    return binascii.a2b_base64(text)


def b64url_decode(text):
    """base64url without padding (as in a JWT) to bytes."""
    if isinstance(text, bytes):
        text = text.decode()
    return b64decode(text.replace("-", "+").replace("_", "/"))


def jwt_claims(token):
    """The payload of a JWT as a dict. The signature is not checked: the token came to us
    from Cognito over a verified TLS connection, and we only read ``exp`` and ``mid``."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    claims = json.loads(b64url_decode(parts[1]))
    if not isinstance(claims, dict):
        raise TypeError("JWT payload is not an object")
    return claims


def hmac_sha256(key, msg):
    """HMAC-SHA256 (RFC 2104), since MicroPython has no ``hmac``."""
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = bytes(key) + bytes(64 - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key))
    inner.update(msg)
    outer = hashlib.sha256(bytes(b ^ 0x5C for b in key))
    outer.update(inner.digest())
    return outer.digest()


def sha256_hex(data):
    return binascii.hexlify(hashlib.sha256(data).digest()).decode()


def pad_left(text, width, fill="0"):
    text = str(text)
    if len(text) >= width:
        return text
    return fill * (width - len(text)) + text


# -- time ------------------------------------------------------------------------------------


def now():
    """Wall time: seconds since 1970 UTC."""
    return time.time()


class _Monotonic:
    """Seconds that never go back and never wrap. MicroPython's ``ticks_ms`` wraps (every
    12.4 days on the unix port), so its differences are summed; call at least daily."""

    def __init__(self):
        self.total = 0.0
        self.last = time.ticks_ms() if IS_MPY else None

    def __call__(self):
        if not IS_MPY:
            return time.monotonic()
        t = time.ticks_ms()
        self.total += time.ticks_diff(t, self.last) / 1000.0
        self.last = t
        return self.total


mono = _Monotonic()


def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a proleptic Gregorian date (H. Hinnant's algorithm)."""
    if m <= 2:
        y -= 1
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    """(year, month, day) for days since 1970-01-01."""
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return (y + 1 if m <= 2 else y, m, d)


def _split_iso(text):
    """'YYYY-MM-DD[T ]HH:MM:SS[.frac][Z|±HH:MM]' -> (y, mo, d, h, mi, s, frac_digits, offset_s).
    Raises ValueError for anything else."""
    text = text.strip()
    if len(text) < 19 or text[4] != "-" or text[7] != "-" or text[10] not in "T " \
            or text[13] != ":" or text[16] != ":":
        raise ValueError("not an ISO timestamp: " + text)
    y, mo, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
    h, mi, s = int(text[11:13]), int(text[14:16]), int(text[17:19])
    rest = text[19:]
    frac = ""
    if rest[:1] == ".":
        i = 1
        while i < len(rest) and rest[i].isdigit():
            i += 1
        frac = rest[1:i]
        if not frac:
            raise ValueError("empty fraction")
        rest = rest[i:]
    offset = 0
    if rest in ("", "Z", "z"):
        pass
    elif len(rest) == 6 and rest[0] in "+-" and rest[3] == ":":
        offset = int(rest[1:3]) * 3600 + int(rest[4:6]) * 60
        if rest[0] == "-":
            offset = -offset
    else:
        raise ValueError("bad timestamp offset: " + text)
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 60):
        raise ValueError("timestamp out of range: " + text)
    return y, mo, d, h, mi, s, frac, offset


def parse_iso(text):
    """An ISO timestamp (no offset means UTC) as seconds since 1970 UTC."""
    y, mo, d, h, mi, s, frac, offset = _split_iso(text)
    secs = days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s - offset
    if frac:
        secs += int(frac[:6]) / (10 ** len(frac[:6]))
    return secs


def _hms(secs):
    days, rem = divmod(int(secs), 86400)
    y, mo, d = civil_from_days(days)
    return y, mo, d, rem // 3600, (rem // 60) % 60, rem % 60


def api_timestamp(epoch):
    """The history query's start: whole UTC seconds rounded up, "YYYY-MM-DD HH:MM:SS"
    (pylitterbot ``LitterRobot4.get_activity_history``)."""
    whole = int(epoch)
    if epoch > whole:
        whole += 1
    y, mo, d, h, mi, s = _hms(whole)
    return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}"


def iso_utc(epoch, micro=None):
    """``datetime.isoformat()`` of an aware UTC time: microseconds only when non-zero."""
    whole = int(epoch // 1)
    if micro is None:
        micro = round((epoch - whole) * 1000000)
        if micro >= 1000000:
            whole, micro = whole + 1, 0
    y, mo, d, h, mi, s = _hms(whole)
    out = f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"
    if micro:
        out += "." + pad_left(micro, 6)
    return out + "+00:00"


def utc_iso_ms(epoch):
    """``arthur.spikelog.utc_iso_ms``: UTC with milliseconds and a Z."""
    whole = int(epoch // 1)
    micro = round((epoch - whole) * 1000000)
    if micro >= 1000000:
        whole, micro = whole + 1, 0
    y, mo, d, h, mi, s = _hms(whole)
    ms = micro // 1000
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}.{ms:03d}Z"


def timestamp_iso(value):
    """What ``to_jsonable(pylitterbot.utils.to_timestamp(value))`` gives: None for a falsy
    value, else the ISO text of an aware UTC datetime. Strings: a 'Z' is dropped, '+00:00'
    is added when absent, a fraction is padded or cut to six digits (``to_timestamp``)."""
    if not value:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return iso_utc(float(value))
    if not isinstance(value, str):
        raise TypeError("timestamp must be str or number")
    text = value.replace("Z", "")
    if "+00:00" not in text:
        text += "+00:00"
    y, mo, d, h, mi, s, frac, offset = _split_iso(text)
    if offset:
        raise ValueError("unexpected offset: " + value)
    micro = int((frac + "000000")[:6]) if frac else 0
    whole = days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s
    return iso_utc(whole, micro)
