"""CPython's ``random.Random``, in pure Python, for MicroPython (D-0029).

The scenes, the dissolve and the in-use jokes draw from ``random.Random(seed)``. MicroPython
has no such class, and its own ``random`` gives other numbers, which would change what the
screens show. This is the same generator as CPython 3.13's: the Mersenne Twister (MT19937)
seeded from an int the way ``random.seed(int)`` does it (``init_by_array`` over the 32-bit
words of ``abs(seed)``, least significant first), with CPython's ``getrandbits``,
``random()`` from 53 bits, ``_randbelow`` by rejection, and the Python-level ``randrange``,
``randint``, ``choice``, ``choices``, ``shuffle`` and ``sample`` copied from
``Lib/random.py`` (3.13). Only what the shared code uses is here.

Imported only on MicroPython (through :mod:`arthur.compat`); CPython uses its own
``random.Random``. ``tests/test_mtrandom.py`` drives both through many seeds and every method
and requires the same results, on CPython and, through the MicroPython guard, on MicroPython.

**The state is kept in 16-bit halves.** The clock's MicroPython is a 32-bit build, where a
small int holds 31 bits: any int of 2**30 or more is a big int on the heap, and every
operation on one allocates. With the 32-bit words held whole, seeding there took 43 to 47 ms
and left about 0.5 MB of garbage, and a drawn word 47 us (measured on the clock,
2026-11-21); a scene's plan, which seeds 2 to 6 generators, spent about half its time or more
in seeding. So the state is two lists, ``_hi`` (bits 31 to 16 of each word) and ``_lo`` (bits
15 to 0), every product
is split until it stays under 2**30 (the bound is written next to each), and a drawn word
is never put back together unless more than 30 bits are asked for. Seeding, drawing up to
30 bits, ``random()`` and ``_randbelow(n)`` for ``n < 2**30`` make no big int on a 32-bit
build; a seed allocates the two state lists (about 5 KB there) and its short key, and
drawing an int allocates nothing (``tests/test_mtrandom.py`` checks both on the clock's own
MicroPython, emulated). A seed of 2**30 or more is itself a big int, and so is a result of
more than 30 bits.

The twist is done one word at a time as words are drawn, in the order a full twist would
compute them, which gives the same words: word ``i`` of a new block reads words ``i + 1``
and ``i + 397`` (mod 624), and those are already new exactly when the full twist would have
made them new. A scene draws a few dozen numbers per generator, so this saves most of the
work of a twist.
"""

import math

_N = 624

#: ``init_genrand(19650218)``, the start of every ``init_by_array``, as (high halves, low
#: halves); built once. Two lists that are never changed, only copied by slicing
#: (``list(t)`` of a tuple grows as it goes on MicroPython: 4.1 KB for 624 words, not 2.5).
_BASE = None


def _base():
    """``init_genrand(19650218)``: ``mt[i] = 1812433253 * (p ^ (p >> 30)) + i``, in halves.
    1812433253 is 0x6C07_8965: high half 0x6C07 = 0x6C << 8 | 0x07, low half 0x8965 =
    0x89 << 8 | 0x65."""
    global _BASE
    if _BASE is None:
        hi = [0] * _N
        lo = [0] * _N
        ph, pl = 19650218 >> 16, 19650218 & 0xFFFF
        hi[0], lo[0] = ph, pl
        for i in range(1, _N):
            xl = pl ^ (ph >> 14)  # x = p ^ (p >> 30): only the top 2 bits reach the low half
            # x * c mod 2**32 = xl*cl + ((xh*cl + xl*ch) << 16), with cl = cb << 8 | ca.
            b = xl * 0x89  # < 2**16 * 2**8
            t = xl * 0x65 + ((b & 0xFF) << 8)  # < 2**23 + 2**16: xl*cl less (b >> 8) << 16
            low = (t & 0xFFFF) + i  # < 2**16 + 624
            # high half: carry of xl*cl, xh*cl and xl*ch, each taken mod 2**16 by bytes;
            # every term < 2**24, the sum < 2**27.
            high = ((t >> 16) + (b >> 8) + (low >> 16)
                    + ph * 0x65 + (((ph * 0x89) & 0xFF) << 8)
                    + xl * 0x07 + (((xl * 0x6C) & 0xFF) << 8))
            ph, pl = high & 0xFFFF, low & 0xFFFF
            hi[i], lo[i] = ph, pl
        _BASE = (hi, lo)
    return _BASE


def _bit_length(n):
    """``n.bit_length()`` for ``n >= 0`` (MicroPython's int has no ``bit_length``)."""
    k = 0
    while n >= 0x10000:
        n >>= 16
        k += 16
    while n:
        n >>= 1
        k += 1
    return k


class Random:
    """``random.Random(seed)`` for an int seed: the methods the shared code uses."""

    def __init__(self, x=None):
        self.seed(x)

    def seed(self, a=None):
        if a is None or not isinstance(a, int) or isinstance(a, bool):
            raise TypeError("this Random takes an int seed only")
        n = -a if a < 0 else a
        # The key: the 32-bit words of abs(a), least significant first, in halves.
        kh = []
        kl = []
        while n:
            kl.append(n & 0xFFFF)
            kh.append((n >> 16) & 0xFFFF)
            n >>= 32
        if not kl:
            kh = [0]
            kl = [0]
        base = _base()
        hi = base[0][:]
        lo = base[1][:]
        klen = len(kl)
        i, j = 1, 0
        # mt[i] = (mt[i] ^ (p ^ (p >> 30)) * 1664525) + key[j] + j, p = mt[i - 1].
        # 1664525 is 0x0019_660D: high half 0x19, low half 0x660D = 0x66 << 8 | 0x0D.
        # p is always the word written last (after a wrap too: mt[0] = mt[623]), so it is
        # carried in (ph, pl) rather than read back.
        ph = hi[0]
        pl = lo[0]
        for _ in range(max(klen, 624)):
            xl = pl ^ (ph >> 14)  # x = p ^ (p >> 30), low half; its high half is ph
            b = xl * 0x66  # < 2**16 * 2**7
            t = xl * 0x0D + ((b & 0xFF) << 8)  # < 2**20 + 2**16: xl*cl less (b >> 8) << 16
            low = (lo[i] ^ (t & 0xFFFF)) + kl[j] + j  # < 3 * 2**16
            pl = low & 0xFFFF
            # high half: carry of xl*cl, xh*cl mod 2**16 by bytes, xl*ch, the key's high
            # half and the low sum's carry; every term < 2**23, the sum < 2**26.
            ph = ((hi[i] ^ (((t >> 16) + (b >> 8) + xl * 0x19 + ph * 0x0D
                             + (((ph * 0x66) & 0xFF) << 8)) & 0xFFFF))
                  + kh[j] + (low >> 16)) & 0xFFFF
            hi[i] = ph
            lo[i] = pl
            i += 1
            j += 1
            if i >= 624:
                hi[0] = ph
                lo[0] = pl
                i = 1
            if j >= klen:
                j = 0
        # mt[i] = (mt[i] ^ (p ^ (p >> 30)) * 1566083941) - i.
        # 1566083941 is 0x5D58_8B65: high half 0x5D58 = 0x5D << 8 | 0x58, low half 0x8B65 =
        # 0x8B << 8 | 0x65.
        for _ in range(623):
            xl = pl ^ (ph >> 14)
            b = xl * 0x8B  # < 2**16 * 2**8
            t = xl * 0x65 + ((b & 0xFF) << 8)  # < 2**23 + 2**16
            low = (lo[i] ^ (t & 0xFFFF)) - i  # > -624, < 2**16: the carry is -1 or 0
            pl = low & 0xFFFF
            # every term < 2**24, the sum < 2**27
            ph = ((hi[i] ^ (((t >> 16) + (b >> 8) + xl * 0x58 + (((xl * 0x5D) & 0xFF) << 8)
                             + ph * 0x65 + (((ph * 0x8B) & 0xFF) << 8)) & 0xFFFF))
                  + (low >> 16)) & 0xFFFF
            hi[i] = ph
            lo[i] = pl
            i += 1
            if i >= 624:
                hi[0] = ph
                lo[0] = pl
                i = 1
        hi[0] = 0x8000  # mt[0] = 0x80000000
        lo[0] = 0
        self._hi = hi
        self._lo = lo
        self._i = _N

    def _top(self, k):
        """``genrand_uint32() >> (32 - k)`` for ``1 <= k <= 32``: the top ``k`` bits of the
        next word, twisted and tempered in halves. Below 2**30 for ``k <= 30``."""
        hi = self._hi
        lo = self._lo
        i = self._i
        # (624, 623, 397 and 227 = 624 - 397 written out: a literal is cheaper than a global
        # on MicroPython.)
        if i >= 624:
            i = 0
        i1 = i + 1 if i < 623 else 0
        im = i + 397 if i < 227 else i - 227
        # The twist: y = (mt[i] & 0x80000000) | (mt[i + 1] & 0x7FFFFFFF);
        # mt[i] = mt[i + 397] ^ (y >> 1) ^ (0x9908B0DF if y is odd).
        yh = (hi[i] & 0x8000) | (hi[i1] & 0x7FFF)
        yl = lo[i1]
        h = hi[im] ^ (yh >> 1)
        low = lo[im] ^ (yl >> 1) ^ ((yh & 1) << 15)
        if yl & 1:
            h ^= 0x9908
            low ^= 0xB0DF
        hi[i] = h
        lo[i] = low
        self._i = i + 1
        # The tempering, each step on both halves (all values < 2**23):
        # v ^= v >> 11
        low ^= (low >> 11) | ((h << 5) & 0xFFFF)
        h ^= h >> 11
        # v ^= (v << 7) & 0x9D2C5680
        h ^= ((h << 7) | (low >> 9)) & 0x9D2C
        low ^= (low << 7) & 0x5680
        # v ^= (v << 15) & 0xEFC60000: only the high half changes; of h << 15 only bit 0
        # of h lands in it (h << 15 itself could reach 2**31)
        h ^= (((h & 1) << 15) | (low >> 1)) & 0xEFC6
        # v ^= v >> 18
        low ^= h >> 2
        if k <= 16:
            return h >> (16 - k)
        return (h << (k - 16)) | (low >> (32 - k))  # < 2**k

    def _next(self):
        """``genrand_uint32``: the next 32-bit word (a big int on a 32-bit build when it is
        2**30 or more; the drawing methods use :meth:`_top`)."""
        return self._top(32)

    def getrandbits(self, k):
        if k < 0:
            raise ValueError("number of bits must be non-negative")
        if k == 0:
            return 0
        if k <= 32:
            return self._top(k)
        out = 0
        shift = 0
        while k > 0:
            out |= self._top(min(k, 32)) << shift
            shift += 32
            k -= 32
        return out

    def random(self):
        a = self._top(27)  # genrand_uint32() >> 5
        b = self._top(26)  # genrand_uint32() >> 6
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0)

    def _randbelow(self, n):
        k = _bit_length(n)
        if k <= 32:
            r = self._top(k)
            while r >= n:
                r = self._top(k)
            return r
        r = self.getrandbits(k)
        while r >= n:
            r = self.getrandbits(k)
        return r

    def randrange(self, start, stop=None, step=1):
        if stop is None:
            if step != 1:
                raise TypeError("Missing a non-None stop argument")
            if start > 0:
                return self._randbelow(start)
            raise ValueError("empty range for randrange()")
        width = stop - start
        if step == 1:
            if width > 0:
                return start + self._randbelow(width)
            raise ValueError("empty range in randrange()")
        if step > 0:
            n = (width + step - 1) // step
        elif step < 0:
            n = (width + step + 1) // step
        else:
            raise ValueError("zero step for randrange()")
        if n <= 0:
            raise ValueError("empty range in randrange()")
        return start + step * self._randbelow(n)

    def randint(self, a, b):
        return self.randrange(a, b + 1)

    def choice(self, seq):
        if not len(seq):
            raise IndexError("Cannot choose from an empty sequence")
        return seq[self._randbelow(len(seq))]

    def shuffle(self, x):
        for i in range(len(x) - 1, 0, -1):
            j = self._randbelow(i + 1)
            x[i], x[j] = x[j], x[i]

    def sample(self, population, k):
        n = len(population)
        if not 0 <= k <= n:
            raise ValueError("Sample larger than population or is negative")
        result = [None] * k
        setsize = 21
        if k > 5:
            setsize += 4 ** math.ceil(math.log(k * 3, 4))
        if n <= setsize:
            pool = list(population)
            for i in range(k):
                j = self._randbelow(n - i)
                result[i] = pool[j]
                pool[j] = pool[n - i - 1]
        else:
            selected = set()
            for i in range(k):
                j = self._randbelow(n)
                while j in selected:
                    j = self._randbelow(n)
                selected.add(j)
                result[i] = population[j]
        return result

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        n = len(population)
        if cum_weights is None:
            if weights is None:
                nf = n + 0.0
                return [population[math.floor(self.random() * nf)] for _ in range(k)]
            cum_weights = []
            total = 0
            for w in weights:
                total = total + w
                cum_weights.append(total)
        elif weights is not None:
            raise TypeError("Cannot specify both weights and cumulative weights")
        if len(cum_weights) != n:
            raise ValueError("The number of weights does not match the population")
        total = cum_weights[-1] + 0.0
        if total <= 0.0:
            raise ValueError("Total of weights must be greater than zero")
        if not math.isfinite(total):
            raise ValueError("Total of weights must be finite")
        hi = n - 1
        out = []
        for _ in range(k):
            x = self.random() * total
            lo, h = 0, hi
            while lo < h:  # bisect.bisect_right(cum_weights, x, 0, hi)
                mid = (lo + h) // 2
                if x < cum_weights[mid]:
                    h = mid
                else:
                    lo = mid + 1
            out.append(population[lo])
        return out
