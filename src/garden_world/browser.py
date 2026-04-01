"""Playwright-based browser automation for Xiaohongshu.

Uses a **persistent browser context** (user-data-dir) so that all browser
state — cookies, localStorage, IndexedDB, cache, service workers — is
preserved between the interactive ``login()`` session and subsequent
headless ``search_and_fetch()`` / ``fetch_note()`` runs.
"""
from __future__ import annotations

import base64 as _b64
import json as _json
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple, Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

class NoteResult(NamedTuple):
    """Single fetched XHS note with optional author info."""
    url: str
    text: str
    user_id: str = ""
    nickname: str = ""


_XHS_BASE = "https://www.xiaohongshu.com"
_XHS_SEARCH_URL = (
    _XHS_BASE
    + "/search_result?keyword={keyword}&source=web_search_result_notes"
)
_XHS_NOTE_URL = _XHS_BASE + "/explore/{note_id}"

# Timeouts
_NAV_TIMEOUT = 30_000      # page navigation
_LOGIN_TIMEOUT = 120_000   # max wait for QR login (2 min)
_SEARCH_WAIT = 6           # seconds — fallback AJAX wait


class AuthRequired(Exception):
    """Raised when no valid auth state / profile is available."""


# ---------------------------------------------------------------------------
# Persistent-context launcher
# ---------------------------------------------------------------------------

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = { runtime: {} };
"""


def _open_persistent(
    pw,
    profile_dir: Path,
    *,
    headless: bool,
    block_resources: bool = True,
) -> BrowserContext:
    """Open (or create) a persistent Chromium context at *profile_dir*.

    *block_resources* — when True **and** headless, heavy resources
    (images, fonts, video) are blocked to speed up scraping.  Set to
    False for the login flow so the QR-code image can render.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx.add_init_script(_STEALTH_JS)
    if headless and block_resources:
        _block_heavy_resources(ctx)
    return ctx


def _block_heavy_resources(ctx: BrowserContext) -> None:
    ctx.route(
        re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|woff2?|ttf|mp4|mp3)(\?|$)", re.I),
        lambda route: route.abort(),
    )


# ---------------------------------------------------------------------------
# Login flow (headed, interactive)
# ---------------------------------------------------------------------------

def login(profile_dir: Path, *, headless: bool = False) -> bool:
    """Open a browser for the user to scan XHS QR code.

    When *headless* is True (recommended for QClaw / remote), the browser
    runs without a visible window.  The QR code is captured as a
    screenshot and emitted via ``QR_IMAGE:`` (file path) and
    ``QR_BASE64:`` (inline PNG data) so the caller can relay it to the
    end-user.

    Strategy (closed-loop, self-verifying):
      1. Open the XHS **search page** — it shows a login wall overlay.
      2. Screenshot the **entire login wall** for remote/QClaw push.
      3. Poll until "登录后查看搜索结果" disappears AND explore links
         appear — this is the only reliable success signal.
    """
    print("LOGIN_STARTING: 正在启动浏览器，请稍候…", flush=True)

    with sync_playwright() as pw:
        ctx = _open_persistent(pw, profile_dir, headless=headless, block_resources=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            print("LOGIN_STARTING: 浏览器已启动，正在加载登录页面…", flush=True)

            # Go to search page — login wall appears on top
            search_url = _XHS_SEARCH_URL.format(keyword="花园世界 兑换码")
            page.goto(search_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            time.sleep(3)

            # --- Screenshot the full login wall for QClaw ---
            qr_path = profile_dir / "qr.png"
            _screenshot_login_wall(page, qr_path)

            # Flush immediately so QClaw poll can see QR_IMAGE/QR_BASE64
            # BEFORE the blocking scan-wait loop below.
            print("LOGIN_WAIT: 请用小红书 App 或微信扫描上方二维码登录。", flush=True)
            print("LOGIN_WAIT: 登录成功后将自动验证并关闭浏览器（超时2分钟）。", flush=True)

            # --- Poll: wait for search results to actually appear ---
            deadline = time.time() + _LOGIN_TIMEOUT / 1000
            logged_in = False
            while time.time() < deadline:
                time.sleep(3)
                try:
                    body = page.inner_text("body")
                    if "登录后查看" in body:
                        continue
                    explore_links = page.eval_on_selector_all(
                        'a[href*="/explore/"]',
                        "els => els.map(el => el.href)",
                    )
                    if len(explore_links) > 0:
                        logged_in = True
                        break
                    # Login wall gone but no links yet — refresh
                    page.goto(search_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                    time.sleep(4)
                except Exception:
                    continue

            if not logged_in:
                print("LOGIN_FAIL: 登录超时（2分钟内未检测到搜索结果）", flush=True)
                return False

            # Write marker
            marker = profile_dir / ".logged_in"
            marker.write_text("1")

            print(f"LOGIN_OK: 登录成功！浏览器配置已保存到 {profile_dir}", flush=True)
            return True

        finally:
            ctx.close()


def _screenshot_login_wall(page: Page, dest: Path) -> None:
    """Screenshot the login wall and emit both file path and base64 data.

    Outputs (flushed immediately so QClaw ``poll`` can see them):
      - ``QR_IMAGE: <absolute path>``
      - ``QR_BASE64: <png base64 string>``
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    captured = False
    try:
        wall = page.query_selector(
            '[class*="login-container"], [class*="login-modal"], '
            '[class*="overlay"], [class*="mask"]'
        )
        if wall and wall.is_visible():
            wall.screenshot(path=str(dest))
            captured = True
    except Exception:
        pass

    if not captured:
        try:
            page.screenshot(path=str(dest))
            captured = True
        except Exception:
            pass

    if captured:
        print(f"QR_IMAGE: {dest}", flush=True)
        try:
            b64_str = _b64.b64encode(dest.read_bytes()).decode()
            print(f"QR_BASE64: {b64_str}", flush=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------

def _ensure_auth(profile_dir: Path) -> None:
    marker = profile_dir / ".logged_in"
    if not profile_dir.exists() or not marker.exists():
        raise AuthRequired(
            f"未找到登录凭证 ({profile_dir})。"
            "请先运行 garden-world login 进行小红书扫码登录。"
        )


# ---------------------------------------------------------------------------
# Search & Fetch (headless, authenticated)
# ---------------------------------------------------------------------------

def search_and_fetch(
    keyword: str,
    date_hint: str,
    limit: int = 8,
    *,
    profile_dir: Path,
) -> list[tuple[str, str]]:
    """Search XHS and fetch note text, using the persistent browser profile.

    Returns list of :class:`NoteResult` with url, text, user_id, nickname.
    """
    _ensure_auth(profile_dir)
    results: list[NoteResult] = []

    with sync_playwright() as pw:
        ctx = _open_persistent(pw, profile_dir, headless=True)
        try:
            page = ctx.new_page()

            # --- Search ---
            search_kw = f"{keyword} {date_hint}" if date_hint else keyword
            page.goto(
                _XHS_SEARCH_URL.format(keyword=search_kw),
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT,
            )

            try:
                page.wait_for_selector(
                    'section.note-item, a[href*="/explore/"], [data-v][class*="note"]',
                    timeout=_NAV_TIMEOUT,
                )
            except Exception:
                time.sleep(_SEARCH_WAIT)

            _dismiss_overlays(page)

            # Still on login wall?
            body_text = page.inner_text("body")
            if "登录后查看搜索结果" in body_text:
                raise AuthRequired(
                    "登录凭证已过期，请重新运行 garden-world login。"
                )

            # Collect note IDs
            links = page.eval_on_selector_all(
                'a[href*="/explore/"]',
                "els => els.map(el => el.href)",
            )

            note_ids: list[str] = []
            seen: set[str] = set()
            for href in links:
                m = re.search(r"/explore/([a-f0-9]{24})", href)
                if not m:
                    continue
                nid = m.group(1)
                if nid in seen:
                    continue
                seen.add(nid)
                note_ids.append(nid)
                if len(note_ids) >= limit:
                    break

            # --- Fetch each note ---
            for nid in note_ids:
                note_url = _XHS_NOTE_URL.format(note_id=nid)
                try:
                    page.goto(
                        note_url,
                        wait_until="domcontentloaded",
                        timeout=_NAV_TIMEOUT,
                    )
                    try:
                        page.wait_for_selector(
                            '#detail-title, [class*="note-text"], [class*="title"]',
                            timeout=15_000,
                        )
                    except Exception:
                        time.sleep(2)

                    _dismiss_overlays(page)
                    nd = _extract_note_data(page)
                    if nd.text and len(nd.text) > 20:
                        results.append(NoteResult(
                            url=note_url,
                            text=nd.text,
                            user_id=nd.user_id,
                            nickname=nd.nickname,
                        ))
                except Exception:
                    continue

        finally:
            ctx.close()

    return results


def fetch_note(note_url: str, *, profile_dir: Path) -> Optional[NoteResult]:
    """Re-fetch a single XHS note page and return a :class:`NoteResult`.

    Used by the cron loop to re-check a cached post for newly-revealed
    timed codes without repeating the full search.
    """
    _ensure_auth(profile_dir)

    with sync_playwright() as pw:
        ctx = _open_persistent(pw, profile_dir, headless=True)
        try:
            page = ctx.new_page()
            page.goto(note_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            try:
                page.wait_for_selector(
                    '#detail-title, [class*="note-text"], [class*="title"]',
                    timeout=15_000,
                )
            except Exception:
                time.sleep(2)

            _dismiss_overlays(page)
            nd = _extract_note_data(page)
            if nd.text and len(nd.text) > 20:
                return NoteResult(
                    url=note_url,
                    text=nd.text,
                    user_id=nd.user_id,
                    nickname=nd.nickname,
                )
            return None
        except Exception:
            return None
        finally:
            ctx.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dismiss_overlays(page: Page) -> None:
    """Try to close common XHS popups / login dialogs."""
    for sel in [
        'button.close-button',
        '[class*="close"]',
        '[class*="modal"] button',
        '.login-mask',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(0.3)
        except Exception:
            pass


def _extract_note_text(page: Page) -> str:
    """Extract title + body text from a rendered XHS note page.

    Strategy (in priority order):
      1. Parse ``window.__INITIAL_STATE__`` from a ``<script>`` tag — this is
         SSR data that XHS always embeds, even when anti-bot measures prevent
         the SPA from rendering the note content in the DOM.
      2. Fall back to DOM selectors (works on some note types / headed mode).
    """
    ssr_data = _parse_ssr(page)

    # --- Strategy 1: SSR JSON ---
    if ssr_data:
        text = _text_from_ssr(ssr_data)
        if text:
            return text

    # --- Strategy 2: DOM selectors (fallback) ---
    parts: list[str] = []

    for sel in ["#detail-title", '[class*="title"][class*="note"]', "h1"]:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                parts.append(t)
                break

    for sel in [
        "#detail-desc",
        '[class*="note-text"]',
        '[class*="desc"]',
        "article",
    ]:
        el = page.query_selector(sel)
        if el:
            t = el.inner_text().strip()
            if t:
                parts.append(t)
                break

    if not parts:
        main = page.query_selector("#noteContainer, main, .main-container")
        if main:
            parts.append(main.inner_text().strip())

    return "\n".join(parts)


def _extract_note_data(page: Page) -> NoteResult:
    """Extract text + author info from a note page. Returns a NoteResult with empty url."""
    ssr_data = _parse_ssr(page)
    text = ""
    user_id = ""
    nickname = ""

    if ssr_data:
        text = _text_from_ssr(ssr_data)
        user_id, nickname = _user_from_ssr(ssr_data)

    if not text:
        text = _extract_note_text(page)  # DOM fallback (no user info)

    return NoteResult(url="", text=text, user_id=user_id, nickname=nickname)


# ---------------------------------------------------------------------------
# SSR JSON parsing
# ---------------------------------------------------------------------------

def _parse_ssr(page: Page) -> Optional[dict]:
    """Parse ``window.__INITIAL_STATE__`` from ``<script>`` tags."""
    try:
        ssr = page.evaluate(r"""() => {
            for (const s of document.querySelectorAll('script')) {
                const t = s.textContent || '';
                if (t.includes('__INITIAL_STATE__')) {
                    const m = t.match(/window\.__INITIAL_STATE__\s*=\s*(\{.+\})/s);
                    if (m) return m[1];
                }
            }
            return null;
        }""")
        if ssr:
            return _json.loads(ssr.replace("undefined", "null"))
    except Exception:
        pass
    return None


def _text_from_ssr(data: dict) -> str:
    """Extract note title + desc from SSR data."""
    try:
        note_map = data.get("note", {}).get("noteDetailMap", {})
        for _nid, detail in note_map.items():
            note_obj = detail.get("note", detail) if isinstance(detail, dict) else {}
            title = note_obj.get("title", "")
            desc = note_obj.get("desc", "")
            if title or desc:
                return f"{title}\n{desc}".strip()
    except Exception:
        pass
    return ""


def _user_from_ssr(data: dict) -> tuple[str, str]:
    """Extract (user_id, nickname) of the note author from SSR data."""
    try:
        note_map = data.get("note", {}).get("noteDetailMap", {})
        for _nid, detail in note_map.items():
            note_obj = detail.get("note", detail) if isinstance(detail, dict) else {}
            user = note_obj.get("user", {})
            uid = user.get("userId", "") or user.get("userid", "")
            nick = user.get("nickname", "") or user.get("nickName", "")
            if uid:
                return uid, nick
    except Exception:
        pass
    return "", ""
