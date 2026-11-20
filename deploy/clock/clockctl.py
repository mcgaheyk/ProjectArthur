"""The clock-side half of ``scripts/clock.py``'s USB commands. Runs under the clock's own
MicroPython (the clock's shell has no grep, head, tr, sleep or df), and under CPython in the
tests. Pushed to ``/tmp/arthur-tool/`` and run from there; never stored on the flash.

    micropython clockctl.py check   ROOT AUTOSTART
    micropython clockctl.py place   STAGE ROOT
    micropython clockctl.py status  ROOT AUTOSTART
    micropython clockctl.py procs   ROOT AUTOSTART
    micropython clockctl.py unhook  AUTOSTART
    micropython clockctl.py wipe    ROOT

Prints ``R key=value`` lines for the Mac and ends with ``R done=1``; a failure prints
``R error=...`` and exits 1. It never prints what ``secrets.json`` or ``tokens.json`` hold,
only whether they exist and their mode. It touches nothing but ROOT (which must end in
``/arthur``) and the autostart file (deleted only when it carries our marker).
"""

import binascii
import hashlib
import json
import os
import sys

MARKER = b"ARTHUR-AUTOSTART"


def emit(key, value):
    print("R " + key + "=" + str(value))


def fail(msg):
    emit("error", msg)
    sys.exit(1)


def kind(path):
    """d, f, l, o or None, without following a link (MicroPython has no lstat)."""
    i = path.rfind("/")
    d, name = (path[:i] or "/"), path[i + 1:]
    if hasattr(os, "ilistdir"):
        try:
            for e in os.ilistdir(d):
                if e[0] == name:
                    t = e[1]
                    return {0x4000: "d", 0x8000: "f", 0xA000: "l"}.get(t, "o")
        except OSError:
            pass
        return None
    try:
        m = os.lstat(path).st_mode & 0o170000
    except OSError:
        return None
    return {0o040000: "d", 0o100000: "f", 0o120000: "l"}.get(m, "o")


def names(path):
    if hasattr(os, "ilistdir"):
        return [e[0] for e in os.ilistdir(path)]
    return os.listdir(path)


def check_root(root):
    if not root.endswith("/arthur") or len(root) < 9:
        fail("refusing a root that does not end in /arthur: " + root)


def autostart_state(path):
    k = kind(path)
    if k is None:
        return "absent"
    if k != "f":
        return "foreign"
    try:
        with open(path, "rb") as f:
            return "ours" if MARKER in f.read() else "foreign"
    except OSError:
        return "unreadable"


def free_bytes(path):
    st = os.statvfs(path)
    return st[4] * (st[1] or st[0])


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4096)
            if not b:
                break
            h.update(b)
    return binascii.hexlify(h.digest()).decode()


def tree(path):
    """(files, bytes) under a real folder; links are counted, never followed."""
    files = size = 0
    for n in names(path):
        p = path + "/" + n
        k = kind(p)
        if k == "d":
            f, s = tree(p)
            files += f
            size += s
        else:
            files += 1
            if k == "f":
                size += os.stat(p)[6]
    return files, size


def copy(src, dst):
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            b = fi.read(8192)
            if not b:
                break
            fo.write(b)


def mkdirs(path):
    if kind(path) == "d":
        return
    i = path.rfind("/")
    if i > 0:
        mkdirs(path[:i])
    os.mkdir(path)


def copy_tree(src, dst):
    mkdirs(dst)
    for n in names(src):
        s, d = src + "/" + n, dst + "/" + n
        k = kind(s)
        if k == "d":
            copy_tree(s, d)
        elif k == "f":
            copy(s, d)
        else:
            fail("not a file in the stage: " + s)


def remove_tree(path):
    for n in names(path):
        p = path + "/" + n
        if kind(p) == "d":
            remove_tree(p)
        else:
            os.remove(p)
    os.rmdir(path)


def our_processes(root, autostart):
    out = []
    try:
        pids = names("/proc")
    except OSError:
        return out
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open("/proc/" + pid + "/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").strip()
        except OSError:
            continue
        if b"clockctl.py" in cmd:
            continue
        if root.encode() + b"/" in cmd or autostart.encode() in cmd:
            out.append((pid, cmd.decode()))
    return out


def cmd_check(root, autostart):
    check_root(root)
    emit("autostart", autostart_state(autostart))
    emit("root", "present" if kind(root) is not None else "absent")
    parent = root[: root.rfind("/")]
    emit("state_dir", "present" if kind(parent) == "d" else "absent")
    try:
        emit("data_free_bytes", free_bytes(parent))
    except OSError as exc:
        emit("data_free_error", repr(exc))


def cmd_place(stage, root):
    """Copy the staged install into ROOT and check every file by its hash."""
    check_root(root)
    if kind(root) is not None:
        fail("already installed at " + root + " (remove first, or push over Wi-Fi)")
    with open(stage + "/expect.json") as f:
        expect = json.loads(f.read())
    mkdirs(root + "/slots/b")
    for name in ("micropython", "boot.py", "config.json"):
        copy(stage + "/" + name, root + "/" + name)
    copy_tree(stage + "/slot", root + "/slots/a")
    with open(root + "/state.json", "w") as f:
        f.write(json.dumps({"v": 1, "active": "a", "tries": {"a": 0, "b": 0}, "errors": {}}))
    bad = 0
    for rel in expect:
        if sha_file(root + "/" + rel) != expect[rel]:
            emit("mismatch", rel)
            bad += 1
    with open(root + "/slots/a/manifest.json") as f:
        man = json.loads(f.read())["files"]
    for rel in man:
        if sha_file(root + "/slots/a/" + rel) != man[rel][0]:
            emit("mismatch", "slots/a/" + rel)
            bad += 1
    emit("checked", len(expect) + len(man))
    if bad:
        fail(str(bad) + " files do not match after copying")
    files, size = tree(root)
    emit("placed_files", files)
    emit("placed_bytes", size)


def cmd_status(root, autostart):
    check_root(root)
    st = autostart_state(autostart)
    emit("autostart", st)
    if st != "absent":
        s = os.stat(autostart)
        emit("autostart.mode", f"0o{s[0] & 0o7777:o}")
        emit("autostart.uid", s[4])
    if kind(root) != "d":
        emit("root", "absent")
    else:
        emit("root", "present")
        files, size = tree(root)
        emit("root.files", files)
        emit("root.bytes", size)
        for slot in ("a", "b"):
            p = root + "/slots/" + slot
            if kind(p) == "d":
                f, s = tree(p)
                emit("slot." + slot + ".files", f)
                emit("slot." + slot + ".bytes", s)
                try:
                    with open(p + "/manifest.json", "rb") as fh:
                        emit("slot." + slot + ".bundle",
                             binascii.hexlify(hashlib.sha256(fh.read()).digest()).decode()[:16])
                except OSError:
                    emit("slot." + slot + ".bundle", "none")
        for name in ("micropython", "boot.py", "config.json"):
            p = root + "/" + name
            emit("file." + name, os.stat(p)[6] if kind(p) == "f" else "absent")
        for name in ("secrets.json", "tokens.json"):  # never their contents
            p = root + "/" + name
            if kind(p) == "f":
                emit("file." + name, f"present, mode 0o{os.stat(p)[0] & 0o777:o}")
            else:
                emit("file." + name, "absent")
        try:
            with open(root + "/state.json") as f:
                state = json.loads(f.read())
            for key in ("active", "tries", "halted", "errors"):
                if key in state:
                    emit("state." + key, json.dumps(state[key]))
        except (OSError, ValueError):
            emit("state", "unreadable")
    procs = our_processes(root, autostart)
    emit("processes", len(procs))
    for pid, cmd in procs:
        emit("process", pid + " " + cmd)
    try:
        emit("data_free_bytes", free_bytes(root[: root.rfind("/")]))
    except OSError:
        pass


def cmd_procs(root, autostart):
    procs = our_processes(root, autostart)
    emit("processes", len(procs))
    emit("pids", " ".join(p for p, _ in procs))


def cmd_unhook(autostart):
    st = autostart_state(autostart)
    emit("autostart", st)
    if st == "ours":
        os.remove(autostart)
        emit("deleted", autostart)


def cmd_wipe(root):
    check_root(root)
    k = kind(root)
    if k == "d":
        remove_tree(root)
        emit("deleted", root)
    elif k is not None:
        fail("not a folder: " + root)
    emit("root", "absent" if kind(root) is None else "present")


def main(argv):
    if len(argv) < 2:
        fail("usage: clockctl.py check|place|status|procs|unhook|wipe ...")
    cmd, args = argv[1], argv[2:]
    table = {"check": (cmd_check, 2), "place": (cmd_place, 2), "status": (cmd_status, 2),
             "procs": (cmd_procs, 2), "unhook": (cmd_unhook, 1), "wipe": (cmd_wipe, 1)}
    if cmd not in table or len(args) != table[cmd][1]:
        fail("bad command line")
    table[cmd][0](*args)
    emit("done", 1)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(type(exc).__name__ + ": " + str(exc))
