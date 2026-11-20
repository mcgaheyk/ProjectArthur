"""``contextlib.contextmanager`` for MicroPython (generator-based, single use), for
``arthur.render.text.strict_text``. MicroPython only."""


class _GeneratorContextManager:
    def __init__(self, gen):
        self.gen = gen

    def __enter__(self):
        return next(self.gen)

    def __exit__(self, typ, value, tb):
        if typ is None:
            try:
                next(self.gen)
            except StopIteration:
                return False
            raise RuntimeError("generator didn't stop")
        try:
            self.gen.throw(value)
        except StopIteration:
            return True
        except BaseException as exc:
            if exc is value:
                return False
            raise
        raise RuntimeError("generator didn't stop after throw()")


def contextmanager(fn):
    def helper(*args, **kw):
        return _GeneratorContextManager(fn(*args, **kw))

    return helper
