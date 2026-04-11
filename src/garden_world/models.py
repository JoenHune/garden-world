from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TimedCodeWindow:
    number: int
    start: str
    end: str
    code: str


@dataclass
class CodeBundle:
    date_label: str
    post_title: str
    post_url: str
    weekly_code: Optional[str]
    universal_code: Optional[str]
    timed: list[TimedCodeWindow]
    # Source tracking for cross-validation
    user_id: str = ""
    nickname: str = ""
    confidence: str = "high"  # "high" (multi-source verified) or "low" (single source)
    parse_clean: bool = True  # True if parsed via primary format, False if fallback


@dataclass
class BloggerScore:
    """Multi-dimensional scoring for a trusted blogger."""
    nickname: str = ""
    success_count: int = 0
    last_seen: str = ""
    total_checks: int = 0
    valid_codes: int = 0
    # 0.0 to 1.0 — sliding average scores
    timeliness_score: float = 0.5
    reliability_score: float = 0.5
    format_score: float = 0.5
    time_window_score: float = 0.5  # does blogger include time windows?

    def trust_bonus(self) -> int:
        """Dynamic trust bonus replacing the fixed +5."""
        return int(
            self.timeliness_score * 2
            + self.reliability_score * 3
            + self.format_score * 1
            + self.time_window_score * 2
        )  # max +8

    def to_dict(self) -> dict:
        return {
            "nickname": self.nickname,
            "success_count": self.success_count,
            "last_seen": self.last_seen,
            "total_checks": self.total_checks,
            "valid_codes": self.valid_codes,
            "timeliness_score": round(self.timeliness_score, 3),
            "reliability_score": round(self.reliability_score, 3),
            "format_score": round(self.format_score, 3),
            "time_window_score": round(self.time_window_score, 3),
        }

    @staticmethod
    def from_dict(d: dict) -> "BloggerScore":
        return BloggerScore(
            nickname=d.get("nickname", ""),
            success_count=d.get("success_count", 0),
            last_seen=d.get("last_seen", ""),
            total_checks=d.get("total_checks", 0),
            valid_codes=d.get("valid_codes", 0),
            timeliness_score=d.get("timeliness_score", 0.5),
            reliability_score=d.get("reliability_score", 0.5),
            format_score=d.get("format_score", 0.5),
            time_window_score=d.get("time_window_score", 0.5),
        )
