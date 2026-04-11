"""Unit tests for sanitization, report formatting, and downranking.

Run with: ``pytest tests/unit/test_report.py -v``
"""
from __future__ import annotations

from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from garden_world.main import (
    _downrank_stale_bloggers,
    _format_codes,
    _is_guanfu_post,
    _sanitize_code,
)
from garden_world.models import CodeBundle, TimedCodeWindow


def _dt(h: int, m: int) -> datetime:
    return datetime(2026, 4, 2, h, m, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


# ── _sanitize_code ──────────────────────────────────────────


class TestSanitizeCode:
    def test_pure_chinese_unchanged(self):
        assert _sanitize_code("桃月花开春意正浓") == "桃月花开春意正浓"

    def test_strips_xhs_emoji_brackets(self):
        assert _sanitize_code("桃月花开春意正浓[蹲后续H][蹲后续H") == "桃月花开春意正浓"

    def test_strips_complete_bracket_annotations(self):
        assert _sanitize_code("测试[emoji]文本") == "测试文本"

    def test_strips_english_and_symbols(self):
        assert _sanitize_code("花开abc!@#") == "花开"

    def test_empty_returns_empty(self):
        assert _sanitize_code("") == ""

    def test_none_returns_empty(self):
        assert _sanitize_code(None) == ""

    def test_only_symbols_returns_empty(self):
        assert _sanitize_code("[emoji123]!!!") == ""

    def test_mixed_content(self):
        assert _sanitize_code("春风拂面[开心R]好心情") == "春风拂面好心情"


# ── _format_codes ───────────────────────────────────────────


class TestFormatCodes:
    def test_full_report(self):
        bundle = CodeBundle(
            date_label="4.2",
            post_title="花园世界4.2兑换码",
            post_url="https://example.com/test",
            weekly_code="韶华常在花园新章",
            universal_code="桃月花开春意正浓",
            timed=[
                TimedCodeWindow(1, "20:01", "20:16", "丁香凝露含清怨"),
                TimedCodeWindow(2, "21:26", "21:41", "浅紫轻白缀碧芳"),
                TimedCodeWindow(3, "22:14", "22:29", ""),
            ],
        )
        report = _format_codes(bundle, _dt(21, 45))
        assert "🌸 花园世界兑换码 4.2" in report
        assert "✅ 周码: 韶华常在花园新章" in report
        assert "✅ 通用码: 桃月花开春意正浓" in report
        assert "⏱️ 限时码1 (20:01-20:16): 丁香凝露含清怨" in report
        assert "⏱️ 限时码2 (21:26-21:41): 浅紫轻白缀碧芳" in report
        assert "⏱️ 限时码3 (22:14-22:29): (未更新)" in report

    def test_pads_to_three(self):
        bundle = CodeBundle(
            date_label="4.2",
            post_title="test",
            post_url="https://example.com",
            weekly_code="周码",
            universal_code="通用码",
            timed=[
                TimedCodeWindow(1, "20:00", "20:15", "码一"),
            ],
        )
        report = _format_codes(bundle, _dt(19, 0))
        assert "⏱️ 限时码1" in report
        assert "⏱️ 限时码2 (未更新): (未更新)" in report
        assert "⏱️ 限时码3 (未更新): (未更新)" in report

    def test_missing_codes_show_placeholder(self):
        bundle = CodeBundle(
            date_label="4.2",
            post_title="test",
            post_url="https://example.com",
            weekly_code=None,
            universal_code=None,
            timed=[TimedCodeWindow(1, "", "", "")],
        )
        report = _format_codes(bundle, _dt(19, 0))
        assert "✅ 周码: (未更新)" in report
        assert "✅ 通用码: (未更新)" in report


# ── _downrank_stale_bloggers ────────────────────────────────


class TestDownrankStaleBloggers:
    def test_no_downrank_when_codes_present(self):
        bloggers = {
            "uid1": {"nickname": "博主A", "success_count": 3, "last_seen": "2026-04-02"},
        }
        bundle = CodeBundle(
            date_label="4.2", post_title="t", post_url="u",
            weekly_code="w", universal_code="u",
            timed=[TimedCodeWindow(1, "20:00", "20:15", "有码")],
        )
        msgs = _downrank_stale_bloggers(bloggers, bundle, _dt(20, 30), "uid1")
        assert len(msgs) == 0
        assert bloggers["uid1"]["success_count"] == 3

    def test_downrank_when_code_missing_past_window(self):
        bloggers = {
            "uid1": {"nickname": "博主A", "success_count": 3, "last_seen": "2026-04-02"},
        }
        bundle = CodeBundle(
            date_label="4.2", post_title="t", post_url="u",
            weekly_code="w", universal_code="u",
            timed=[
                TimedCodeWindow(1, "20:00", "20:15", "有码"),
                TimedCodeWindow(2, "21:00", "21:15", ""),  # missing, past window
            ],
        )
        msgs = _downrank_stale_bloggers(bloggers, bundle, _dt(21, 30), "uid1")
        assert len(msgs) == 1
        assert "降权" in msgs[0]
        assert bloggers["uid1"]["success_count"] == 2

    def test_no_downrank_when_within_grace(self):
        bloggers = {
            "uid1": {"nickname": "博主A", "success_count": 3, "last_seen": "2026-04-02"},
        }
        bundle = CodeBundle(
            date_label="4.2", post_title="t", post_url="u",
            weekly_code=None, universal_code=None,
            timed=[TimedCodeWindow(1, "20:00", "20:15", "")],
        )
        # 20:20 is within the 10-min grace after 20:15
        msgs = _downrank_stale_bloggers(bloggers, bundle, _dt(20, 20), "uid1")
        assert len(msgs) == 0

    def test_no_downrank_for_unknown_user(self):
        bloggers = {
            "uid1": {"nickname": "博主A", "success_count": 3, "last_seen": "2026-04-02"},
        }
        bundle = CodeBundle(
            date_label="4.2", post_title="t", post_url="u",
            weekly_code=None, universal_code=None,
            timed=[TimedCodeWindow(1, "20:00", "20:15", "")],
        )
        msgs = _downrank_stale_bloggers(bloggers, bundle, _dt(20, 30), "uid_unknown")
        assert len(msgs) == 0

    def test_success_count_floors_at_zero(self):
        bloggers = {
            "uid1": {"nickname": "博主A", "success_count": 1, "last_seen": "2026-04-02"},
        }
        bundle = CodeBundle(
            date_label="4.2", post_title="t", post_url="u",
            weekly_code=None, universal_code=None,
            timed=[
                TimedCodeWindow(1, "20:00", "20:15", ""),
                TimedCodeWindow(2, "21:00", "21:15", ""),
                TimedCodeWindow(3, "22:00", "22:15", ""),
            ],
        )
        msgs = _downrank_stale_bloggers(bloggers, bundle, _dt(22, 30), "uid1")
        assert bloggers["uid1"]["success_count"] == 0


# ── _is_guanfu_post ────────────────────────────────────────


class TestIsGuanfuPost:
    def test_guanfu_explicit(self):
        assert _is_guanfu_post("我的花园世界4.2兑换码（官服）\n通用码:测试") is True

    def test_no_server_marker(self):
        assert _is_guanfu_post("我的花园世界4.2兑换码\n通用码:测试") is True

    def test_zhifu_rejected(self):
        assert _is_guanfu_post("我的花园世界4.2兑换码（支服）\n限时兑换码:") is False

    def test_zhifubao_rejected(self):
        assert _is_guanfu_post("我的花园世界4.2兑换码（支付宝服）") is False

    def test_b_server_rejected(self):
        assert _is_guanfu_post("我的花园世界4.2兑换码B服\n") is False

    def test_qudao_rejected(self):
        assert _is_guanfu_post("我的花园世界兑换码 渠道服\n码:") is False

    def test_zfb_rejected(self):
        assert _is_guanfu_post("我的花园世界2026.3.28兑换码ZFB\n限时兑换码:") is False

    def test_zhifu_deep_in_text_allowed(self):
        # 支服 marker past the 300-char header area should be ignored
        text = "我的花园世界4.2兑换码\n通用码:测试\n" + "x" * 300 + "支服"
        assert _is_guanfu_post(text) is True
