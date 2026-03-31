"""Simulate a full day of progressive timed-code reveals.

Mock the browser layer.  Simulate 4 cron runs:
 1. 19:05 — post found, universal code available, time windows known but codes empty
 2. 20:03 — refetch (fast path), timed code 1 now revealed
 3. 21:31 — refetch, timed code 2 revealed
 4. 22:19 — refetch, all 3 timed codes revealed
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from garden_world.main import (
    _build_notifications,
    _find_today_bundle,
    _parse_codes,
    _read_state,
    _write_state,
)
from garden_world.browser import NoteResult
from garden_world.config import Settings

_NOTE_URL = "https://www.xiaohongshu.com/explore/abc123def456789012345678"

# ── Post text at different times ──

POST_19_05 = """
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


def _fake_dt(h, m):
    return datetime(2026, 3, 31, h, m, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _run_cycle(settings, state, now, search_results=None, fetch_text=None):
    """Simulate one cron cycle.

    If search_results is given, mock search_and_fetch.
    If fetch_text is given, mock fetch_note for fast path.
    """
    with (
        patch(
            "garden_world.main.search_and_fetch",
            return_value=search_results or [],
        ),
        patch(
            "garden_world.main.fetch_note",
            return_value=fetch_text,
        ),
    ):
        bundle = _find_today_bundle(settings, now, state)
        if not bundle:
            return None, []
        notes = _build_notifications(bundle, state, now)
        return bundle, notes


def main():
    tmpdir = Path(tempfile.mkdtemp())
    state_path = tmpdir / "state.json"
    profile_dir = tmpdir / "profile"
    profile_dir.mkdir()
    (profile_dir / ".logged_in").write_text("1")

    settings = Settings(
        state_path=state_path,
        profile_dir=profile_dir,
    )

    state = _read_state(state_path)
    all_ok = True

    # ── Run 1: 19:05 ─────────────────────────────────────────────
    print("═══ Run 1: 19:05 — first discovery ═══")
    now = _fake_dt(19, 5)
    bundle, notes = _run_cycle(
        settings, state, now,
        search_results=[NoteResult(url=_NOTE_URL, text=POST_19_05, user_id="uid_test", nickname="TestBlogger")],
    )
    _write_state(state_path, state)

    assert bundle is not None, "Run 1: bundle should exist"
    assert bundle.universal_code == "四季轮转花事不断", "Run 1: universal code"
    assert len(bundle.timed) == 3, "Run 1: should have 3 time windows"
    assert all(t.code == "" for t in bundle.timed), "Run 1: all timed codes empty"

    # universal pushed, timed NOT pushed (codes empty + not past window)
    assert any("通用码" in n for n in notes), "Run 1: universal notification"
    assert not any("限时码" in n for n in notes), "Run 1: no timed notifications"

    # URL cached
    assert state["cached_post_url"] == _NOTE_URL
    assert state["cached_date"] == "2026-03-31"

    print(f"  bundle: universal={bundle.universal_code}")
    print(f"  windows: {[(t.number, t.start, t.end, repr(t.code)) for t in bundle.timed]}")
    print(f"  notifications ({len(notes)}): {notes}")
    print("  ✓ PASS\n")

    # ── Run 2: 20:03 — timed code 1 revealed ─────────────────────
    print("═══ Run 2: 20:03 — timed code 1 revealed ═══")
    state = _read_state(state_path)
    now = _fake_dt(20, 3)
    bundle, notes = _run_cycle(
        settings, state, now,
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_20_03),   # fast path: fetch cached URL
    )
    _write_state(state_path, state)

    assert bundle is not None, "Run 2: bundle should exist"
    assert bundle.timed[0].code == "露珠轻颤花信已至", "Run 2: timed code 1"
    assert bundle.timed[1].code == "", "Run 2: timed code 2 still empty"
    assert bundle.timed[2].code == "", "Run 2: timed code 3 still empty"

    assert any("限时码1" in n for n in notes), "Run 2: push timed code 1"
    assert not any("限时码2" in n for n in notes), "Run 2: no timed code 2"
    assert not any("通用码" in n for n in notes), "Run 2: universal not re-pushed"

    print(f"  windows: {[(t.number, repr(t.code)) for t in bundle.timed]}")
    print(f"  notifications ({len(notes)}): {notes}")
    print("  ✓ PASS\n")

    # ── Run 3: 21:31 — timed code 2 revealed ─────────────────────
    print("═══ Run 3: 21:31 — timed code 2 revealed ═══")
    state = _read_state(state_path)
    now = _fake_dt(21, 31)
    bundle, notes = _run_cycle(
        settings, state, now,
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_21_31),
    )
    _write_state(state_path, state)

    assert bundle is not None, "Run 3: bundle should exist"
    assert bundle.timed[0].code == "露珠轻颤花信已至"
    assert bundle.timed[1].code == "直播带路种花不迷"
    assert bundle.timed[2].code == "", "Run 3: timed code 3 still empty"

    assert any("限时码2" in n for n in notes), "Run 3: push timed code 2"
    assert not any("限时码1" in n for n in notes), "Run 3: code 1 not re-pushed"
    assert not any("限时码3" in n for n in notes), "Run 3: code 3 not yet"

    print(f"  windows: {[(t.number, repr(t.code)) for t in bundle.timed]}")
    print(f"  notifications ({len(notes)}): {notes}")
    print("  ✓ PASS\n")

    # ── Run 4: 22:19 — timed code 3 revealed ─────────────────────
    print("═══ Run 4: 22:19 — timed code 3 revealed ═══")
    state = _read_state(state_path)
    now = _fake_dt(22, 19)
    bundle, notes = _run_cycle(
        settings, state, now,
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_22_19),
    )
    _write_state(state_path, state)

    assert bundle is not None, "Run 4: bundle should exist"
    assert bundle.timed[2].code == "同耕一方共享花开"

    assert any("限时码3" in n for n in notes), "Run 4: push timed code 3"
    assert not any("限时码1" in n for n in notes), "Run 4: code 1 not re-pushed"
    assert not any("限时码2" in n for n in notes), "Run 4: code 2 not re-pushed"

    print(f"  windows: {[(t.number, repr(t.code)) for t in bundle.timed]}")
    print(f"  notifications ({len(notes)}): {notes}")
    print("  ✓ PASS\n")

    # ── Run 5: 22:30 — re-run, nothing new ───────────────────────
    print("═══ Run 5: 22:30 — idempotent re-run ═══")
    state = _read_state(state_path)
    now = _fake_dt(22, 30)
    bundle, notes = _run_cycle(
        settings, state, now,
        fetch_text=NoteResult(url=_NOTE_URL, text=POST_22_19),
    )
    _write_state(state_path, state)

    assert bundle is not None, "Run 5: bundle"
    assert len(notes) == 0, f"Run 5: no new notifications, got {notes}"

    print(f"  notifications ({len(notes)}): {notes}")
    print("  ✓ PASS\n")

    # ── Final state check ─────────────────────────────────────────
    print("═══ Final state ═══")
    final_state = _read_state(state_path)
    today_sent = final_state["sent"]["2026-03-31"]
    print(f"  sent: {json.dumps(today_sent, ensure_ascii=False, indent=4)}")

    assert today_sent.get("universal") == "四季轮转花事不断"
    assert today_sent.get("timed_1") == "露珠轻颤花信已至"
    assert today_sent.get("timed_2") == "直播带路种花不迷"
    assert today_sent.get("timed_3") == "同耕一方共享花开"

    # Trusted blogger should have been recorded from Run 1
    bloggers = final_state.get("trusted_bloggers", {})
    assert "uid_test" in bloggers, f"Run 1 blogger should be tracked: {bloggers}"
    assert bloggers["uid_test"]["nickname"] == "TestBlogger"
    print(f"  trusted_bloggers: {json.dumps(bloggers, ensure_ascii=False)}")

    print("  ✓ PASS\n")
    print("═══ ALL PROGRESSIVE TESTS PASSED ═══")

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
