"""``scripts/wire_check.py`` (the supervised live check) run against the fake Whisker, so the
first time it meets the real service is not the first time it runs. Always with
``--base-url`` to 127.0.0.1, a token file of the fake's, and no Whisker credentials in its
environment."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from wirefake import activity_entry

pytestmark = pytest.mark.usefixtures("loopback_only")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wire_check.py"


async def test_wire_check_against_the_fake(fake, tmp_path):
    t0 = time.time()
    fake.activities = [activity_entry(t0 - 3000, "robotStatusCatDetect"),
                       activity_entry(t0 - 2900, "catWeight", 4.67)]
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps(fake.issue_tokens()))
    env = {k: v for k, v in os.environ.items() if not k.startswith("WHISKER_")}
    assert fake.url.startswith("http://127.0.0.1:")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SCRIPT), "--base-url", fake.url, "--plain", "--token-file",
        str(token_file), "--minutes", "0.05", cwd=tmp_path, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def push_when_subscribed():
        await asyncio.wait_for(fake.subscribed.wait(), 10)
        await fake.push({"robotStatus": "ROBOT_CAT_DETECT"})

    pusher = asyncio.ensure_future(push_when_subscribed())
    out, err = await asyncio.wait_for(proc.communicate(), 30)
    await pusher
    assert proc.returncode == 0, err.decode()[-2000:]
    lines = {}
    for raw in out.decode().splitlines():
        line = json.loads(raw)
        lines.setdefault(line["check"], []).append(line)
    assert lines["renewal"][0]["ok"] and not lines["renewal"][0]["new_refresh_token"]
    assert lines["tokens"][0]["id_lifetime_s"] == 3600
    assert lines["robots_query"][0]["status"] == 200
    assert lines["robot"][0]["missing_model_keys"] == []
    assert lines["history_start"][0]["order"] == "newest first"
    assert lines["history_start"][0]["timestamp_shapes"] == ["9999-99-99T99:99:99.999Z"]
    assert lines["history_start_end"][0]["ok"]
    assert lines["update"][0]["status"] == "CAT_DETECTED"
    assert lines["update"][0]["missing_model_keys"] == []
    assert lines["socket_summary"][0]["types"]["start_ack"] == 1
    text = out.decode()
    for secret in json.loads(token_file.read_text()).values():
        assert secret not in text
    assert "USER-9" not in text and "LR4C000001" not in text
