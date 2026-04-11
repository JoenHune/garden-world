"""WeChat ClawBot client — direct HTTP communication via iLink API.

Bypasses OpenClaw's scheduling/skill layer but uses the same iLink backend
protocol that the ``@tencent-weixin/openclaw-weixin`` plugin speaks.

Capabilities:
  - QR-code login to obtain Bearer token (``start_login`` / ``wait_login``)
  - ``send_text()`` / ``send_image()`` to push messages
  - ``get_updates()`` for long-poll message reception
  - ``WeChatBridge`` multi-account manager with broadcast helpers

Account credentials are stored in ``.garden_world/wechat.json``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("garden_world.wechat")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_BOT_TYPE = "3"
MAX_ACCOUNTS = 5

QR_POLL_TIMEOUT_S = 35
LOGIN_TIMEOUT_S = 480
QR_MAX_REFRESH = 3

_SEND_TIMEOUT = 15.0
_UPLOAD_TIMEOUT = 30.0
_LONG_POLL_TIMEOUT = 40.0

# ---------------------------------------------------------------------------
# AES-128-ECB helpers (for CDN upload)
# ---------------------------------------------------------------------------

try:
    from Crypto.Cipher import AES  # pycryptodome
except ImportError:  # pragma: no cover
    AES = None  # type: ignore[assignment,misc]


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    """PKCS7 padded ciphertext length for AES-128-ECB."""
    return ((plaintext_size + 1) // 16 + 1) * 16


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    if AES is None:
        raise RuntimeError("pycryptodome is required for image sending: pip install pycryptodome")
    cipher = AES.new(key, AES.MODE_ECB)
    # PKCS7 padding
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len] * pad_len)
    return cipher.encrypt(padded)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def _random_wechat_uin() -> str:
    """X-WECHAT-UIN header: random uint32 → decimal → base64."""
    val = struct.unpack(">I", secrets.token_bytes(4))[0]
    return base64.b64encode(str(val).encode()).decode()


_ILINK_APP_ID = "bot"
_ILINK_APP_CLIENT_VERSION = str((2 << 16) | (1 << 8) | 3)  # 2.1.3 → 131331


def _build_headers(token: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": _ILINK_APP_ID,
        "iLink-App-ClientVersion": _ILINK_APP_CLIENT_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _base_info() -> dict:
    return {"channel_version": "2.1.3"}


# ---------------------------------------------------------------------------
# Account data
# ---------------------------------------------------------------------------

@dataclass
class WeChatAccount:
    """One bound WeChat ClawBot account."""
    account_id: str  # e.g. "d89b7d649a1b-im-bot"
    token: str  # Bearer token
    base_url: str = ILINK_BASE_URL
    user_id: str = ""  # the WeChat user who scanned the QR
    target_user_id: str = ""  # who to send messages to (= user_id typically)
    context_token: str = ""  # conversation context
    sync_cursor: str = ""  # getUpdates buf

    def api_url(self, endpoint: str) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/{endpoint}"


# ---------------------------------------------------------------------------
# Credential storage
# ---------------------------------------------------------------------------

_CONFIG_FILENAME = "wechat.json"


def _resolve_config_path() -> Path:
    override = os.getenv("GARDEN_WORLD_WECHAT_CONFIG")
    if override:
        return Path(override)
    return Path(os.getenv("GARDEN_WORLD_STATE_PATH", ".garden_world/state.json")).parent / _CONFIG_FILENAME


def load_accounts(config_path: Path | None = None) -> list[WeChatAccount]:
    path = config_path or _resolve_config_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text("utf-8"))
        accounts = []
        for entry in raw.get("accounts", []):
            accounts.append(WeChatAccount(
                account_id=entry["account_id"],
                token=entry["token"],
                base_url=entry.get("base_url", ILINK_BASE_URL),
                user_id=entry.get("user_id", ""),
                target_user_id=entry.get("target_user_id", ""),
                context_token=entry.get("context_token", ""),
                sync_cursor=entry.get("sync_cursor", ""),
            ))
        return accounts[:MAX_ACCOUNTS]
    except Exception:
        logger.exception("Failed to load wechat.json")
        return []


def save_accounts(accounts: list[WeChatAccount], config_path: Path | None = None) -> None:
    path = config_path or _resolve_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "accounts": [
            {
                "account_id": a.account_id,
                "token": a.token,
                "base_url": a.base_url,
                "user_id": a.user_id,
                "target_user_id": a.target_user_id,
                "context_token": a.context_token,
                "sync_cursor": a.sync_cursor,
            }
            for a in accounts[:MAX_ACCOUNTS]
        ]
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# QR Login — simulates openclaw-weixin login-qr.ts
# ---------------------------------------------------------------------------


def render_qr_terminal(url: str) -> str:
    """Render a URL as a scannable QR code using Unicode block characters.

    Returns the multi-line string. Also prints directly to stdout.
    """
    import io
    try:
        import qrcode
    except ImportError:
        logger.warning("qrcode package not installed, cannot render QR in terminal")
        return f"(请安装 qrcode: pip install qrcode)\n{url}"

    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    result = buf.getvalue()
    return result


@dataclass
class QRLoginSession:
    """Tracks an in-progress QR login."""
    qrcode: str = ""
    qrcode_url: str = ""
    started_at: float = 0.0
    status: str = "wait"
    bot_token: str = ""
    account_id: str = ""
    base_url: str = ILINK_BASE_URL
    user_id: str = ""
    current_poll_url: str = ILINK_BASE_URL


def start_qr_login(bot_type: str = DEFAULT_BOT_TYPE) -> QRLoginSession:
    """Request a new QR code from iLink and return the session.

    The returned ``qrcode_url`` can be sent to users as a scannable link.
    """
    url = f"{ILINK_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type={bot_type}"
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    return QRLoginSession(
        qrcode=data["qrcode"],
        qrcode_url=data.get("qrcode_img_content", ""),
        started_at=time.time(),
        current_poll_url=ILINK_BASE_URL,
    )


def poll_qr_status(session: QRLoginSession) -> QRLoginSession:
    """Long-poll the QR code status once. Updates session in place.

    After calling, check ``session.status``:
      - ``"wait"`` → not scanned yet
      - ``"scaned"`` → scanned, waiting user confirmation
      - ``"scaned_but_redirect"`` → IDC redirect, keep polling
      - ``"confirmed"`` → success; ``session.bot_token`` etc. populated
      - ``"expired"`` → QR expired, need refresh
    """
    base = session.current_poll_url.rstrip("/")
    url = f"{base}/ilink/bot/get_qrcode_status?qrcode={session.qrcode}"

    try:
        with httpx.Client(timeout=QR_POLL_TIMEOUT_S + 5) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        # Long-poll timeout is normal
        session.status = "wait"
        return session
    except Exception as exc:
        logger.warning("QR poll error: %s", exc)
        session.status = "wait"
        return session

    session.status = data.get("status", "wait")

    if session.status == "confirmed":
        session.bot_token = data.get("bot_token", "")
        session.account_id = data.get("ilink_bot_id", "")
        session.base_url = data.get("baseurl", ILINK_BASE_URL) or ILINK_BASE_URL
        session.user_id = data.get("ilink_user_id", "")
    elif session.status == "scaned_but_redirect":
        redirect_host = data.get("redirect_host", "")
        if redirect_host:
            session.current_poll_url = f"https://{redirect_host}"

    return session


def wait_for_login(
    session: QRLoginSession,
    timeout_s: float = LOGIN_TIMEOUT_S,
    on_status: object | None = None,  # callable(session) for UI updates
) -> QRLoginSession:
    """Block until login completes, expires, or times out.

    If ``on_status`` is callable, it's called after each poll with the
    current session so the caller can emit progress / refresh QR.
    """
    deadline = time.time() + timeout_s
    refresh_count = 0

    while time.time() < deadline:
        poll_qr_status(session)

        if callable(on_status):
            on_status(session)

        if session.status == "confirmed":
            return session

        if session.status == "expired":
            refresh_count += 1
            if refresh_count >= QR_MAX_REFRESH:
                return session  # give up
            # Refresh QR code
            try:
                new_session = start_qr_login()
                session.qrcode = new_session.qrcode
                session.qrcode_url = new_session.qrcode_url
                session.started_at = new_session.started_at
                session.status = "wait"
                session.current_poll_url = ILINK_BASE_URL
            except Exception:
                logger.exception("Failed to refresh QR")
                return session

        time.sleep(1)

    return session


def register_login_result(
    session: QRLoginSession,
    accounts: list[WeChatAccount],
    config_path: Path | None = None,
) -> WeChatAccount | None:
    """Persist a confirmed login into the account list. Returns the new account."""
    if session.status != "confirmed" or not session.bot_token:
        return None

    # Normalize account_id (ilink_bot_id format: "xxxx@im.bot" → "xxxx-im-bot")
    raw_id = session.account_id
    norm_id = raw_id.replace("@", "-").replace(".", "-") if raw_id else ""
    if not norm_id:
        return None

    # Token format: "raw_id:token_hex"
    token = f"{raw_id}:{session.bot_token}" if ":" not in session.bot_token else session.bot_token

    # Check for duplicate account (same user_id) and replace
    existing_idx = None
    for i, acct in enumerate(accounts):
        if acct.user_id and acct.user_id == session.user_id:
            existing_idx = i
            break

    new_acct = WeChatAccount(
        account_id=norm_id,
        token=token,
        base_url=session.base_url,
        user_id=session.user_id,
        target_user_id=session.user_id,
    )

    if existing_idx is not None:
        accounts[existing_idx] = new_acct
    elif len(accounts) < MAX_ACCOUNTS:
        accounts.append(new_acct)
    else:
        logger.warning("Max accounts (%d) reached, cannot add new account", MAX_ACCOUNTS)
        return None

    save_accounts(accounts, config_path)
    return new_acct


# ---------------------------------------------------------------------------
# Session-expired error codes
# ---------------------------------------------------------------------------

# ret values that indicate the bot session / token is no longer valid.
_SESSION_EXPIRED_RETS = {-14}
# ret=-2 means either "ilink_user_id required" or "no context" — treat as
# "needs context_token" rather than "session expired".
_NEEDS_CONTEXT_RETS = {-2}


def _check_send_result(data: dict, label: str) -> str:
    """Check sendMessage JSON response.

    Returns 'ok', 'needs_context', 'session_expired', or 'error'.
    """
    ret = data.get("ret", 0)
    errcode = data.get("errcode", 0)
    if ret == 0 and errcode == 0:
        return "ok"
    if ret in _SESSION_EXPIRED_RETS or errcode == -14:
        logger.warning("%s: session expired (ret=%s errcode=%s)", label, ret, errcode)
        return "session_expired"
    if ret in _NEEDS_CONTEXT_RETS:
        logger.warning("%s: context_token required (ret=%s)", label, ret)
        return "needs_context"
    logger.error("%s: API error ret=%s errcode=%s errmsg=%s",
                 label, ret, errcode, data.get("errmsg", ""))
    return "error"


# ---------------------------------------------------------------------------
# API: send_text
# ---------------------------------------------------------------------------

def send_text(account: WeChatAccount, text: str, to_user_id: str | None = None) -> str:
    """Send a text message. Returns 'ok', 'session_expired', or 'error'."""
    target = to_user_id or account.target_user_id
    if not target:
        logger.error("send_text: no target_user_id")
        return "error"

    body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": target,
            "client_id": f"gw-{secrets.token_hex(8)}",
            "message_type": 2,  # BOT
            "message_state": 2,  # FINISH
            "item_list": [{"type": 1, "text_item": {"text": text}}],
            "context_token": account.context_token or None,
        },
        "base_info": _base_info(),
    }

    try:
        with httpx.Client(timeout=_SEND_TIMEOUT) as client:
            resp = client.post(
                account.api_url("ilink/bot/sendmessage"),
                headers=_build_headers(account.token),
                json=body,
            )
            resp.raise_for_status()
            return _check_send_result(resp.json(), "send_text")
    except Exception:
        logger.exception("send_text failed to %s", target)
        return "error"


# ---------------------------------------------------------------------------
# API: send_image (CDN upload pipeline)
# ---------------------------------------------------------------------------

def send_image(account: WeChatAccount, image_path: str | Path, to_user_id: str | None = None) -> bool:
    """Upload image to WeChat CDN and send as image message. Returns True on success."""
    if AES is None:
        logger.error("send_image requires pycryptodome: pip install pycryptodome")
        return False

    target = to_user_id or account.target_user_id
    if not target:
        logger.error("send_image: no target_user_id")
        return False

    image_data = Path(image_path).read_bytes()
    raw_size = len(image_data)
    raw_md5 = hashlib.md5(image_data).hexdigest()
    cipher_size = _aes_ecb_padded_size(raw_size)
    filekey = secrets.token_hex(16)
    aes_key = secrets.token_bytes(16)

    # Step 1: getUploadUrl
    upload_body = {
        "filekey": filekey,
        "media_type": 1,  # IMAGE
        "to_user_id": target,
        "rawsize": raw_size,
        "rawfilemd5": raw_md5,
        "filesize": cipher_size,
        "no_need_thumb": True,
        "aeskey": aes_key.hex(),
        "base_info": _base_info(),
    }

    try:
        with httpx.Client(timeout=_UPLOAD_TIMEOUT) as client:
            resp = client.post(
                account.api_url("ilink/bot/getuploadurl"),
                headers=_build_headers(account.token),
                json=upload_body,
            )
            resp.raise_for_status()
            upload_resp = resp.json()
    except Exception:
        logger.exception("getUploadUrl failed")
        return False

    # Step 2: encrypt + upload to CDN
    upload_full_url = (upload_resp.get("upload_full_url") or "").strip()
    upload_param = upload_resp.get("upload_param", "")

    if upload_full_url:
        cdn_url = upload_full_url
    elif upload_param:
        cdn_url = (
            f"{CDN_BASE_URL}/upload?"
            f"encrypted_query_param={httpx.URL(upload_param)}"
            f"&filekey={filekey}"
        )
    else:
        logger.error("getUploadUrl returned no upload URL")
        return False

    ciphertext = _aes_ecb_encrypt(image_data, aes_key)

    try:
        with httpx.Client(timeout=_UPLOAD_TIMEOUT) as client:
            cdn_resp = client.post(
                cdn_url,
                content=ciphertext,
                headers={"Content-Type": "application/octet-stream"},
            )
            cdn_resp.raise_for_status()
            download_param = cdn_resp.headers.get("x-encrypted-param", "")
            if not download_param:
                logger.error("CDN response missing x-encrypted-param")
                return False
    except Exception:
        logger.exception("CDN upload failed")
        return False

    # Step 3: sendMessage with image_item
    aes_key_b64 = base64.b64encode(aes_key.hex().encode()).decode()
    send_body = {
        "msg": {
            "from_user_id": "",
            "to_user_id": target,
            "client_id": f"gw-{secrets.token_hex(8)}",
            "message_type": 2,
            "message_state": 2,
            "item_list": [{
                "type": 2,  # IMAGE
                "image_item": {
                    "media": {
                        "encrypt_query_param": download_param,
                        "aes_key": aes_key_b64,
                        "encrypt_type": 1,
                    },
                    "mid_size": cipher_size,
                },
            }],
            "context_token": account.context_token or None,
        },
        "base_info": _base_info(),
    }

    try:
        with httpx.Client(timeout=_SEND_TIMEOUT) as client:
            resp = client.post(
                account.api_url("ilink/bot/sendmessage"),
                headers=_build_headers(account.token),
                json=send_body,
            )
            resp.raise_for_status()
            return _check_send_result(resp.json(), "send_image") == "ok"
    except Exception:
        logger.exception("sendMessage (image) failed")
        return False


# ---------------------------------------------------------------------------
# API: getUpdates (long-poll)
# ---------------------------------------------------------------------------

@dataclass
class IncomingMessage:
    """Simplified inbound message."""
    from_user_id: str
    text: str
    context_token: str
    message_id: int = 0
    create_time_ms: int = 0


def get_updates(account: WeChatAccount) -> tuple[list[IncomingMessage], str]:
    """Long-poll for new messages. Returns (messages, new_cursor).

    The caller should store the returned cursor and pass it as
    ``account.sync_cursor`` on the next call.

    **context_token handling**: every message (regardless of type) that
    carries a ``context_token`` is used to update ``account.context_token``
    in-place.  This mirrors OpenClaw's approach — the token can arrive on
    any message type (subscribe events, system messages, etc.), not only
    type-1 user messages.
    """
    body = {
        "get_updates_buf": account.sync_cursor or "",
        "base_info": _base_info(),
    }

    try:
        with httpx.Client(timeout=_LONG_POLL_TIMEOUT) as client:
            resp = client.post(
                account.api_url("ilink/bot/getupdates"),
                headers=_build_headers(account.token),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        # Normal for long-poll
        return [], account.sync_cursor
    except Exception:
        logger.exception("getUpdates failed")
        return [], account.sync_cursor

    new_cursor = data.get("get_updates_buf", account.sync_cursor)

    # Capture context_token from ANY message type (system events, subscribe, etc.)
    # before filtering to user messages — same approach as OpenClaw.
    for msg in data.get("msgs", []):
        ctx = msg.get("context_token", "")
        if ctx:
            account.context_token = ctx

    messages: list[IncomingMessage] = []
    for msg in data.get("msgs", []):
        # Only return USER messages (type=1) to caller, skip BOT messages (type=2)
        if msg.get("message_type") != 1:
            continue
        items = msg.get("item_list", [])
        text_parts = []
        for item in items:
            if item.get("type") == 1 and item.get("text_item"):
                text_parts.append(item["text_item"].get("text", ""))
        if text_parts:
            messages.append(IncomingMessage(
                from_user_id=msg.get("from_user_id", ""),
                text="\n".join(text_parts),
                context_token=msg.get("context_token", ""),
                message_id=msg.get("message_id", 0),
                create_time_ms=msg.get("create_time_ms", 0),
            ))

    return messages, new_cursor


# ---------------------------------------------------------------------------
# Context-token acquisition — wait for user's first message
# ---------------------------------------------------------------------------


def wait_for_context_token(
    account: WeChatAccount,
    timeout_s: float = 120.0,
    config_path: Path | None = None,
) -> bool:
    """Block until a context_token is received from ANY getUpdates message.

    Unlike ``get_updates()`` which only returns user text messages, this
    scans ALL message types (subscribe events, system messages, etc.) so
    that a QR-scan subscribe event can provide the token without the user
    needing to send a manual message.

    Updates ``account.context_token`` and ``account.sync_cursor`` in place
    and persists to disk.  Returns True if context_token was captured.
    """
    deadline = time.time() + timeout_s
    logger.info("Waiting for context_token (timeout=%ds)…", int(timeout_s))

    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        poll_timeout = min(remaining + 5, _LONG_POLL_TIMEOUT)

        body = {
            "get_updates_buf": account.sync_cursor or "",
            "base_info": _base_info(),
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(10, read=poll_timeout)) as client:
                resp = client.post(
                    account.api_url("ilink/bot/getupdates"),
                    headers=_build_headers(account.token),
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            continue
        except Exception:
            logger.exception("Error during context_token wait")
            time.sleep(3)
            continue

        new_cursor = data.get("get_updates_buf", account.sync_cursor)
        if new_cursor != account.sync_cursor:
            account.sync_cursor = new_cursor

        # Scan ALL messages (any type) for a context_token
        for msg in data.get("msgs", []):
            ctx = msg.get("context_token", "")
            if ctx:
                account.context_token = ctx
                from_uid = msg.get("from_user_id", "?")
                msg_type = msg.get("message_type", "?")
                logger.info("Captured context_token (msg_type=%s, from=%s)", msg_type, from_uid)
                # Persist
                if config_path:
                    accounts = load_accounts(config_path)
                    for a in accounts:
                        if a.account_id == account.account_id:
                            a.context_token = ctx
                            a.sync_cursor = new_cursor
                            break
                    save_accounts(accounts, config_path)
                return True

    logger.warning("Timed out waiting for context_token")
    return False


# ---------------------------------------------------------------------------
# WeChatBridge — multi-account manager
# ---------------------------------------------------------------------------

class WeChatBridge:
    """Manages multiple WeChat accounts and provides broadcast helpers."""

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path
        self._accounts = load_accounts(config_path)
        self._session_expired = False
        self._needs_context = False

    @property
    def accounts(self) -> list[WeChatAccount]:
        return self._accounts

    @property
    def has_accounts(self) -> bool:
        return len(self._accounts) > 0

    def reload(self) -> None:
        self._accounts = load_accounts(self._config_path)

    def save(self) -> None:
        save_accounts(self._accounts, self._config_path)

    @property
    def session_expired(self) -> bool:
        """True if the last broadcast detected a session-expired error."""
        return self._session_expired

    @property
    def needs_context(self) -> bool:
        """True if the last broadcast failed because context_token is stale."""
        return self._needs_context

    def refresh_context(self, account_id: str | None = None) -> None:
        """Do a single short-poll getUpdates to refresh context_token.

        This picks up any pending messages (system events, user texts, etc.)
        and updates ``account.context_token`` in-place, then persists to disk.
        Call before sending when daemon/autoreply isn't running.
        """
        targets = self._accounts if account_id is None else [
            a for a in self._accounts if a.account_id == account_id
        ]
        for acct in targets:
            try:
                # Very short timeout — just drain whatever is already queued.
                # We do NOT want to long-poll here; 3s is enough for one round-trip.
                old_ctx = acct.context_token
                body = {
                    "get_updates_buf": acct.sync_cursor or "",
                    "base_info": _base_info(),
                }
                with httpx.Client(timeout=httpx.Timeout(3.0, read=4.0)) as client:
                    resp = client.post(
                        acct.api_url("ilink/bot/getupdates"),
                        headers=_build_headers(acct.token),
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                new_cursor = data.get("get_updates_buf", acct.sync_cursor)
                if new_cursor:
                    acct.sync_cursor = new_cursor
                # Extract context_token from ALL messages
                for msg in data.get("msgs", []):
                    ctx = msg.get("context_token", "")
                    if ctx:
                        acct.context_token = ctx
                if acct.context_token != old_ctx:
                    logger.info("Refreshed context_token for %s", acct.account_id)
                self.save()
            except Exception:
                logger.debug("refresh_context failed for %s", acct.account_id, exc_info=True)

    def broadcast_text(self, text: str) -> dict[str, bool]:
        """Send text to all accounts. Returns {account_id: success}.

        On ``needs_context`` (ret=-2), automatically refreshes the
        context_token via a single getUpdates poll and retries once.
        """
        results = {}
        for acct in self._accounts:
            result = send_text(acct, text)
            if result == "needs_context":
                logger.info("context_token stale for %s, refreshing…", acct.account_id)
                self.refresh_context(acct.account_id)
                result = send_text(acct, text)
                if result == "needs_context":
                    logger.warning(
                        "context_token still missing for %s — "
                        "user needs to send a message to the bot",
                        acct.account_id,
                    )
            if result == "session_expired":
                self._session_expired = True
            if result == "needs_context":
                self._needs_context = True
            results[acct.account_id] = (result == "ok")
        return results

    def broadcast_image(self, image_path: str | Path) -> dict[str, bool]:
        """Send image to all accounts. Returns {account_id: success}."""
        results = {}
        for acct in self._accounts:
            ok = send_image(acct, image_path)
            if not ok and not acct.context_token:
                # Might need a context refresh
                self.refresh_context(acct.account_id)
                ok = send_image(acct, image_path)
            results[acct.account_id] = ok
        return results

    def update_context_token(self, account_id: str, context_token: str) -> None:
        """Update the context_token for an account (from getUpdates)."""
        for acct in self._accounts:
            if acct.account_id == account_id:
                acct.context_token = context_token
                self.save()
                return

    def update_sync_cursor(self, account_id: str, cursor: str) -> None:
        """Update the sync cursor for an account."""
        for acct in self._accounts:
            if acct.account_id == account_id:
                acct.sync_cursor = cursor
                self.save()
                return


# ---------------------------------------------------------------------------
# Import from OpenClaw
# ---------------------------------------------------------------------------

_OPENCLAW_ACCOUNTS_DIR = Path.home() / ".openclaw" / "openclaw-weixin" / "accounts"


def import_from_openclaw(config_path: Path | None = None) -> list[WeChatAccount]:
    """Import WeChat accounts from OpenClaw's ``~/.openclaw/openclaw-weixin/accounts/``.

    Reads ``*.json`` (excluding ``*.context-tokens.json`` and ``*.sync.json``),
    merges with any existing accounts, and saves.  Returns the imported accounts.
    """
    if not _OPENCLAW_ACCOUNTS_DIR.is_dir():
        logger.info("OpenClaw accounts directory not found: %s", _OPENCLAW_ACCOUNTS_DIR)
        return []

    existing = load_accounts(config_path)
    existing_user_ids = {a.user_id for a in existing if a.user_id}
    imported: list[WeChatAccount] = []

    for f in sorted(_OPENCLAW_ACCOUNTS_DIR.glob("*.json")):
        if f.name.endswith(".context-tokens.json") or f.name.endswith(".sync.json"):
            continue
        try:
            data = json.loads(f.read_text("utf-8"))
        except Exception:
            logger.warning("Skipping unreadable file: %s", f)
            continue

        token = data.get("token", "")
        base_url = data.get("baseUrl", ILINK_BASE_URL) or ILINK_BASE_URL
        user_id = data.get("userId", "")
        if not token or not user_id:
            continue

        # Derive account_id from filename (e.g. "d89b7d649a1b-im-bot.json")
        account_id = f.stem  # "d89b7d649a1b-im-bot"

        # Try to load context_token from companion file
        context_token = ""
        ctx_file = f.parent / f"{account_id}.context-tokens.json"
        if ctx_file.exists():
            try:
                ctx_data = json.loads(ctx_file.read_text("utf-8"))
                # Format: {user_id: context_token_string}
                if isinstance(ctx_data, dict):
                    context_token = ctx_data.get(user_id, "")
            except Exception:
                pass

        # Try to load sync cursor from companion file
        sync_cursor = ""
        sync_file = f.parent / f"{account_id}.sync.json"
        if sync_file.exists():
            try:
                sync_data = json.loads(sync_file.read_text("utf-8"))
                sync_cursor = sync_data.get("get_updates_buf", "")
            except Exception:
                pass

        if user_id in existing_user_ids:
            logger.info("Account %s (user=%s) already exists, updating token", account_id, user_id)
            for acct in existing:
                if acct.user_id == user_id:
                    acct.token = token
                    acct.base_url = base_url
                    if context_token:
                        acct.context_token = context_token
                    if sync_cursor:
                        acct.sync_cursor = sync_cursor
                    break
        else:
            if len(existing) >= MAX_ACCOUNTS:
                logger.warning("Max accounts reached, skipping %s", account_id)
                continue
            acct = WeChatAccount(
                account_id=account_id,
                token=token,
                base_url=base_url,
                user_id=user_id,
                target_user_id=user_id,
                context_token=context_token,
                sync_cursor=sync_cursor,
            )
            existing.append(acct)
            imported.append(acct)
            existing_user_ids.add(user_id)
            logger.info("Imported account %s (user=%s)", account_id, user_id)

    if existing:
        save_accounts(existing, config_path)

    return imported
