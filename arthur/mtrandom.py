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

The twist is done one word at a time as words are drawn, in the order a full twist would
compute them, which gives the same words: word ``i`` of a new block reads words ``i + 1``
and ``i + 397`` (mod 624), and those are already new exactly when the full twist would have
made them new. A scene draws a few dozen numbers per generator, so this saves most of the
work of a twist.
"""

import math

_N = 624
_M = 397
_MASK = 0xFFFFFFFF
_UPPER = 0x80000000
_LOWER = 0x7FFFFFFF
_MATRIX_A = 0x9908B0DF

#: ``init_genrand(19650218)``, the start of every ``init_by_array``; built once.
_BASE = None


def _mul32(x, c):
    """``(x * c) & 0xFFFFFFFF`` for 32-bit ``x`` and ``c``, in 16-bit halves of ``c``, so a
    64-bit MicroPython keeps every intermediate a small int."""
    return (x * (c & 0xFFFF) + (((x * (c >> 16)) & 0xFFFF) << 16)) & _MASK


def _base():
    global _BASE
    if _BASE is None:
        mt = [0] * _N
        mt[0] = 19650218
        prev = 19650218
        for i in range(1, _N):
            prev = (_mul32(prev ^ (prev >> 30), 1812433253) + i) & _MASK
            mt[i] = prev
        _BASE = tuple(mt)
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
        key = []
        while n:
            key.append(n & _MASK)
            n >>= 32
        if not key:
            key = [0]
        mt = list(_base())
        klen = len(key)
        i, j = 1, 0
        for _ in range(max(_N, klen)):
            p = mt[i - 1]
            mt[i] = ((mt[i] ^ _mul32(p ^ (p >> 30), 1664525)) + key[j] + j) & _MASK
            i += 1
            j += 1
            if i >= _N:
                mt[0] = mt[_N - 1]
                i = 1
            if j >= klen:
                j = 0
        for _ in range(_N - 1):
            p = mt[i - 1]
            mt[i] = ((mt[i] ^ _mul32(p ^ (p >> 30), 1566083941)) - i) & _MASK
            i += 1
            if i >= _N:
                mt[0] = mt[_N - 1]
                i = 1
        mt[0] = 0x80000000
        self._mt = mt
        self._i = _N

    def _next(self):
        """``genrand_uint32``: the next 32-bit word."""
        mt = self._mt
        i = self._i
        if i >= _N:
            i = 0
        y = (mt[i] & _UPPER) | (mt[i + 1 if i < _N - 1 else 0] & _LOWER)
        v = mt[i + _M if i < _N - _M else i + _M - _N] ^ (y >> 1)
        if y & 1:
            v ^= _MATRIX_A
        mt[i] = v
        self._i = i + 1
        v ^= v >> 11
        v ^= (v << 7) & 0x9D2C5680
        v ^= (v << 15) & 0xEFC60000
        v ^= v >> 18
        return v

    def getrandbits(self, k):
        if k < 0:
            raise ValueError("number of bits must be non-negative")
        if k == 0:
            return 0
        if k <= 32:
            return self._next() >> (32 - k)
        out = 0
        shift = 0
        while k > 0:
            r = self._next()
            if k < 32:
                r >>= 32 - k
            out |= r << shift
            shift += 32
            k -= 32
        return out

    def random(self):
        a = self._next() >> 5
        b = self._next() >> 6
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0)

    def _randbelow(self, n):
        k = _bit_length(n)
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
