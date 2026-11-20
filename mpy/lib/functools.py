"""``functools.cache`` and ``lru_cache`` for MicroPython, which has no ``functools``.
Unbounded memo on the positional arguments: the shared code caches only small pure functions
(fonts, icons, the dissolve's order). MicroPython only."""


def cache(fn):
    memo = {}

    def wrapper(*args):
        try:
            return memo[args]
        except KeyError:
            v = memo[args] = fn(*args)
            return v

    return wrapper


def lru_cache(maxsize=128):
    if callable(maxsize):
        return cache(maxsize)
    return cache
