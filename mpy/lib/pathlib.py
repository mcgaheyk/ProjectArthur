"""The little of ``pathlib.Path`` the shared code uses (fonts, art, the config's default
path), for MicroPython's unix port: join with ``/``, ``resolve``, ``parent``, ``name``,
``stem``, ``exists``, ``read_text``, ``open`` and ``glob("*suffix")``. POSIX paths only.
MicroPython only."""

import os


def _norm(p):
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return ("/" if p.startswith("/") else "") + "/".join(parts)


class Path:
    def __init__(self, *parts):
        self._s = "/".join(str(p) for p in parts) if parts else "."

    def __str__(self):
        return self._s

    def __repr__(self):
        return "Path(" + repr(self._s) + ")"

    def __truediv__(self, other):
        o = str(other)
        if o.startswith("/"):
            return Path(o)
        return Path(self._s.rstrip("/") + "/" + o)

    def resolve(self):
        s = self._s
        if not s.startswith("/"):
            s = os.getcwd() + "/" + s
        return Path(_norm(s))

    @property
    def parent(self):
        s = self._s.rstrip("/")
        i = s.rfind("/")
        if i < 0:
            return Path(".")
        return Path(s[:i] or "/")

    @property
    def name(self):
        return self._s.rstrip("/").split("/")[-1]

    @property
    def stem(self):
        n = self.name
        i = n.rfind(".")
        return n[:i] if i > 0 else n

    def exists(self):
        try:
            os.stat(self._s)
            return True
        except OSError:
            return False

    def read_text(self, encoding=None, errors=None):
        # MicroPython's open() takes ``encoding`` but not ``errors``; these files are ASCII.
        with open(self._s, encoding="utf-8") as f:
            return f.read()

    def open(self, mode="r", encoding=None, errors=None):
        return open(self._s, mode) if "b" in mode else open(self._s, mode, encoding="utf-8")

    def glob(self, pattern):
        if not pattern.startswith("*"):
            raise NotImplementedError("glob: only '*suffix' patterns")
        suffix = pattern[1:]
        return [self / n for n in sorted(os.listdir(self._s)) if n.endswith(suffix)]
