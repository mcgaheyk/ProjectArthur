"""The log ring: the robot's log lines and the program's own console lines, kept in memory.

The clock has 8 MiB of flash that the program must not wear out and nowhere else to write, so
its log lives in RAM with a byte cap; the oldest lines are dropped first. Each line gets a
sequence number, so a reader (the update door, a script on the Mac) can ask for "everything
after N" and never see a line twice.

A line is a JSON-able dict. It is kept as its JSON text (far smaller than a dict on
MicroPython) and its size is the length of that text. Sequence numbers start at 1 when the
program starts; a reader that asks after a number this run has not reached yet (the program
was restarted) gets everything from the oldest line kept.

**With a store on the flash** (:class:`~arthur.onclock.logstore.LogStore`, D-0035) the lines
also go there, in batches (:meth:`ClockLog.flush`, called by the app about once a minute at a
quiet moment, and at once when :data:`FLUSH_LINES` or :data:`FLUSH_BYTES` are waiting), and
a read that asks for lines older than the ring holds is answered from the flash. The
numbering then carries on across restarts: a new run starts :data:`FLUSH_LINES` past the
last line on the flash, which is past any number the last run handed out (it never had more
than that many lines waiting), so a reader that remembers a number never skips a line. A
flash that refuses a write loses that batch, and says so in :meth:`store_status`; the ring
in RAM is unaffected.
"""

import json

#: The default cap, bytes of JSON text.
DEFAULT_MAX_BYTES = 128 * 1024
#: With a store: write to the flash at once when this many lines are waiting. Also how far
#: past the last line on the flash a new run starts its numbering.
FLUSH_LINES = 256
#: With a store: write at once when this many bytes of JSON text are waiting.
FLUSH_BYTES = 32 * 1024
#: How many characters of a flash error :meth:`ClockLog.store_status` keeps.
ERROR_CHARS = 200


class ClockLog:
    def __init__(self, max_bytes=DEFAULT_MAX_BYTES, store=None):
        self.max_bytes = int(max_bytes)
        self._texts = []  # JSON texts, oldest first
        self._first = 1  # sequence number of _texts[0]
        self._next = 1  # sequence number the next line gets
        self._bytes = 0
        self.dropped = 0  # lines dropped to keep under the cap, since start
        self.store = None
        self._pending = []  # [(seq, text)] not yet on the flash
        self._pending_bytes = 0
        self.store_error = None  # the last flash error, or None
        self.store_failures = 0  # batches the flash refused (or opening it), since start
        self.flushes = 0
        self.resumed_from = None  # the last line on the flash at start, with a store
        if store is not None:
            try:
                last = store.open()
                self.store = store
                self.resumed_from = last
                if last:
                    self._first = self._next = last + 1 + FLUSH_LINES
            except Exception as exc:  # noqa: BLE001 - the log works without the flash
                self._fail(exc)

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
        if self.store is not None:
            self._pending.append((seq, text))
            self._pending_bytes += len(text)
            if len(self._pending) >= FLUSH_LINES or self._pending_bytes >= FLUSH_BYTES:
                self.flush()
        return seq

    def _fail(self, exc):
        self.store_failures += 1
        self.store_error = (type(exc).__name__ + " " + str(exc))[:ERROR_CHARS]

    @property
    def pending(self):
        """Lines waiting to go to the flash."""
        return len(self._pending)

    def flush(self):
        """Write the waiting lines to the flash in one append. A refusal loses them (kept
        in RAM still) and is counted. True if something was written."""
        if self.store is None or not self._pending:
            return False
        items = self._pending
        self._pending = []
        self._pending_bytes = 0
        try:
            self.store.append(items)
        except Exception as exc:  # noqa: BLE001 - the log never stops the program
            self._fail(exc)
            return False
        self.flushes += 1
        return True

    @property
    def log_id(self):
        """The id of the log on the flash (the same across restarts), or None without one."""
        return self.store.log_id if self.store is not None else None

    def store_status(self):
        """For ``status``: the flash log, or None without one."""
        st = self.store
        if st is None:
            if self.store_failures:
                return {"error": self.store_error}
            return None
        return {"id": st.log_id, "first_seq": st.first_seq, "last_seq": st.last_seq,
                "bytes": st.size_bytes, "resumed_from": self.resumed_from,
                "pending": len(self._pending), "flushes": self.flushes,
                "failures": self.store_failures, "error": self.store_error}

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
        there is one). ``next_seq`` is the number to pass as ``after_seq`` next time. Lines
        older than the ring holds come from the flash, when there is a store."""
        if self.store is not None:
            after = int(after_seq)
            if after >= self._next:
                after = 0
            if after + 1 < self._first:
                try:
                    got = self.store.read(after, max_bytes, self._first)
                except Exception:  # noqa: BLE001 - fall back to what is in RAM
                    got = []
                if got:
                    return got, got[-1][0]
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
