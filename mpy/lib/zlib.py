"""``zlib.crc32`` for MicroPython, from ``binascii`` (the same CRC-32). ``compress`` is not
here: only ``Frame.to_png`` uses it, and the clock never makes a PNG. MicroPython only."""

from binascii import crc32  # noqa: F401
