"""The user-run acceptance helper cannot manufacture a confirmed outcome."""

import asyncio
from pathlib import Path
import runpy

import pytest


def test_acceptance_helper_rejects_existing_data_and_never_auto_approves(tmp_path):
    module = runpy.run_path(str(Path(__file__).parents[1] / "scripts/external-agent-acceptance.py"))
    exercise = module["exercise"]
    data = tmp_path / "fresh"
    pending = asyncio.run(exercise(data, prepare=True, expected="pending"))
    assert pending["status"] == "pending" and pending["event_count"] == 0
    with pytest.raises(RuntimeError, match="observed pending; no approval is simulated"):
        asyncio.run(exercise(data, prepare=False, expected="committed"))
    before = (data / "data.db").read_bytes()
    with pytest.raises(FileExistsError):
        asyncio.run(exercise(data, prepare=True, expected="pending"))
    assert (data / "data.db").read_bytes() == before
    assert asyncio.run(exercise(data, prepare=False, expected="pending"))["event_count"] == 0
