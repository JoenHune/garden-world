"""Unit tests for blogger scoring in garden_world.main and models."""
from __future__ import annotations

import pytest

from garden_world.main import (
    _prune_bloggers,
    _sliding_avg,
    _update_trusted_blogger,
)
from garden_world.models import BloggerScore, CodeBundle, TimedCodeWindow


# ---------------------------------------------------------------------------
# BloggerScore model
# ---------------------------------------------------------------------------


class TestBloggerScore:
    def test_trust_bonus_default(self):
        bs = BloggerScore()
        # default 0.5 on all axes → 0.5*2 + 0.5*3 + 0.5*1 + 0.5*2 = 4
        assert bs.trust_bonus() == 4

    def test_trust_bonus_max(self):
        bs = BloggerScore(timeliness_score=1.0, reliability_score=1.0, format_score=1.0, time_window_score=1.0)
        assert bs.trust_bonus() == 8

    def test_trust_bonus_min(self):
        bs = BloggerScore(timeliness_score=0.0, reliability_score=0.0, format_score=0.0, time_window_score=0.0)
        assert bs.trust_bonus() == 0

    def test_to_dict_roundtrip(self):
        bs = BloggerScore(
            nickname="测试博主",
            success_count=5,
            last_seen="2025-01-15",
            total_checks=10,
            valid_codes=8,
            timeliness_score=0.8,
            reliability_score=0.9,
            format_score=0.7,
        )
        d = bs.to_dict()
        bs2 = BloggerScore.from_dict(d)
        assert bs2.nickname == "测试博主"
        assert bs2.success_count == 5
        assert bs2.reliability_score == 0.9
        assert bs2.format_score == 0.7

    def test_from_dict_missing_keys(self):
        bs = BloggerScore.from_dict({})
        assert bs.nickname == ""
        assert bs.success_count == 0
        assert bs.timeliness_score == 0.5


# ---------------------------------------------------------------------------
# _sliding_avg
# ---------------------------------------------------------------------------


class TestSlidingAvg:
    def test_basic(self):
        result = _sliding_avg(0.5, 1.0)
        # 0.5 * 0.7 + 1.0 * 0.3 = 0.35 + 0.3 = 0.65
        assert abs(result - 0.65) < 0.001

    def test_no_change(self):
        result = _sliding_avg(0.5, 0.5)
        assert abs(result - 0.5) < 0.001

    def test_converges_to_new(self):
        val = 0.0
        for _ in range(50):
            val = _sliding_avg(val, 1.0)
        assert val > 0.99


# ---------------------------------------------------------------------------
# _update_trusted_blogger
# ---------------------------------------------------------------------------


def _make_bundle(universal="代码", timed_codes=None, parse_clean=True):
    timed = [
        TimedCodeWindow(number=n, start="20:00", end="20:15", code=c)
        for n, c in (timed_codes or [])
    ]
    b = CodeBundle(
        date_label="7.10",
        post_title="test",
        post_url="https://example.com",
        weekly_code=None,
        universal_code=universal,
        timed=timed,
    )
    b.parse_clean = parse_clean
    return b


class TestUpdateTrustedBlogger:
    def test_new_blogger_added(self):
        bloggers = {}
        bundle = _make_bundle()
        _update_trusted_blogger(
            bloggers, ("uid1", "博主A"), 10, "2025-01-15",
            bundle=bundle, all_bundles=[bundle],
        )
        assert "uid1" in bloggers
        assert bloggers["uid1"]["nickname"] == "博主A"
        assert bloggers["uid1"]["success_count"] == 1

    def test_below_threshold_not_added(self):
        bloggers = {}
        _update_trusted_blogger(bloggers, ("uid1", "低分博主"), 2, "2025-01-15")
        assert "uid1" not in bloggers

    def test_existing_blogger_updated(self):
        bloggers = {
            "uid1": BloggerScore(
                nickname="博主A", success_count=3,
                last_seen="2025-01-14",
            ).to_dict()
        }
        bundle = _make_bundle()
        _update_trusted_blogger(
            bloggers, ("uid1", "博主A"), 12, "2025-01-15",
            bundle=bundle, all_bundles=[bundle],
        )
        assert bloggers["uid1"]["success_count"] == 4
        assert bloggers["uid1"]["last_seen"] == "2025-01-15"

    def test_empty_uid_ignored(self):
        bloggers = {}
        _update_trusted_blogger(bloggers, ("", "无ID"), 10, "2025-01-15")
        assert len(bloggers) == 0

    def test_reliability_cross_check(self):
        b1 = _make_bundle(universal="春风化雨", timed_codes=[(1, "桃李争春")])
        b1.user_id = "uid1"
        b2 = _make_bundle(universal="春风化雨", timed_codes=[(1, "桃李争春")])
        b2.user_id = "uid2"

        bloggers = {}
        _update_trusted_blogger(
            bloggers, ("uid1", "博主A"), 10, "2025-01-15",
            bundle=b1, all_bundles=[b1, b2],
        )
        # Both agree → reliability should increase from default 0.5
        bs = BloggerScore.from_dict(bloggers["uid1"])
        assert bs.reliability_score > 0.5

    def test_format_score_clean_parse(self):
        bundle = _make_bundle(parse_clean=True)
        bloggers = {}
        _update_trusted_blogger(
            bloggers, ("uid1", "博主A"), 10, "2025-01-15",
            bundle=bundle, all_bundles=[bundle],
        )
        bs = BloggerScore.from_dict(bloggers["uid1"])
        # parse_clean=True → new_format=1.0 → sliding avg moves toward 1.0
        assert bs.format_score > 0.5

    def test_format_score_dirty_parse(self):
        bundle = _make_bundle(parse_clean=False)
        bloggers = {}
        _update_trusted_blogger(
            bloggers, ("uid1", "博主A"), 10, "2025-01-15",
            bundle=bundle, all_bundles=[bundle],
        )
        bs = BloggerScore.from_dict(bloggers["uid1"])
        # parse_clean=False → new_format=0.3 → sliding avg moves toward 0.3
        assert bs.format_score < 0.5


# ---------------------------------------------------------------------------
# _prune_bloggers
# ---------------------------------------------------------------------------


class TestPruneBloggers:
    def test_stale_bloggers_removed(self):
        bloggers = {
            "old": BloggerScore(
                nickname="旧博主",
                success_count=5,
                last_seen="2025-01-01",
            ).to_dict(),
            "new": BloggerScore(
                nickname="新博主",
                success_count=3,
                last_seen="2025-01-20",
            ).to_dict(),
        }
        _prune_bloggers(bloggers, "2025-01-20")
        assert "old" not in bloggers  # 19 days > 14 → pruned
        assert "new" in bloggers

    def test_very_stale_removed(self):
        bloggers = {
            "ancient": BloggerScore(
                nickname="古老",
                success_count=1,
                last_seen="2024-12-01",
            ).to_dict(),
        }
        _prune_bloggers(bloggers, "2025-01-20")
        assert "ancient" not in bloggers

    def test_cap_at_max_entries(self):
        bloggers = {}
        for i in range(25):
            bloggers[f"uid{i}"] = BloggerScore(
                nickname=f"博主{i}",
                success_count=i,
                last_seen="2025-01-20",
            ).to_dict()
        _prune_bloggers(bloggers, "2025-01-20")
        assert len(bloggers) <= 20

    def test_lowest_composite_dropped(self):
        bloggers = {
            "low": BloggerScore(
                nickname="低分", success_count=0,
                last_seen="2025-01-20",
                reliability_score=0.0, timeliness_score=0.0, format_score=0.0,
            ).to_dict(),
            "high": BloggerScore(
                nickname="高分", success_count=100,
                last_seen="2025-01-20",
                reliability_score=1.0, timeliness_score=1.0, format_score=1.0,
            ).to_dict(),
        }
        # Add enough to exceed max
        for i in range(20):
            bloggers[f"mid{i}"] = BloggerScore(
                nickname=f"中等{i}", success_count=5,
                last_seen="2025-01-20",
            ).to_dict()
        _prune_bloggers(bloggers, "2025-01-20")
        assert "high" in bloggers
        assert "low" not in bloggers
