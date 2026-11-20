"""``contextvars.ContextVar`` for MicroPython: the drawing code runs on one thread, so one
global value per variable is all it needs. MicroPython only."""

_MISSING = object()


class Token:
    def __init__(self, var, old):
        self.var = var
        self.old_value = old


class ContextVar:
    def __init__(self, name, *, default=_MISSING):
        self.name = name
        self._default = default
        self._value = _MISSING

    def get(self, *default):
        if self._value is not _MISSING:
            return self._value
        if default:
            return default[0]
        if self._default is not _MISSING:
            return self._default
        raise LookupError(self.name)

    def set(self, value):
        tok = Token(self, self._value)
        self._value = value
        return tok

    def reset(self, token):
        self._value = token.old_value
