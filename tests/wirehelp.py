"""Helpers for the wire client's tests (the fixtures ``loopback_only``, ``fake`` and
``tls_files`` are in ``conftest.py``)."""

from __future__ import annotations

import json
import ssl
from pathlib import Path

from wirefake import FakeWhisker, raw_from_record

from arthur.wire.client import WhiskerClient
from arthur.wire.net import Tls

DATA = Path(__file__).parent / "data"


def server_ssl(tls_files) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tls_files["cert"], tls_files["key"])
    return ctx


def make_client(fake: FakeWhisker, *, tls=None, tokens="fresh", **kw) -> WhiskerClient:
    if tokens == "fresh":
        tokens = fake.issue_tokens()
    kw.setdefault("ws_backoff", (0.05, 0.2))
    kw.setdefault("timeout", 5.0)
    return WhiskerClient(tls or Tls(allow_plain=True), tokens=tokens,
                         lr4_url=fake.url + "/graphql", cognito_url=fake.url + "/",
                         now_fn=fake.now, **kw)


def real_lines() -> list[dict]:
    """The state lines of ``tests/data/real-cycles.jsonl`` (real visits, logged by the spike)."""
    lines = [json.loads(x) for x in (DATA / "real-cycles.jsonl").read_text().splitlines()]
    return [x for x in lines if x.get("kind") == "state"]


def real_raws() -> list[dict]:
    """Each real line's record as full raw robot data (see ``wirefake.raw_from_record``)."""
    return [raw_from_record(x["record"]) for x in real_lines()]
