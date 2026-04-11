"""Unit tests for garden_world.wechat — API client, login, and bridge."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from garden_world.wechat import (
    MAX_ACCOUNTS,
    WeChatAccount,
    WeChatBridge,
    IncomingMessage,
    QRLoginSession,
    _aes_ecb_padded_size,
    _build_headers,
    _random_wechat_uin,
    load_accounts,
    poll_qr_status,
    register_login_result,
    save_accounts,
    send_text,
    start_qr_login,
)


# ---------------------------------------------------------------------------
# AES helpers
# ---------------------------------------------------------------------------


def test_aes_ecb_padded_size():
    # PKCS7: ((n+1)//16 + 1) * 16
    assert _aes_ecb_padded_size(0) == 16
    assert _aes_ecb_padded_size(1) == 16
    assert _aes_ecb_padded_size(15) == 32  # 15 bytes + 1 byte padding = 16 → next block
    assert _aes_ecb_padded_size(16) == 32
    assert _aes_ecb_padded_size(31) == 48
    assert _aes_ecb_padded_size(32) == 48


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def test_random_wechat_uin_format():
    """UIN should be base64-encoded decimal string."""
    import base64
    uin = _random_wechat_uin()
    decoded = base64.b64decode(uin).decode()
    assert decoded.isdigit()


def test_build_headers_with_token():
    headers = _build_headers("test-token-123")
    assert headers["Authorization"] == "Bearer test-token-123"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["Content-Type"] == "application/json"
    assert "X-WECHAT-UIN" in headers


def test_build_headers_without_token():
    headers = _build_headers(None)
    assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# Account persistence
# ---------------------------------------------------------------------------


def test_save_and_load_accounts(tmp_path: Path):
    accounts = [
        WeChatAccount(
            account_id="test-id-1",
            token="token-1",
            base_url="https://example.com",
            user_id="user-1",
            target_user_id="user-1",
        ),
        WeChatAccount(
            account_id="test-id-2",
            token="token-2",
            user_id="user-2",
            target_user_id="user-2",
        ),
    ]
    config_path = tmp_path / "wechat.json"
    save_accounts(accounts, config_path)

    loaded = load_accounts(config_path)
    assert len(loaded) == 2
    assert loaded[0].account_id == "test-id-1"
    assert loaded[0].token == "token-1"
    assert loaded[0].base_url == "https://example.com"
    assert loaded[1].account_id == "test-id-2"


def test_load_accounts_missing_file(tmp_path: Path):
    assert load_accounts(tmp_path / "nonexistent.json") == []


def test_max_accounts_enforced(tmp_path: Path):
    accounts = [
        WeChatAccount(account_id=f"id-{i}", token=f"tok-{i}")
        for i in range(MAX_ACCOUNTS + 3)
    ]
    config_path = tmp_path / "wechat.json"
    save_accounts(accounts, config_path)

    loaded = load_accounts(config_path)
    assert len(loaded) == MAX_ACCOUNTS


# ---------------------------------------------------------------------------
# QR Login
# ---------------------------------------------------------------------------


def test_start_qr_login_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "qrcode": "test-qr-code",
        "qrcode_img_content": "https://example.com/qr.png",
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("garden_world.wechat.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_resp

        session = start_qr_login()
        assert session.qrcode == "test-qr-code"
        assert session.qrcode_url == "https://example.com/qr.png"
        assert session.status == "wait"


def test_poll_qr_status_confirmed():
    session = QRLoginSession(
        qrcode="test-qr",
        started_at=1000,
        current_poll_url="https://ilinkai.weixin.qq.com",
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "confirmed",
        "bot_token": "abc123",
        "ilink_bot_id": "test@im.bot",
        "baseurl": "https://somewhere.qq.com",
        "ilink_user_id": "user@im.wechat",
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("garden_world.wechat.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_resp

        poll_qr_status(session)
        assert session.status == "confirmed"
        assert session.bot_token == "abc123"
        assert session.account_id == "test@im.bot"
        assert session.user_id == "user@im.wechat"


def test_poll_qr_status_redirect():
    session = QRLoginSession(
        qrcode="test-qr",
        started_at=1000,
        current_poll_url="https://ilinkai.weixin.qq.com",
    )

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "scaned_but_redirect",
        "redirect_host": "other.weixin.qq.com",
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("garden_world.wechat.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.get.return_value = mock_resp

        poll_qr_status(session)
        assert session.status == "scaned_but_redirect"
        assert session.current_poll_url == "https://other.weixin.qq.com"


# ---------------------------------------------------------------------------
# Register login result
# ---------------------------------------------------------------------------


def test_register_login_result_new_account(tmp_path: Path):
    session = QRLoginSession(
        status="confirmed",
        bot_token="abc123",
        account_id="test@im.bot",
        base_url="https://somewhere.qq.com",
        user_id="user@im.wechat",
    )
    accounts = []
    config = tmp_path / "wechat.json"

    acct = register_login_result(session, accounts, config_path=config)
    assert acct is not None
    assert acct.account_id == "test-im-bot"
    assert acct.user_id == "user@im.wechat"
    assert len(accounts) == 1


def test_register_login_result_replaces_same_user(tmp_path: Path):
    existing = WeChatAccount(
        account_id="old-id",
        token="old-token",
        user_id="user@im.wechat",
    )
    accounts = [existing]
    session = QRLoginSession(
        status="confirmed",
        bot_token="new-token",
        account_id="new@im.bot",
        base_url="https://example.com",
        user_id="user@im.wechat",
    )
    config = tmp_path / "wechat.json"

    acct = register_login_result(session, accounts, config_path=config)
    assert acct is not None
    assert len(accounts) == 1  # replaced, not appended
    assert accounts[0].token.endswith("new-token")


def test_register_login_result_rejects_unconfirmed():
    session = QRLoginSession(status="expired")
    result = register_login_result(session, [])
    assert result is None


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


def test_send_text_success():
    acct = WeChatAccount(
        account_id="test",
        token="tok",
        target_user_id="target-user",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ret": 0}

    with patch("garden_world.wechat.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.post.return_value = mock_resp

        assert send_text(acct, "Hello!") == "ok"

        call_args = instance.post.call_args
        body = call_args[1]["json"]
        assert body["msg"]["to_user_id"] == "target-user"
        assert body["msg"]["item_list"][0]["text_item"]["text"] == "Hello!"


def test_send_text_no_target():
    acct = WeChatAccount(account_id="test", token="tok")
    assert send_text(acct, "Hello!") == "error"


# ---------------------------------------------------------------------------
# WeChatBridge
# ---------------------------------------------------------------------------


def test_bridge_broadcast_text(tmp_path: Path):
    accounts = [
        WeChatAccount(account_id="a1", token="t1", target_user_id="u1"),
        WeChatAccount(account_id="a2", token="t2", target_user_id="u2"),
    ]
    config = tmp_path / "wechat.json"
    save_accounts(accounts, config)

    bridge = WeChatBridge(config_path=config)
    assert bridge.has_accounts
    assert len(bridge.accounts) == 2

    with patch("garden_world.wechat.send_text", return_value="ok") as mock_send:
        results = bridge.broadcast_text("test message")
        assert results == {"a1": True, "a2": True}
        assert mock_send.call_count == 2


def test_bridge_no_accounts(tmp_path: Path):
    bridge = WeChatBridge(config_path=tmp_path / "nonexistent.json")
    assert not bridge.has_accounts
    assert bridge.broadcast_text("test") == {}
