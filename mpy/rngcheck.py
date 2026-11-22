"""Drives a ``Random`` class through every method the shared code uses, for one seed.

Runs on both Pythons. ``tests/test_mtrandom.py`` compares ``arthur.mtrandom.Random`` with
CPython's ``random.Random`` through it on CPython, and the MicroPython guard
(``mpy/guard.py``) runs it under MicroPython against CPython's answers.

The calls depend on the seed, so different seeds take different paths: every method, with
the argument shapes the scenes use (``randrange`` with a step, ``choices`` with weights,
``sample`` of a range and of a list, both of ``sample``'s algorithms), and enough draws to
cross several 624-word blocks of the twister.
"""

#: Seeds the guard and the test use: edges, the dissolve's seed, CRC-32 sized values like
#: the scenes' and the jokes', more than 32 bits, negative, and either side of 2**30 (the
#: first big int on a 32-bit MicroPython, which ``arthur.mtrandom`` keeps its state under).
SEEDS = (
    0, 1, 2, 7, 5216, 65535, 65536, 2**30 - 1, 2**30, 2**31 - 1, 2**31, 2**32 - 1, 2**32,
    2**32 + 1, 2**64 - 1, 2**64 + 5, 2**64 + 12345, 2**100 + 3, -1, -5216, -(2**30),
    -(2**31), -(2**40),
    3735928559, 2654435761, 123456789, 987654321, 4022250974, 1144201745,
)


def more_seeds(n):
    """``n`` more seeds spread over 32 bits, from a fixed sequence."""
    out = []
    x = 0x2545F491
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        out.append(x)
    return out


def exercise(random_class, seed):
    """A list of every result drawn from ``random_class(seed)``."""
    r = random_class(seed)
    out = []
    plan = seed & 0xFFFF if seed >= 0 else (-seed) & 0xFFFF
    out.append(r.random())
    for k in (1, 2, 7, 8, 15, 16, 17, 29, 30, 31, 32, 33, 63, 64, 65, 96, 100):
        out.append(r.getrandbits(k))
    for n in (1, 2, 3, 10, 17, 255, 256, 257, 1000, 2**15, 2**16, 2**16 + 1, 2**29 - 1,
              2**29, 2**30 - 1, 2**30, 2**30 + 1, 2**31, 2**32 - 1, 2**32, 2**32 + 7,
              10**20):
        out.append(r.randrange(n))
    out.append(r.randrange(3, 99, 2))
    out.append(r.randrange(8, 17, 2))  # the bird's lead (LEAD_MIN, LEAD_MAX + 1, 2)
    out.append(r.randrange(99, 3, -3))
    out.append(r.randrange(-50, 50))
    for a, b in ((1, 6), (16, 48), (8, 16), (2, 3), (0, 0), (-5, 5), (24, 40)):
        out.append(r.randint(a, b))
    out.append(r.choice("abcdefg"))
    out.append(r.choice([(1, 2), (3, 4)]))
    out.append(r.choice((2, 2, 3)))
    out.append(r.choices([1, 2, 3], [4, 5, 2]))
    out.append(r.choices(["a", "b", "c", "d"], [3, 2, 2, 1], k=5))
    out.append(r.choices("xyz", k=4))
    out.append(r.choices("xyz", cum_weights=[1, 5, 6], k=3))
    out.append(r.sample(range(20), 7))
    out.append(r.sample(range(5), 5))
    out.append(r.sample(list("abcdefghij"), 10))
    out.append(r.sample(range(1, 9), 2))
    out.append(r.sample(range(1000), 30))  # the set-based algorithm
    out.append(r.sample(range(200), 6))
    lst = list(range(50))
    r.shuffle(lst)
    out.append(lst)
    lst = list(range(832))  # the dissolve's order
    r.shuffle(lst)
    out.append(lst)
    # A seed-dependent mix, long enough to cross block boundaries.
    for i in range(300 + plan % 400):
        m = (plan + i) % 6
        if m == 0:
            out.append(r.random())
        elif m == 1:
            out.append(r.randint(0, 1 + (i % 50)))
        elif m == 2:
            out.append(r.getrandbits(1 + i % 70))
        elif m == 3:
            out.append(r.choice(range(1, 2 + i % 9)))
        elif m == 4:
            out.append(r.random() < 0.45)
        else:
            out.append(r.randrange(0, 40, 1 + i % 4))
    return out


def as_text(values):
    """The results as text the same on both Pythons: floats by their exact bits."""
    import struct

    def one(v):
        if isinstance(v, bool):
            return "T" if v else "F"
        if isinstance(v, float):
            return "f" + "".join(f"{b:02x}" for b in struct.pack(">d", v))
        if isinstance(v, (list, tuple)):
            return "[" + ",".join(one(x) for x in v) + "]"
        return repr(v)

    return one(values)
