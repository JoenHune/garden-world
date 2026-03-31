from __future__ import annotations

from dataclasses import dataclass
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
