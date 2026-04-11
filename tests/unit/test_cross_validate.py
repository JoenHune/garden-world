"""Unit tests for cross-validation logic in garden_world.main."""
from __future__ import annotations

import pytest

from garden_world.main import _cross_validate
from garden_world.models import CodeBundle, TimedCodeWindow


def _make_bundle(
    universal: str | None = None,
    weekly: str | None = None,
    timed: list[tuple[int, str]] | None = None,
    user_id: str = "",
) -> CodeBundle:
    """Helper to build a CodeBundle with minimal boilerplate."""
    timed_windows = [
        TimedCodeWindow(number=n, start="20:00", end="20:15", code=code)
        for n, code in (timed or [])
    ]
    return CodeBundle(
        date_label="7.10",
        post_title="test",
        post_url="https://example.com",
        weekly_code=weekly,
        universal_code=universal,
        timed=timed_windows,
        user_id=user_id,
    )


class TestCrossValidate:
    def test_single_bundle_passthrough(self):
        b = _make_bundle(universal="春风化雨", timed=[(1, "桃李争春")])
        result = _cross_validate([b])
        assert result.universal_code == "春风化雨"
        assert result.timed[0].code == "桃李争春"

    def test_majority_vote_universal(self):
        b1 = _make_bundle(universal="春风化雨")
        b2 = _make_bundle(universal="春风化雨")
        b3 = _make_bundle(universal="错误代码")
        result = _cross_validate([b1, b2, b3])
        assert result.universal_code == "春风化雨"

    def test_single_source_low_confidence(self):
        b1 = _make_bundle(universal="唯一来源")
        result = _cross_validate([b1])
        # single bundle → first bundle returned as-is; confidence set upstream
        assert result.universal_code == "唯一来源"

    def test_majority_vote_timed_codes(self):
        b1 = _make_bundle(timed=[(1, "花开富贵"), (2, "春暖花开")])
        b2 = _make_bundle(timed=[(1, "花开富贵"), (2, "春暖花开")])
        b3 = _make_bundle(timed=[(1, "错误代码"), (2, "春暖花开")])
        result = _cross_validate([b1, b2, b3])
        codes = {t.number: t.code for t in result.timed}
        assert codes[1] == "花开富贵"
        assert codes[2] == "春暖花开"

    def test_weekly_code_majority(self):
        b1 = _make_bundle(weekly="周码正确")
        b2 = _make_bundle(weekly="周码正确")
        b3 = _make_bundle(weekly="周码错误")
        result = _cross_validate([b1, b2, b3])
        assert result.weekly_code == "周码正确"

    def test_empty_bundles_raises(self):
        with pytest.raises(ValueError):
            _cross_validate([])

    def test_two_sources_agree(self):
        b1 = _make_bundle(universal="一致代码")
        b2 = _make_bundle(universal="一致代码")
        result = _cross_validate([b1, b2])
        assert result.universal_code == "一致代码"
        # 2 votes → high confidence (not "low")
        assert result.confidence != "low"

    def test_two_sources_disagree(self):
        b1 = _make_bundle(universal="代码甲")
        b2 = _make_bundle(universal="代码乙")
        result = _cross_validate([b1, b2])
        # Each has 1 vote; most_common picks "代码甲" (first in counter)
        assert result.universal_code in ("代码甲", "代码乙")
        assert result.confidence == "low"

    def test_preserves_time_windows(self):
        b1 = _make_bundle(timed=[(1, "代码一")])
        b1.timed[0].start = "19:30"
        b1.timed[0].end = "19:45"
        b2 = _make_bundle(timed=[(1, "代码一")])
        result = _cross_validate([b1, b2])
        assert result.timed[0].start == "19:30"
        assert result.timed[0].end == "19:45"

    def test_enriches_time_windows_from_other_source(self):
        """Best bundle has codes but no time windows; another source has times."""
        b1 = _make_bundle(universal="通用码", timed=[(1, "花有重开日"), (2, "春暖花开")])
        # Remove time windows from b1
        for t in b1.timed:
            t.start = ""
            t.end = ""
        # b2 has time windows but no codes
        b2 = CodeBundle(
            date_label="7.10", post_title="test", post_url="https://example.com/2",
            weekly_code=None, universal_code=None,
            timed=[
                TimedCodeWindow(number=1, start="20:00", end="", code=""),
                TimedCodeWindow(number=2, start="21:00", end="", code=""),
            ],
        )
        result = _cross_validate([b1, b2])
        # Codes from b1 should be preserved
        codes = {t.number: t.code for t in result.timed}
        assert codes[1] == "花有重开日"
        assert codes[2] == "春暖花开"
        # Time windows from b2 should be merged in
        windows = {t.number: t.start for t in result.timed}
        assert windows[1] == "20:00"
        assert windows[2] == "21:00"
