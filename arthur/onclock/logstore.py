"""The log kept on the flash, so it outlives a restart of the program (D-0035).

The log ring (:class:`~arthur.onclock.clocklog.ClockLog`) lives in RAM and was lost at every
restart, and after about 13 hours of an ordinary day even without one. This keeps the same
lines in a folder on the flash, in segment files of about :data:`SEGMENT_BYTES`, the oldest
removed once the folder holds more than :data:`DEFAULT_MAX_BYTES` (about four days of an
ordinary day's log).

    <folder>/id                        the log's id: stays the same across restarts, so a
                                       reader on the Mac knows the numbering carries on
    <folder>/s0000012345.jsonl         a segment, named by the first line's number; each
                                       line is "<seq> <json text>\\n"

The ring hands lines over in batches (:meth:`LogStore.append`), each one append to the file,
so the flash sees a write about once a minute, not once a line. JFFS2 levels the wear; at
about 250 KB a day this is nothing to 7 MB of NOR flash. A line cut short by a power loss
(no newline at the end of the file) is never read, and the next batch starts a new segment
rather than continuing it.

Nothing here raises to the caller for a fault on the flash except :meth:`open` and
:meth:`append`, which the ring catches and counts. Runs on CPython and MicroPython: no
``os.path``, paths are built as strings.
"""

import binascii
import os

#: The most the folder may hold, bytes; the oldest segments are removed past it.
DEFAULT_MAX_BYTES = 1024 * 1024
#: A segment is closed once it reaches this size and the next batch starts a new one.
SEGMENT_BYTES = 64 * 1024
ID_NAME = "id"
_PREFIX = "s"
_SUFFIX = ".jsonl"


def segment_name(first):
    s = str(int(first))
    return _PREFIX + "0" * (10 - len(s)) + s + _SUFFIX


def segment_first(name):
    """The first line's number from a segment's file name, or None for any other file."""
    if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
        return None
    digits = name[len(_PREFIX):len(name) - len(_SUFFIX)]
    if not digits or not digits.isdigit():
        return None
    return int(digits)


def _lines(data):
    """``[(seq, text), ...]`` from a segment's bytes: whole lines only (a last line with no
    newline was cut short), any line that does not parse skipped."""
    out = []
    text = data.decode()
    parts = text.split("\n")
    for part in parts[:-1]:  # the piece after the last newline is empty or cut short
        sp = part.find(" ")
        if sp <= 0:
            continue
        head = part[:sp]
        if not head.isdigit():
            continue
        out.append((int(head), part[sp + 1:]))
    return out


class LogStore:
    def __init__(self, folder, max_bytes=DEFAULT_MAX_BYTES, segment_bytes=SEGMENT_BYTES):
        self.folder = folder.rstrip("/") if len(folder) > 1 else folder
        self.max_bytes = int(max_bytes)
        self.segment_bytes = int(segment_bytes)
        self.log_id = None
        self._segs = []  # [first, name, size] oldest first
        self._open_new = True  # the next batch starts a new segment
        self.last_seq = 0

    # -- start ----------------------------------------------------------------------------

    def open(self):
        """Make the folder if need be, read or make the id, and find the last line kept.
        Returns that line's number, 0 when there is none."""
        try:
            os.mkdir(self.folder)
        except OSError:
            pass  # there already
        self.log_id = self._read_id()
        segs = []
        for name in os.listdir(self.folder):
            first = segment_first(name)
            if first is None:
                continue
            segs.append([first, name, os.stat(self.folder + "/" + name)[6]])
        segs.sort()
        self._segs = segs
        self.last_seq = 0
        self._open_new = True
        # The newest segment that has a whole line holds the last one.
        for i in range(len(segs) - 1, -1, -1):
            data = self._read(segs[i][1])
            got = _lines(data)
            if got:
                self.last_seq = got[-1][0]
                # Carry on in it only if it ends cleanly and is not full yet.
                self._open_new = (i != len(segs) - 1 or not data.endswith(b"\n")
                                  or segs[i][2] >= self.segment_bytes)
                break
        return self.last_seq

    def _read_id(self):
        path = self.folder + "/" + ID_NAME
        try:
            with open(path) as f:
                got = f.read().strip()
            if got:
                return got
        except OSError:
            pass
        new = binascii.hexlify(os.urandom(8)).decode()
        with open(path, "w") as f:
            f.write(new + "\n")
        return new

    def _read(self, name):
        with open(self.folder + "/" + name, "rb") as f:
            return f.read()

    # -- write ----------------------------------------------------------------------------

    def append(self, items):
        """Write ``items`` (``[(seq, json_text), ...]``, rising) in one append. Raises
        OSError when the flash refuses; the ring counts it."""
        if not items:
            return
        data = "".join([str(s) + " " + t + "\n" for s, t in items]).encode()
        if self._open_new or not self._segs:
            self._segs.append([items[0][0], segment_name(items[0][0]), 0])
            self._open_new = False
        seg = self._segs[-1]
        with open(self.folder + "/" + seg[1], "ab") as f:
            f.write(data)
        seg[2] += len(data)
        self.last_seq = items[-1][0]
        if seg[2] >= self.segment_bytes:
            self._open_new = True
        self._prune()

    def _prune(self):
        total = 0
        for s in self._segs:
            total += s[2]
        while total > self.max_bytes and len(self._segs) > 1:
            old = self._segs.pop(0)
            total -= old[2]
            try:
                os.remove(self.folder + "/" + old[1])
            except OSError:
                pass

    # -- read -----------------------------------------------------------------------------

    @property
    def size_bytes(self):
        total = 0
        for s in self._segs:
            total += s[2]
        return total

    @property
    def first_seq(self):
        """The oldest line's number on the flash (a segment's first), or None."""
        return self._segs[0][0] if self._segs else None

    def read(self, after_seq, max_bytes, below):
        """``[(seq, json_text), ...]``: the lines numbered above ``after_seq`` and below
        ``below``, oldest first, as many as fit in ``max_bytes`` (at least one when there is
        one)."""
        start = 0
        for i in range(len(self._segs)):
            if self._segs[i][0] <= after_seq + 1:
                start = i
        out = []
        used = 0
        for i in range(start, len(self._segs)):
            if self._segs[i][0] >= below:
                break
            try:
                got = _lines(self._read(self._segs[i][1]))
            except OSError:
                continue
            for seq, text in got:
                if seq <= after_seq:
                    continue
                if seq >= below:
                    return out
                if out and used + len(text) > max_bytes:
                    return out
                out.append((seq, text))
                used += len(text)
        return out
