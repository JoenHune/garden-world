"""Unit tests for garden_world.autoreply — keyword matching + fallback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from garden_world.autoreply import DEFAULT_FALLBACK, _load_rules, match_reply


# ---------------------------------------------------------------------------
# match_reply
# ---------------------------------------------------------------------------


class TestMatchReply:
    def test_exact_keyword_match(self):
        rules = [{"keywords": ["兑换码", "码"], "reply": "今日兑换码将在19:05后推送"}]
        assert match_reply("今天的兑换码是什么", rules, "默认") == "今日兑换码将在19:05后推送"

    def test_keyword_case_insensitive(self):
        rules = [{"keywords": ["help", "帮助"], "reply": "帮助信息"}]
        assert match_reply("HELP me", rules, "默认") == "帮助信息"

    def test_fallback_no_match(self):
        rules = [{"keywords": ["hello"], "reply": "hi"}]
        assert match_reply("你好", rules, "这是默认回复") == "这是默认回复"

    def test_empty_rules(self):
        assert match_reply("anything", [], "默认回复") == "默认回复"

    def test_first_rule_wins(self):
        rules = [
            {"keywords": ["码"], "reply": "回复A"},
            {"keywords": ["码"], "reply": "回复B"},
        ]
        assert match_reply("兑换码", rules, "默认") == "回复A"

    def test_partial_keyword_match(self):
        rules = [{"keywords": ["兑换"], "reply": "匹配到兑换"}]
        assert match_reply("请问兑换码怎么用", rules, "默认") == "匹配到兑换"

    def test_empty_text(self):
        rules = [{"keywords": ["hi"], "reply": "hello"}]
        assert match_reply("", rules, "默认") == "默认"

    def test_missing_reply_key_in_rule(self):
        """Rule without 'reply' key should fall back to fallback."""
        rules = [{"keywords": ["test"]}]
        result = match_reply("test", rules, "fallback")
        assert result == "fallback"


# ---------------------------------------------------------------------------
# _load_rules
# ---------------------------------------------------------------------------


class TestLoadRules:
    def test_load_valid_config(self, tmp_path: Path):
        config = {
            "rules": [
                {"keywords": ["码"], "reply": "有码"},
            ],
            "fallback": "自定义默认回复",
        }
        (tmp_path / "autoreply.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )
        rules, fallback = _load_rules(tmp_path)
        assert len(rules) == 1
        assert fallback == "自定义默认回复"

    def test_missing_file_returns_defaults(self, tmp_path: Path):
        rules, fallback = _load_rules(tmp_path)
        assert rules == []
        assert fallback == DEFAULT_FALLBACK

    def test_invalid_json_returns_defaults(self, tmp_path: Path):
        (tmp_path / "autoreply.json").write_text("not json", encoding="utf-8")
        rules, fallback = _load_rules(tmp_path)
        assert rules == []
        assert fallback == DEFAULT_FALLBACK

    def test_missing_fallback_key(self, tmp_path: Path):
        config = {"rules": [{"keywords": ["x"], "reply": "y"}]}
        (tmp_path / "autoreply.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        rules, fallback = _load_rules(tmp_path)
        assert len(rules) == 1
        assert fallback == DEFAULT_FALLBACK
