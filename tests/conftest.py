"""Fixtures shared by the wire client's tests. ``loopback_only`` is not automatic: the wire
test modules ask for it with ``pytestmark``."""

from __future__ import annotations

import socket

import pytest
from wirefake import FakeWhisker, make_ca, make_server_cert

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture
def loopback_only(monkeypatch):
    """Any name lookup for a host that is not this machine fails the test: nothing here may
    reach Whisker, Amazon or anything else."""
    real = socket.getaddrinfo

    def guard(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name not in LOOPBACK:
            raise AssertionError("test tried to reach " + repr(name))
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guard)


@pytest.fixture
async def fake():
    f = FakeWhisker(ka_s=0.2)
    await f.start()
    yield f
    await f.stop()


@pytest.fixture(scope="session")
def tls_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    ca_path, ca_cert, ca_key = make_ca(d)
    cert, key = make_server_cert(d, ca_cert, ca_key)
    wrong_ca, _, _ = make_ca(d, "Some Other CA")
    return {"ca": ca_path, "cert": cert, "key": key, "wrong_ca": wrong_ca}
