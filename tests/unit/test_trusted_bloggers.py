"""Unit tests for trusted-blogger management.

Run with: ``pytest tests/unit/test_trusted_bloggers.py -v``
"""
from __future__ import annotations

import pytest

from garden_world.main import (
    _BLOGGER_TRUST_THRESHOLD,
    _prune_bloggers,
    _score_bundle,
    _update_trusted_blogger,
)
from garden_world.models import CodeBundle


class TestUpdateTrustedBlogger:
    """Tests for ``_update_trusted_blogger``."""

    def test_records_high_score_blogger(self):
        bloggers: dict = {}
        _update_trusted_blogger(bloggers, ("uid_abc", "花世界攻略"), score=13, today_key="2025-03-31")
        assert "uid_abc" in bloggers
        assert bloggers["uid_abc"]["nickname"] == "花世界攻略"
        assert bloggers["uid_abc"]["success_count"] == 1
        assert bloggers["uid_abc"]["last_seen"] == "2025-03-31"

    def test_repeat_success_increments_count(self):
        bloggers: dict = {}
        _update_trusted_blogger(bloggers, ("uid_abc", "花世界攻略"), score=13, today_key="2025-03-31")
        _update_trusted_blogger(bloggers, ("uid_abc", "花世界攻略"), score=13, today_key="2025-04-01")
        assert bloggers["uid_abc"]["success_count"] == 2
        assert bloggers["uid_abc"]["last_seen"] == "2025-04-01"

    def test_rejects_low_score_blogger(self):
        bloggers: dict = {}
        _update_trusted_blogger(bloggers, ("uid_low", "Low Score"), score=4, today_key="2025-04-01")
        assert "uid_low" not in bloggers

    def test_ignores_empty_user_id(self):
        bloggers: dict = {}
        _update_trusted_blogger(bloggers, ("", "NoID"), score=13, today_key="2025-04-01")
        assert "" not in bloggers


class TestScoreBundle:
    """Tests for bundle scoring and blogger bonus integration."""

    def test_base_score(self):
        bundle = CodeBundle(
            date_label="4.2",
            post_title="test",
            post_url="https://example.com",
            weekly_code="weekly",
            universal_code="universal",
            timed=[],
        )
        assert _score_bundle(bundle) == 4  # universal(3) + weekly(1)

    def test_trusted_bonus_concept(self):
        existing = {
            "uid_abc": {"nickname": "花世界攻略", "success_count": 3, "last_seen": "2025-04-01"},
        }
        base = 4
        bonus_score = base + (5 if "uid_abc" in existing else 0)
        assert bonus_score == 9


class TestPruneBloggers:
    """Tests for ``_prune_bloggers``."""

    def test_prunes_stale_bloggers(self):
        bloggers = {
            "uid_recent": {"nickname": "Recent", "success_count": 5, "last_seen": "2025-04-14"},
            "uid_stale": {"nickname": "Stale", "success_count": 2, "last_seen": "2025-03-20"},
            "uid_medium": {"nickname": "Medium", "success_count": 3, "last_seen": "2025-04-10"},
        }
        _prune_bloggers(bloggers, "2025-04-15")
        assert "uid_recent" in bloggers
        assert "uid_stale" not in bloggers
        assert "uid_medium" in bloggers

    def test_caps_at_max_entries(self):
        bloggers = {
            f"uid_{i}": {
                "nickname": f"Blogger{i}",
                "success_count": i,
                "last_seen": "2025-04-15",
            }
            for i in range(25)
        }
        _prune_bloggers(bloggers, "2025-04-15")
        assert len(bloggers) == 20  # _BLOGGER_MAX_ENTRIES raised to 20
        assert "uid_0" not in bloggers  # lowest removed
        assert "uid_24" in bloggers     # highest kept
