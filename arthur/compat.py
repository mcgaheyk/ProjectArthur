"""What lets the shared modules run unchanged on CPython 3.13 and MicroPython 1.29 (D-0029).

The shared modules (``model``, ``config``, ``timebase``, ``schedule``, ``mapping``,
``sources/tracker`` and all of ``render`` but ``preview``) import from here what one of the
two Pythons lacks. On CPython every helper is CPython's own thing, so behaviour there does not
change; on MicroPython each is a small stand-in that gives the same results. Stdlib modules
MicroPython lacks entirely (``datetime``, ``functools``, ``pathlib``, ``typing``, ...) are
stand-ins in ``mpy/lib/`` instead, outside this package, so CPython never imports them.

- :func:`record`: ``@dataclass(frozen=True)``. On CPython it *is* that decorator. MicroPython
  keeps no class annotations, so the field names are given to the decorator, in order; on
  CPython they are checked against the annotations.
- :func:`tuple_record`: ``class X(NamedTuple)``. On CPython the class is built by
  ``NamedTuple`` itself; on MicroPython it is a ``collections.namedtuple`` subclass and the
  decorated name is a factory function (``isinstance`` against it does not work there).
- :func:`field`, :func:`replace`, :func:`field_names`, :func:`field_default`: the
  ``dataclasses`` functions the shared code uses.
- :data:`Random`: ``random.Random``. MicroPython has none; :mod:`arthur.mtrandom` gives the
  same numbers bit for bit.
- :func:`zip_strict`, :func:`stable_sorted`, :func:`decimal_ratio`: ``zip(strict=True)``, a
  sort that keeps the order of equal keys (MicroPython's ``sorted`` is not stable), and
  ``Fraction(str(x))``.

The fuller list of differences, and how each was found: ``docs/reference/micropython.md``.
"""

import sys

#: True under MicroPython (the clock), False under CPython (the Mac, the tests).
IS_MICROPYTHON = sys.implementation.name == "micropython"

_MISSING = object()


def _names(fields):
    names = tuple(fields.split())
    if not names:
        raise TypeError("a record needs at least one field")
    return names


if not IS_MICROPYTHON:  # =================================================================
    import dataclasses
    import inspect
    import random as _random
    import types as _types
    import typing as _typing
    from fractions import Fraction as _Fraction

    #: ``random.Random``: CPython's own.
    Random = _random.Random

    def _check_annotations(cls, names):
        ann = tuple(inspect.get_annotations(cls))
        if ann != names:
            raise TypeError(
                f"{cls.__name__}: record fields {names} do not match its annotations {ann}"
            )

    def record(fields):
        """``@dataclass(frozen=True)``, with the field names listed for MicroPython."""
        names = _names(fields)

        def deco(cls):
            _check_annotations(cls, names)
            return dataclasses.dataclass(frozen=True)(cls)

        return deco

    def tuple_record(fields):
        """``class X(NamedTuple)``, with the field names listed for MicroPython."""
        names = _names(fields)

        def deco(cls):
            _check_annotations(cls, names)
            body = {k: v for k, v in cls.__dict__.items()
                    if k not in ("__dict__", "__weakref__")}
            return _types.new_class(cls.__name__, (_typing.NamedTuple,),
                                    exec_body=lambda ns: ns.update(body))

        return deco

    field = dataclasses.field
    replace = dataclasses.replace

    def field_names(cls):
        """The field names of a :func:`record` class, in order."""
        return tuple(f.name for f in dataclasses.fields(cls))

    def field_default(cls, name):
        """The default of field ``name`` of a :func:`record` class."""
        return cls.__dataclass_fields__[name].default

    def zip_strict(*seqs):
        """``zip(*seqs, strict=True)``."""
        return zip(*seqs, strict=True)

    def stable_sorted(seq, key):
        """``sorted(seq, key=key)``: CPython's sort is stable."""
        return sorted(seq, key=key)

    def decimal_ratio(x):
        """``(numerator, denominator)`` of ``Fraction(str(x))``: a float read as the decimal
        it prints as (33.3 is 333/10, not the float's binary value)."""
        f = _Fraction(str(x))
        return f.numerator, f.denominator

else:  # MicroPython ===========================================================================
    from collections import namedtuple

    from arthur.mtrandom import Random  # noqa: F401  (re-exported)

    _RECORD_SKIP = ("__module__", "__qualname__", "__dict__", "__weakref__", "__doc__")

    class _Field:
        def __init__(self, default, default_factory):
            self.default = default
            self.default_factory = default_factory

    def field(*, default=_MISSING, default_factory=_MISSING):
        """``dataclasses.field``, for ``default`` and ``default_factory`` only."""
        return _Field(default, default_factory)

    def _defaults(cls, names):
        """``(body, defaults)``: the class body without field defaults, and per field its
        default value, a ``_Field`` or ``_MISSING``."""
        body = {}
        for k, v in cls.__dict__.items():
            if k not in _RECORD_SKIP:
                body[k] = v
        defaults = []
        for n in names:
            v = body.pop(n, _MISSING)
            if isinstance(v, _Field) and v.default_factory is _MISSING:
                v = v.default
            defaults.append(v)
        return body, defaults

    def _signature(names, defaults, glob):
        """``"a, b=_d1, c=_d2"`` for the generated function, with the defaults in ``glob``.
        A default factory's argument defaults to a marker that the body replaces."""
        params = []
        for i, (n, d) in enumerate(zip(names, defaults)):
            if d is _MISSING:
                if params and "=" in params[-1]:
                    raise TypeError("non-default field " + n + " follows a default field")
                params.append(n)
            elif isinstance(d, _Field):
                glob["_f" + str(i)] = d.default_factory
                params.append(n + "=_MISSING")
            else:
                glob["_d" + str(i)] = d
                params.append(n + "=_d" + str(i))
        return ", ".join(params)

    def _record_eq(self, other):
        if other.__class__ is not self.__class__:
            return False
        for n in self._fields:
            if not _same(getattr(self, n), getattr(other, n)):
                return False
        return True

    def _same(a, b):
        # ``a == b`` with None never compared against (micropython-lib's datetime raises on
        # ``datetime == None``; CPython answers False).
        if a is None or b is None:
            return a is b
        return a == b

    def _record_ne(self, other):
        return not _record_eq(self, other)

    def _record_hash(self):
        return hash(tuple(getattr(self, n) for n in self._fields))

    def _record_repr(self):
        return self.__class__.__name__ + "(" + ", ".join(
            n + "=" + repr(getattr(self, n)) for n in self._fields) + ")"

    def record(fields):
        """A frozen-dataclass look-alike: ``__init__`` with the same signature, ``==`` and
        ``hash`` over the fields, ``repr``, ``__post_init__``. Not frozen on MicroPython."""
        names = _names(fields)

        def deco(cls):
            body, defaults = _defaults(cls, names)
            glob = {"_MISSING": _MISSING}
            lines = ["def __init__(self, " + _signature(names, defaults, glob) + "):"]
            for i, n in enumerate(names):
                if isinstance(defaults[i], _Field):
                    lines.append("    if " + n + " is _MISSING: " + n + " = _f" + str(i) + "()")
                lines.append("    self." + n + " = " + n)
            if "__post_init__" in body:
                lines.append("    self.__post_init__()")
            exec("\n".join(lines), glob)  # noqa: S102 (the only way to give __init__ its signature)
            body["__init__"] = glob["__init__"]
            body["_fields"] = names
            body["_defaults"] = {n: d for n, d in zip(names, defaults)
                                 if d is not _MISSING and not isinstance(d, _Field)}
            body["__eq__"] = _record_eq
            body["__ne__"] = _record_ne
            body["__hash__"] = _record_hash
            body.setdefault("__repr__", _record_repr)
            return type(cls.__name__, (), body)

        return deco

    def tuple_record(fields):
        """A ``NamedTuple`` look-alike: a namedtuple subclass with the class's methods, made
        by a factory function with the same signature (MicroPython has no ``__new__`` on a
        namedtuple to override). The factory is not a class."""
        names = _names(fields)

        def deco(cls):
            body, defaults = _defaults(cls, names)
            klass = type(cls.__name__, (namedtuple(cls.__name__, names),), body)
            glob = {"_cls": klass}
            src = ("def " + cls.__name__ + "(" + _signature(names, defaults, glob)
                   + "):\n    return _cls(" + ", ".join(names) + ")")
            exec(src, glob)  # noqa: S102 (the only way to give the factory its signature)
            return glob[cls.__name__]

        return deco

    def replace(obj, **changes):
        """``dataclasses.replace`` for a :func:`record` instance."""
        values = {}
        for n in obj._fields:
            values[n] = changes.pop(n) if n in changes else getattr(obj, n)
        if changes:
            raise TypeError("unknown field(s): " + ", ".join(changes))
        return obj.__class__(**values)

    def field_names(cls):
        return cls._fields

    def field_default(cls, name):
        return cls._defaults[name]

    def zip_strict(*seqs):
        n = len(seqs[0])
        for s in seqs[1:]:
            if len(s) != n:
                raise ValueError("zip_strict: the sequences differ in length")
        return zip(*seqs)

    def stable_sorted(seq, key):
        """A stable sort: ties keep their order (MicroPython's ``sorted`` does not)."""
        items = list(seq)
        order = sorted(range(len(items)), key=lambda i: (key(items[i]), i))
        return [items[i] for i in order]

    def decimal_ratio(x):
        """``(numerator, denominator)`` of the decimal ``x`` prints as. MicroPython's float
        repr is not always the shortest round trip, so the float goes through 15 significant
        digits, which is exact for every decimal of up to 15 digits."""
        if isinstance(x, int):
            return int(x), 1
        s = f"{x:.15g}"
        neg = s.startswith("-")
        if neg:
            s = s[1:]
        exp = 0
        if "e" in s:
            s, e = s.split("e")
            exp = int(e)
        if "." in s:
            whole, frac = s.split(".")
        else:
            whole, frac = s, ""
        frac = frac.rstrip("0")
        num = int(whole + frac)
        exp -= len(frac)
        if exp >= 0:
            num, den = num * 10**exp, 1
        else:
            den = 10**-exp
        a, b = num, den  # lowest terms, as Fraction gives
        while b:
            a, b = b, a % b
        if a > 1:
            num, den = num // a, den // a
        return (-num if neg else num), den
