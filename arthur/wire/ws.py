"""A small WebSocket client (RFC 6455) over asyncio streams, for both Pythons.

What it does: the upgrade handshake with the ``Sec-WebSocket-Accept`` check, masked client
frames, text, binary, close, ping and pong frames, messages split into fragments (with
control frames between them), and payload lengths of 7, 16 and 64 bits. What it does not:
extensions (no ``permessage-deflate`` is offered, so the server must not compress).
"""

import hashlib
import os
import struct

from arthur.wire import errors, http, net
from arthur.wire.const import REQUEST_TIMEOUT_S, USER_AGENT
from arthur.wire.util import b64encode

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

CONT, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

#: The largest message we accept. A robot update is about 4 KB.
MAX_MESSAGE = 512 * 1024


def accept_key(key):
    """The ``Sec-WebSocket-Accept`` a server must answer to ``key``."""
    return b64encode(hashlib.sha1(key.encode() + GUID).digest())


def mask(payload, key):
    """XOR with the 4-byte key (the same operation masks and unmasks)."""
    out = bytearray(payload)
    for i in range(len(out)):
        out[i] ^= key[i & 3]
    return out


def encode_frame(opcode, payload, fin=True, mask_key=None):
    """One client frame, masked. ``mask_key`` is for tests; normally random."""
    if mask_key is None:
        mask_key = os.urandom(4)
    n = len(payload)
    first = (0x80 if fin else 0) | opcode
    if n < 126:
        head = struct.pack(">BB", first, 0x80 | n)
    elif n < 65536:
        head = struct.pack(">BBH", first, 0x80 | 126, n)
    else:
        head = struct.pack(">BBQ", first, 0x80 | 127, n)
    return head + bytes(mask_key) + bytes(mask(payload, mask_key))


class Closed(Exception):
    """The connection is closed (by the server or by us). ``code`` if the server said."""

    def __init__(self, code=None, reason=""):
        Exception.__init__(self, "WebSocket closed" + (f" ({code})" if code else ""))
        self.code = code
        self.reason = reason


class WebSocket:
    def __init__(self, reader, writer, protocol=None):
        self.reader = reader
        self.writer = writer
        self.protocol = protocol
        self.closed = False

    @classmethod
    async def connect(cls, url_text, tls, headers=None, subprotocol=None,
                      timeout=REQUEST_TIMEOUT_S):
        """Open and upgrade. Raises WireError: ``auth`` for a 401 answer, else ``api``."""
        url = net.Url(url_text)
        reader, writer = await net.open_stream(url, tls, timeout)
        try:
            key = b64encode(os.urandom(16))
            lines = [
                "GET " + url.path + " HTTP/1.1",
                "Host: " + url.host_header(),
                "Upgrade: websocket",
                "Connection: Upgrade",
                "Sec-WebSocket-Key: " + key,
                "Sec-WebSocket-Version: 13",
                "User-Agent: " + USER_AGENT,
            ]
            if subprotocol:
                lines.append("Sec-WebSocket-Protocol: " + subprotocol)
            for name in headers or {}:
                lines.append(name + ": " + headers[name])
            await net.write_all(writer, ("\r\n".join(lines) + "\r\n\r\n").encode())
            status, reason, rheaders = await http.read_head(reader)
            if status != 101:
                try:
                    body = await http.read_body(reader, rheaders, 16384)
                except Exception:  # noqa: BLE001 - the status is what matters
                    body = b""
                said = body[:200].decode("utf-8", "replace") if body else ""
                message = f"WebSocket upgrade refused: HTTP {status} {reason} {said}"
                if status == 401:
                    raise errors.auth_error(message.strip(), status)
                raise errors.api_error(message.strip(), status)
            if rheaders.get("upgrade", "").lower() != "websocket" \
                    or "upgrade" not in rheaders.get("connection", "").lower():
                raise errors.api_error("WebSocket upgrade answer lacks Upgrade/Connection")
            if rheaders.get("sec-websocket-accept") != accept_key(key):
                raise errors.api_error("WebSocket upgrade answer has a wrong accept key")
            protocol = rheaders.get("sec-websocket-protocol")
            if protocol is not None and protocol != subprotocol:
                raise errors.api_error("WebSocket server chose subprotocol " + protocol)
            if rheaders.get("sec-websocket-extensions"):
                raise errors.api_error("WebSocket server chose an extension we did not offer")
            return cls(reader, writer, protocol)
        except BaseException:
            await net.close_stream(writer)
            raise

    async def _send(self, opcode, payload, mask_key=None):
        if self.closed:
            raise Closed()
        await net.write_all(self.writer, encode_frame(opcode, payload, True, mask_key))

    async def send_text(self, text):
        await self._send(TEXT, text.encode() if isinstance(text, str) else text)

    async def ping(self, payload=b""):
        await self._send(PING, payload)

    async def _read_exactly(self, n):
        try:
            return await self.reader.readexactly(n)
        except Exception as exc:  # EOFError (MicroPython), IncompleteReadError (CPython)
            self.closed = True
            raise Closed() from exc

    async def _frame(self):
        """(fin, opcode, payload) of the next frame."""
        b0, b1 = struct.unpack(">BB", await self._read_exactly(2))
        if b0 & 0x70:
            raise errors.api_error("WebSocket frame uses reserved bits")
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", await self._read_exactly(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", await self._read_exactly(8))[0]
        if n > MAX_MESSAGE:
            raise errors.api_error("WebSocket frame too large")
        key = await self._read_exactly(4) if b1 & 0x80 else None
        payload = await self._read_exactly(n) if n else b""
        if key is not None:
            payload = bytes(mask(payload, key))
        return fin, opcode, payload

    async def recv(self):
        """The next message as (opcode, bytes): TEXT or BINARY. Pings are answered, pongs
        dropped. A close frame is answered and raises :class:`Closed`."""
        parts = None
        kind = None
        size = 0
        while True:
            fin, opcode, payload = await self._frame()
            if opcode >= 0x8:
                if not fin or len(payload) > 125:
                    raise errors.api_error("bad WebSocket control frame")
                if opcode == PING:
                    await self._send(PONG, payload)
                    continue
                if opcode == PONG:
                    continue
                if opcode == CLOSE:
                    code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                    try:
                        await self._send(CLOSE, payload[:2])
                    except Exception:  # noqa: BLE001 - it is closing anyway
                        self.closed = True
                    self.closed = True
                    await net.close_stream(self.writer)
                    raise Closed(code, payload[2:].decode("utf-8", "replace"))
                raise errors.api_error(f"unknown WebSocket control opcode {opcode}")
            if opcode == CONT:
                if parts is None:
                    raise errors.api_error("WebSocket continuation without a start")
            elif opcode in (TEXT, BINARY):
                if parts is not None:
                    raise errors.api_error("WebSocket message started inside another")
                parts, kind = [], opcode
            else:
                raise errors.api_error(f"unknown WebSocket opcode {opcode}")
            size += len(payload)
            if size > MAX_MESSAGE:
                raise errors.api_error("WebSocket message too large")
            if fin and not parts:
                return kind, payload
            parts.append(payload)
            if fin:
                return kind, b"".join(parts)

    async def close(self, code=1000):
        """Say goodbye and close the socket. Never raises."""
        if not self.closed:
            try:
                await self._send(CLOSE, struct.pack(">H", code))
            except Exception:  # noqa: BLE001 - closing must never raise
                self.closed = True
        self.closed = True
        await net.close_stream(self.writer)

