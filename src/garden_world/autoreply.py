"""Auto-reply engine — template-matching responder for WeChat messages.

Runs as a background thread, long-polling ``getUpdates`` on each bound
account and replying with configured template rules or a fallback message.

Configuration lives in ``.garden_world/autoreply.json``::

    {
      "rules": [
        {"keywords": ["兑换码", "码"], "reply": "今日兑换码将在19:05后自动推送…"},
        {"keywords": ["帮助", "help"], "reply": "本机器人自动推送花园世界兑换码。"}
      ],
      "fallback": "本机器人仅用于推送花园世界兑换码，暂不处理其他消息。"
    }
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from .wechat import (
    IncomingMessage,
    WeChatAccount,
    WeChatBridge,
    get_updates,
    send_text,
)

logger = logging.getLogger("garden_world.autoreply")

# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

DEFAULT_FALLBACK = "本机器人仅用于推送花园世界兑换码，暂不处理其他消息。"


def _load_rules(config_dir: Path) -> tuple[list[dict], str]:
    """Load autoreply rules from config_dir/autoreply.json.

    Returns (rules_list, fallback_text).
    """
    path = config_dir / "autoreply.json"
    if not path.exists():
        return [], DEFAULT_FALLBACK
    try:
        data = json.loads(path.read_text("utf-8"))
        rules = data.get("rules", [])
        fallback = data.get("fallback", DEFAULT_FALLBACK)
        return rules, fallback
    except Exception:
        logger.exception("Failed to load autoreply.json")
        return [], DEFAULT_FALLBACK


def match_reply(text: str, rules: list[dict], fallback: str) -> str:
    """Match incoming text against keyword rules. Returns the reply string."""
    text_lower = text.lower()
    for rule in rules:
        keywords = rule.get("keywords", [])
        if any(kw.lower() in text_lower for kw in keywords):
            return rule.get("reply", fallback)
    return fallback


# ---------------------------------------------------------------------------
# Polling loop (runs in background thread)
# ---------------------------------------------------------------------------

class AutoReplyService:
    """Long-poll getUpdates on all accounts and auto-reply."""

    def __init__(self, bridge: WeChatBridge, config_dir: Path):
        self._bridge = bridge
        self._config_dir = config_dir
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._rules: list[dict] = []
        self._fallback: str = DEFAULT_FALLBACK

    def start(self) -> None:
        if self._running:
            return
        self._rules, self._fallback = _load_rules(self._config_dir)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="autoreply")
        self._thread.start()
        logger.info("AutoReply service started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("AutoReply service stopped")

    def reload_rules(self) -> None:
        self._rules, self._fallback = _load_rules(self._config_dir)

    def _run(self) -> None:
        while self._running:
            if not self._bridge.has_accounts:
                import time
                time.sleep(30)
                continue

            for acct in self._bridge.accounts:
                if not self._running:
                    break
                self._poll_account(acct)

    def _poll_account(self, acct: WeChatAccount) -> None:
        try:
            messages, new_cursor = get_updates(acct)

            if new_cursor != acct.sync_cursor:
                self._bridge.update_sync_cursor(acct.account_id, new_cursor)

            for msg in messages:
                self._handle_message(acct, msg)

        except Exception:
            logger.exception("AutoReply poll error for %s", acct.account_id)

    def _handle_message(self, acct: WeChatAccount, msg: IncomingMessage) -> None:
        # Update context_token if present
        if msg.context_token:
            self._bridge.update_context_token(acct.account_id, msg.context_token)

        reply = match_reply(msg.text, self._rules, self._fallback)
        logger.info(
            "Auto-reply to %s: '%s' → '%s'",
            msg.from_user_id, msg.text[:30], reply[:30],
        )
        send_text(acct, reply, to_user_id=msg.from_user_id)
