"""Simulate a full day of progressive timed-code reveals.

Mocks the browser layer.  Simulates 5 cron runs:
 1. 19:05 — post found, universal code available, time windows known but codes empty
 2. 20:03 — refetch (fast path), timed code 1 now revealed
 3. 21:31 — refetch, timed code 2 revealed
 4. 22:19 — refetch, all 3 timed codes revealed
 5. 22:30 — idempotent re-run, nothing new

Run with: ``pytest tests/unit/test_progressive.py -v``
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from zoneinfo import ZoneInfo

from garden_world.browser import NoteResult
from garden_world.config import Settings
from garden_world.main import (
    _build_notifications,
    _find_today_bundle,
    _read_state,
    _write_state,
)

_NOTE_URL = "https://www.xiaohongshu.com/explore/abc123def456789012345678"

# ── Post text at different times ──

POST_19_05 = """\
3.31我的花园世界兑换码+账号福利攻略
周码（4/1日前有效）:指尖花开治愈常在

宝子们！每天19点左右更新！！！记得蹲！

今日通用 :四季轮转花事不断
限时码（1️⃣5️⃣分钟有效）请及时兑换哦～
限时码1（19:58～20:13）:
限时码2（21:26～21:41）:
限时码3（22:14～22:29）:
"""

POST_20_03 = POST_19_05.replace(
    "限时码1（19:58～20:13）:",
    "限时码1（19:58～20:13）:露珠轻颤花信已至",
)

POST_21_31 = POST_20_03.replace(
    "限时码2（21:26～21:41）:",
    "限时码2（21:26～21:41）:直播带路种花不迷",
)

POST_22_19 = POST_21_31.replace(
    "限时码3（22:14～22:29）:",
    "限时码3（22:14～22:29）:同耕一方共享花开",
)


def _fake_dt(h: int, m: int) -> datetime:
    return datetime(2026, 3, 31, h, m, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


# ── Fixtures ──

@pytest.fixture()
def env(tmp_path):
    """Create a temporary state dir + Settings for each test run."""
    state_path = tmp_path / "state.json"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / ".logged_in").write_text("1")
    settings = Settings(state_path=state_path, profile_dir=profile_dir)
    return settings, state_path


def _run_cycle(settings, state, now, *, search_results=None, fetch_text=None):
    with (
        patch("garden_world.main.search_and_fetch", return_value=search_results or []),
        patch("garden_world.main.fetch_note", return_value=fetch_text),
    ):
        bundle = _find_today_bundle(settings, now, state)
        if not bundle:
            return None, []
        notes = _build_notifications(bundle, state, now)
        return bundle, notes


# ── Tests ──

def test_full_progressive_day(env):
    """Five cron runs throughout a day, verifying incremental code reveals."""
    settings, state_path = env

    # ── Run 1: 19:05 — first discovery ──
    state = _read_state(state_path)
    bundle, notes = _run_cycle(
        settings, state, _fake_dt(19, 5),
        search_results=[
            NoteResult(url=_NOTE_URL, text=POST_19_05, user_id="uid_test", nickname="TestBlogger"),
        ],
    )
    _write_state(state_path, state)

    assert bundle is not None
    assert bundle.universal_code == "四季轮转花事不断"
    assert len(bundle.timed) == 3
    assert all(t.code == "" for t in bundle.timed)
    assert any("通用码" in n for n in notes)
    assert not any("限时码" in n for n in notes)
    assert state["cached_post_url"] == _NOTE_URL

    # ── Run 2: 20:03 — timed code 1 revealed ──
    state = _read_state(state_path)
    bundle, notes = _run_cycle(
        settings, state, _fake_dt(20, 3),
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_20_03),
    )
    _write_state(state_path, state)

    assert bundle.timed[0].code == "露珠轻颤花信已至"
    assert bundle.timed[1].code == ""
    assert any("限时码1" in n for n in notes)
    assert not any("限时码2" in n for n in notes)
    assert not any("通用码" in n for n in notes)

    # ── Run 3: 21:31 — timed code 2 revealed ──
    state = _read_state(state_path)
    bundle, notes = _run_cycle(
        settings, state, _fake_dt(21, 31),
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_21_31),
    )
    _write_state(state_path, state)

    assert bundle.timed[1].code == "直播带路种花不迷"
    assert any("限时码2" in n for n in notes)
    assert not any("限时码1" in n for n in notes)

    # ── Run 4: 22:19 — timed code 3 revealed ──
    state = _read_state(state_path)
    bundle, notes = _run_cycle(
        settings, state, _fake_dt(22, 19),
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_22_19),
    )
    _write_state(state_path, state)

    assert bundle.timed[2].code == "同耕一方共享花开"
    assert any("限时码3" in n for n in notes)
    assert not any("限时码1" in n for n in notes)

    # ── Run 5: 22:30 — idempotent re-run ──
    state = _read_state(state_path)
    bundle, notes = _run_cycle(
        settings, state, _fake_dt(22, 30),
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_22_19),
    )
    _write_state(state_path, state)

    assert bundle is not None
    assert len(notes) == 0

    # ── Verify final state ──
    final = _read_state(state_path)
    today_sent = final["sent"]["2026-03-31"]
    assert today_sent["universal"] == "四季轮转花事不断"
    assert today_sent["timed_1"] == "露珠轻颤花信已至"
    assert today_sent["timed_2"] == "直播带路种花不迷"
    assert today_sent["timed_3"] == "同耕一方共享花开"

    # Trusted blogger recorded from Run 1
    bloggers = final.get("trusted_bloggers", {})
    assert "uid_test" in bloggers
    assert bloggers["uid_test"]["nickname"] == "TestBlogger"
