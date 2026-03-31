from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class Settings:
    keyword: str = "我的花园世界 兑换码"
    timezone: str = "Asia/Shanghai"
    state_path: Path = Path(".garden_world/state.json")
    profile_dir: Path = Path(".garden_world/browser_profile")
    channel: str = "wechat"
    max_candidates: int = 8

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            keyword=os.getenv("GARDEN_WORLD_KEYWORD", "我的花园世界 兑换码"),
            timezone=os.getenv("GARDEN_WORLD_TZ", "Asia/Shanghai"),
            state_path=Path(os.getenv("GARDEN_WORLD_STATE_PATH", ".garden_world/state.json")),
            profile_dir=Path(os.getenv("GARDEN_WORLD_PROFILE_DIR", ".garden_world/browser_profile")),
            channel=os.getenv("GARDEN_WORLD_CHANNEL", "wechat"),
            max_candidates=int(os.getenv("GARDEN_WORLD_MAX_CANDIDATES", "8")),
        )
