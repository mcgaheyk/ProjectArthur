"""What ``arthur.mtrandom`` allocates on a 32-bit MicroPython, the clock's kind of build.

Run by ``tests/test_mtrandom.py`` on the clock's own MicroPython (``micropython-armv7``,
emulated in Docker): ``micropython rngalloc.py REPO``. There a small int holds 31 bits, and
any int of 2**30 or more is a big int on the heap; the generator keeps its state in 16-bit
halves so that seeding and drawing make none. With the collector off, this measures the heap
taken by each piece and prints one line ``name bytes`` each, then ``done``. On a 64-bit
build it prints ``not-32-bit`` and nothing else.
"""

import gc
import sys

sys.path.insert(0, sys.argv[1])

if sys.maxsize != 2**31 - 1:
    print("not-32-bit")
    sys.exit(0)

from arthur import mtrandom


def measure(name, fn):
    gc.collect()
    gc.disable()
    a0 = gc.mem_alloc()
    keep = fn()
    used = gc.mem_alloc() - a0
    gc.enable()
    print(name, used)
    return keep


def draws(r, fn):
    def run():
        for _ in range(2000):
            fn(r)
    return run


measure("base", mtrandom._base)
# A seed of 2**30 or more is itself a big int (and so are the words split from it): measured
# apart from the small ones.
measure("seed_small", lambda: mtrandom.Random(123456))
measure("seed_max_small", lambda: mtrandom.Random(2**30 - 1))
measure("seed_negative", lambda: mtrandom.Random(-(2**30 - 1)))
measure("seed_crc32", lambda: mtrandom.Random(3735928559))
r = mtrandom.Random(5216)
xs = list(range(52))
measure("randrange17_x2000", draws(r, lambda g: g.randrange(17)))
measure("randrange_2p30m1_x2000", draws(r, lambda g: g.randrange(2**30 - 1)))
measure("randint_x2000", draws(r, lambda g: g.randint(16, 48)))
measure("getrandbits30_x2000", draws(r, lambda g: g.getrandbits(30)))
measure("getrandbits1_x2000", draws(r, lambda g: g.getrandbits(1)))
measure("choice_x2000", draws(r, lambda g: g.choice(xs)))
measure("shuffle52_x2000", draws(r, lambda g: g.shuffle(xs)))
measure("random_x2000", draws(r, lambda g: g.random()))
print("done")
