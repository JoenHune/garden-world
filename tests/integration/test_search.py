"""Integration test: E2E search for a specific date.

Requires a live browser + authenticated XHS session.
Run with: ``pytest tests/integration/test_search.py -v -m integration``
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from zoneinfo import ZoneInfo

from garden_world.main import run


@pytest.mark.integration
def test_search_specific_date(tmp_path):
    """Full pipeline search for March 27 codes."""
    state_path = tmp_path / "state.json"
    fake = datetime(2026, 3, 27, 22, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    with (
        patch("garden_world.main._now", return_value=fake),
        patch("garden_world.config.Settings.from_env", return_value=__import__(
            "garden_world.config", fromlist=["Settings"]
        ).Settings(state_path=state_path)),
    ):
        rc = run(now_mode=True, force_refresh=True)

    assert rc in (0, 2), f"Unexpected exit code: {rc}"

    if state_path.exists():
        state = json.loads(state_path.read_text())
        assert isinstance(state, dict)
