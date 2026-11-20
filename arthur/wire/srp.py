"""The client side of Cognito's SRP password login (``USER_SRP_AUTH`` then the
``PASSWORD_VERIFIER`` challenge), as pycognito 2024.5.1 computes it (``pycognito/aws_srp.py``,
``AWSSRP``), in pure Python for both Pythons.

The big ``pow()`` calls are the cost: 0.45 s of arithmetic for a whole login under
MicroPython on the Mac, so about 11 to 14 s on the clock (25 to 30 times slower).
:func:`slow_pow` does them in slices of ``window`` exponent bits with an ``await`` between
slices, so the display keeps drawing meanwhile. Measured on the Mac under MicroPython, the
longest slice is 2 ms with a window of 4 bits (6 ms with 16), about 60 ms on the clock, inside
the 125 ms beat; slicing adds about 3% to the total.
"""

import asyncio
import binascii
import os

from arthur.wire import util

#: 3072-bit group from amazon-cognito-identity-js (``N_HEX``, ``G_HEX`` in aws_srp.py).
N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B"
    "F12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
G_HEX = "2"
#: Exponent bits per slice of :func:`slow_pow` (see above).
WINDOW = 4
N = int(N_HEX, 16)
G = 2
INFO_BITS = b"Caldera Derived Key"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def hash_hex(data):
    """``hash_sha256``: hex SHA-256, 64 digits."""
    return util.pad_left(util.sha256_hex(data), 64)


def hex_hash(hex_text):
    return hash_hex(binascii.unhexlify(hex_text))


def long_to_hex(n):
    return f"{n:x}"


def pad_hex(value):
    """``pad_hex``: even length, and a leading 00 when the top bit would be set."""
    text = value if isinstance(value, str) else long_to_hex(value)
    if len(text) % 2 == 1:
        return "0" + text
    if text[0] in "89ABCDEFabcdef":
        return "00" + text
    return text


K = int(hex_hash("00" + N_HEX + "0" + G_HEX), 16)


def hkdf(ikm, salt):
    """``compute_hkdf``: one HMAC-SHA256 extract and one expand block, 16 bytes."""
    prk = util.hmac_sha256(salt, ikm)
    return util.hmac_sha256(prk, INFO_BITS + b"\x01")[:16]


def calculate_u(big_a, big_b):
    return int(hex_hash(pad_hex(big_a) + pad_hex(big_b)), 16)


def cognito_timestamp(epoch):
    """``get_cognito_formatted_timestamp``: 'Wed Nov 20 12:05:09 UTC 2026', day unpadded."""
    days = int(epoch // 86400)
    y, mo, d = util.civil_from_days(days)
    rem = int(epoch) - days * 86400
    weekday = (days + 3) % 7  # 1970-01-01 was a Thursday; Monday is 0
    hh, mm, ss = rem // 3600, (rem // 60) % 60, rem % 60
    return f"{WEEKDAYS[weekday]} {MONTHS[mo - 1]} {d} {hh:02d}:{mm:02d}:{ss:02d} UTC {y}"


async def slow_pow(base, exponent, modulus, window=WINDOW):
    """``pow(base, exponent, modulus)``, a ``window`` of exponent bits at a time, yielding
    between windows. Each window is two C-level ``pow`` calls; nothing blocks for long."""
    if window <= 0 or exponent < (1 << window):
        return pow(base, exponent, modulus)
    base %= modulus
    bits = 0
    e = exponent
    while e:
        bits += 1
        e >>= 1
    chunks = (bits + window - 1) // window
    mask = (1 << window) - 1
    shift_pow = 1 << window
    result = 1
    i = chunks - 1
    while i >= 0:
        piece = (exponent >> (i * window)) & mask
        result = pow(result, shift_pow, modulus)
        if piece:
            result = (result * pow(base, piece, modulus)) % modulus
        i -= 1
        await asyncio.sleep(0)
    return result


class SrpClient:
    """One login attempt's secret ``a`` and public ``A`` (``AWSSRP.__init__``)."""

    def __init__(self, pool_id, small_a=None):
        self.pool_name = pool_id.split("_")[1]
        if small_a is None:
            small_a = int(binascii.hexlify(os.urandom(128)), 16) % N
        self.small_a = small_a
        self.big_a = None

    async def start(self, window=WINDOW):
        """Compute A = g^a mod N. Returns SRP_A as pycognito sends it (no padding)."""
        self.big_a = await slow_pow(G, self.small_a, N, window)
        if self.big_a % N == 0:
            raise ValueError("Safety check for A failed")
        return long_to_hex(self.big_a)

    async def key(self, user_id, password, big_b, salt_hex, window=WINDOW):
        """``get_password_authentication_key``: the 16-byte HKDF key."""
        if big_b % N == 0:
            raise ValueError("server B is zero mod N")
        u = calculate_u(self.big_a, big_b)
        if u == 0:
            raise ValueError("U cannot be zero.")
        up_hash = hash_hex((self.pool_name + user_id + ":" + password).encode())
        x = int(hex_hash(pad_hex(salt_hex) + up_hash), 16)
        g_x = await slow_pow(G, x, N, window)
        base = (big_b - K * g_x) % N
        s = await slow_pow(base, self.small_a + u * x, N, window)
        return hkdf(binascii.unhexlify(pad_hex(s)), binascii.unhexlify(pad_hex(long_to_hex(u))))

    async def respond(self, params, username, password, epoch, window=WINDOW):
        """``process_challenge``: the PASSWORD_VERIFIER answer for the challenge parameters."""
        user_id = params["USER_ID_FOR_SRP"]
        timestamp = cognito_timestamp(epoch)
        key = await self.key(user_id, password, int(params["SRP_B"], 16), params["SALT"], window)
        secret_block = params["SECRET_BLOCK"]
        msg = (self.pool_name.encode() + user_id.encode() + util.b64decode(secret_block)
               + timestamp.encode())
        return {
            "TIMESTAMP": timestamp,
            "USERNAME": params.get("USERNAME", username),
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block,
            "PASSWORD_CLAIM_SIGNATURE": util.b64encode(util.hmac_sha256(key, msg)),
        }

