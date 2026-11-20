"""The log ring: the robot's log lines and the program's own console lines, kept in memory.

The clock has 8 MiB of flash that the program must not wear out and nowhere else to write, so
its log lives in RAM with a byte cap; the oldest lines are dropped first. Each line gets a
sequence number, so a reader (the update door, a script on the Mac) can ask for "everything
after N" and never see a line twice.

A line is a JSON-able dict. It is kept as its JSON text (far smaller than a dict on
MicroPython) and its size is the length of that text. Sequence numbers start at 1 when the
program starts; a reader that asks after a number this run has not reached yet (the program
was restarted) gets everything from the oldest line kept.
"""

import json

#: The default cap, bytes of JSON text.
DEFAULT_MAX_BYTES = 128 * 1024


class ClockLog:
    def __init__(self, max_bytes=DEFAULT_MAX_BYTES):
        self.max_bytes = int(max_bytes)
        self._texts = []  # JSON texts, oldest first
        self._first = 1  # sequence number of _texts[0]
        self._next = 1  # sequence number the next line gets
        self._bytes = 0
        self.dropped = 0  # lines dropped to keep under the cap, since start

    def append(self, line):
        """Keep ``line`` (a JSON-able dict); returns its sequence number. A line bigger than
        the whole cap is kept alone (everything older is dropped)."""
        text = json.dumps(line)
        seq = self._next
        self._next += 1
        self._texts.append(text)
        self._bytes += len(text)
        drop = 0
        n = len(self._texts)
        while self._bytes > self.max_bytes and drop < n - 1:
            self._bytes -= len(self._texts[drop])
            drop += 1
        if drop:
            del self._texts[:drop]
            self._first += drop
            self.dropped += drop
        return seq

    @property
    def first_seq(self):
        """The oldest kept line's number; ``last_seq + 1`` when empty."""
        return self._first

    @property
    def last_seq(self):
        """The newest line's number; 0 before the first."""
        return self._next - 1

    @property
    def size_bytes(self):
        return self._bytes

    def __len__(self):
        return len(self._texts)

    def _start(self, after_seq):
        after_seq = int(after_seq)
        if after_seq >= self._next:  # a reader from before a restart
            after_seq = 0
        return max(after_seq + 1, self._first) - self._first

    def read_text(self, after_seq, max_bytes):
        """``([(seq, json_text), ...], next_seq)``: the lines after ``after_seq``, oldest
        first, as their JSON texts, as many as fit in ``max_bytes`` (always at least one when
        there is one). ``next_seq`` is the number to pass as ``after_seq`` next time."""
        i = self._start(after_seq)
        out = []
        used = 0
        while i < len(self._texts):
            text = self._texts[i]
            if out and used + len(text) > max_bytes:
                break
            out.append((self._first + i, text))
            used += len(text)
            i += 1
        if out:
            return out, out[-1][0]
        return out, max(min(int(after_seq), self.last_seq), self._first - 1)

    def read(self, after_seq, max_bytes):
        """:meth:`read_text` with each line as a dict again: ``([(seq, line), ...],
        next_seq)``."""
        texts, nxt = self.read_text(after_seq, max_bytes)
        return [(seq, json.loads(text)) for seq, text in texts], nxt
