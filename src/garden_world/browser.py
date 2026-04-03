"""Playwright-based browser automation for Xiaohongshu.

Uses a **persistent browser context** (user-data-dir) so that all browser
state — cookies, localStorage, IndexedDB, cache, service workers — is
preserved between the interactive ``login()`` session and subsequent
headless ``search_and_fetch()`` / ``fetch_note()`` runs.
"""
from __future__ import annotations

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
_LOGIN_TIMEOUT = 240_000   # max wait for QR login (4 min)
_QR_REFRESH_INTERVAL = 90  # re-screenshot QR every N seconds
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
            "Chrome/131.0.0.0 Safari/537.36"
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
    screenshot and emitted via ``QR_IMAGE:`` (absolute file path) so
    the caller can relay it to the end-user.

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
            time.sleep(2)  # minimal wait for login wall to render

            # --- Screenshot the full login wall for QClaw ---
            qr_path = profile_dir / "qr.png"
            _screenshot_login_wall(page, qr_path)

            # Flush immediately so QClaw poll can see QR_IMAGE/QR_BASE64
            # BEFORE the blocking scan-wait loop below.
            timeout_min = _LOGIN_TIMEOUT // 60_000
            print(f"LOGIN_WAIT: 请用小红书 App 或微信扫描上方二维码登录（超时{timeout_min}分钟）。", flush=True)

            # --- Poll: wait for search results to actually appear ---
            deadline = time.time() + _LOGIN_TIMEOUT / 1000
            last_qr_time = time.time()
            last_ping_time = time.time()
            logged_in = False

            while time.time() < deadline:
                time.sleep(3)
                remaining = int(deadline - time.time())

                # Periodic progress: every 15s emit a status line
                if time.time() - last_ping_time >= 15:
                    last_ping_time = time.time()
                    print(
                        f"LOGIN_WAIT: 等待扫码中… 剩余 {remaining} 秒",
                        flush=True,
                    )

                # Re-screenshot QR periodically (QR may expire/refresh)
                if time.time() - last_qr_time >= _QR_REFRESH_INTERVAL:
                    last_qr_time = time.time()
                    print("LOGIN_WAIT: 二维码可能已刷新，正在重新截图…", flush=True)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
                        time.sleep(2)
                        _screenshot_login_wall(page, qr_path)
                        print(
                            f"LOGIN_WAIT: 新二维码已生成，请重新扫码（剩余 {remaining} 秒）",
                            flush=True,
                        )
                    except Exception:
                        pass

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
                    time.sleep(3)
                except Exception:
                    continue

            if not logged_in:
                print(
                    f"LOGIN_FAIL: 登录超时（{timeout_min}分钟内未检测到搜索结果）",
                    flush=True,
                )
                return False

            # Write marker
            marker = profile_dir / ".logged_in"
            marker.write_text("1")

            print(f"LOGIN_OK: 登录成功！浏览器配置已保存到 {profile_dir}", flush=True)
            return True

        finally:
            ctx.close()


def _screenshot_login_wall(page: Page, dest: Path) -> None:
    """Screenshot the login wall and emit the file path.

    Tries selectors from most specific (login-container) to least specific
    (full page) so the screenshot is focused on the QR code area.

    Outputs (flushed immediately so QClaw ``poll`` can see them):
      - ``QR_IMAGE: <absolute path>``
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Priority order: most focused first
    _WALL_SELECTORS = [
        '[class*="login-container"]',   # dialog box (~800x480)
        '[class*="login-modal"]',       # full modal overlay
        '[class*="overlay"]',
        '[class*="mask"]',
    ]

    captured = False
    for sel in _WALL_SELECTORS:
        try:
            wall = page.query_selector(sel)
            if wall and wall.is_visible():
                wall.screenshot(path=str(dest))
                captured = True
                break
        except Exception:
            continue

    if not captured:
        try:
            page.screenshot(path=str(dest))
            captured = True
        except Exception:
            pass

    if captured:
        print(f"QR_IMAGE: {dest.resolve()}", flush=True)


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
# Vue-router based note-detail fetcher
# ---------------------------------------------------------------------------

_FEED_API_PATTERN = "/api/sns/web/v1/feed"


def _fetch_note_via_router(
    page: Page,
    note_id: str,
    xsec_token: str = "",
) -> Optional[NoteResult]:
    """Fetch a single note by triggering XHS's own Vue 3 router.

    This makes XHS's client-side JS issue the feed API call with proper
    cryptographic signatures (``X-s``, ``X-s-common``, ``X-t``) that the
    server requires — we cannot forge these ourselves.

    Pre-condition: *page* must already be on an XHS page where the Vue 3
    app (``#app.__vue_app__``) and its router are loaded.
    """
    # Build the route query parameters (mirrors what a real click sends)
    query_parts = "xsec_source: 'pc_search', source: 'web_search_result_note'"
    if xsec_token:
        # Escape single-quotes in token (tokens are base64, shouldn't have any)
        safe_token = xsec_token.replace("'", "\\'")
        query_parts = f"xsec_token: '{safe_token}', " + query_parts

    push_js = f"""() => {{
        const app = document.querySelector('#app');
        if (!app || !app.__vue_app__) return 'no_vue_app';
        const router = app.__vue_app__.config.globalProperties.$router;
        if (!router) return 'no_router';
        router.push({{
            path: '/explore/{note_id}',
            query: {{ {query_parts} }}
        }});
        return 'ok';
    }}"""

    try:
        with page.expect_response(
            lambda r: _FEED_API_PATTERN in r.url,
            timeout=15_000,
        ) as feed_info:
            status = page.evaluate(push_js)
            if status != "ok":
                print(
                    f"INFO: Vue router not available ({status}) for {note_id}",
                    file=sys.stderr,
                    flush=True,
                )
                return None

        feed_resp = feed_info.value
        data = feed_resp.json()
    except Exception as exc:
        print(
            f"INFO: feed response wait failed for {note_id}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return None

    if not isinstance(data, dict) or not data.get("success"):
        _code = data.get("code", "") if isinstance(data, dict) else ""
        print(
            f"INFO: feed API returned success=false for {note_id}: code={_code}",
            file=sys.stderr,
            flush=True,
        )
        return None

    items = (data.get("data") or {}).get("items", [])
    if not items:
        return None

    nc = items[0].get("note_card", {})
    title = nc.get("title", "")
    desc = nc.get("desc", "")
    text = f"{title}\n{desc}".strip()
    if not text:
        return None

    user = nc.get("user", {})
    uid = user.get("user_id", "") or user.get("userId", "")
    nick = user.get("nickname", "") or user.get("nickName", "")
    note_url = _XHS_NOTE_URL.format(note_id=note_id)
    return NoteResult(url=note_url, text=text, user_id=uid, nickname=nick)


# ---------------------------------------------------------------------------
# Search & Fetch (headless, authenticated)
# ---------------------------------------------------------------------------

def search_and_fetch(
    keyword: str,
    date_hint: str,
    limit: int = 8,
    *,
    profile_dir: Path,
) -> list[NoteResult]:
    """Search XHS and fetch note text, using the persistent browser profile.

    Strategy (v3 — Vue-router SPA navigation):
      1. Load the search-results page and intercept the
         ``/api/sns/web/v1/search/notes`` response for note metadata.
      2. For each candidate, use the page's Vue 3 router to push to
         ``/explore/{note_id}`` — this triggers XHS's own JS to call
         the feed API with proper cryptographic signatures.
      3. Intercept the feed API response for full title + description.

    Returns list of :class:`NoteResult` with url, text, user_id, nickname.
    """
    _ensure_auth(profile_dir)
    results: list[NoteResult] = []

    with sync_playwright() as pw:
        ctx = _open_persistent(pw, profile_dir, headless=True)
        try:
            page = ctx.new_page()

            # --- Search (intercept API response) ---
            search_kw = f"{keyword} {date_hint}" if date_hint else keyword
            search_api_items: list[dict] = []
            try:
                with page.expect_response(
                    lambda r: "/api/sns/web/v1/search/notes" in r.url,
                    timeout=_NAV_TIMEOUT,
                ) as search_resp_info:
                    page.goto(
                        _XHS_SEARCH_URL.format(keyword=search_kw),
                        wait_until="domcontentloaded",
                        timeout=_NAV_TIMEOUT,
                    )
                search_body = search_resp_info.value.json()
                search_api_items = search_body.get("data", {}).get("items", [])
            except Exception:
                # Fallback: wait for DOM render
                try:
                    page.wait_for_selector(
                        'section.note-item, a[href*="/explore/"]',
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

            # --- Collect candidates from search API response ---
            # Deduplicate and limit
            seen: set[str] = set()
            candidates: list[dict] = []
            for item in search_api_items:
                nid = item.get("id", "")
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                candidates.append(item)
                if len(candidates) >= limit:
                    break

            # Fallback: if API interception yielded nothing, scrape links
            if not candidates:
                links = page.eval_on_selector_all(
                    'a[href*="/explore/"]',
                    "els => els.map(el => el.href)",
                )
                for href in links:
                    m = re.search(r"/explore/([a-f0-9]{24})", href)
                    if not m:
                        continue
                    nid = m.group(1)
                    if nid in seen:
                        continue
                    seen.add(nid)
                    candidates.append({"id": nid, "xsec_token": ""})
                    if len(candidates) >= limit:
                        break

            # --- Fetch each note via Vue router + feed API ---
            for item in candidates:
                nid = item["id"]
                xsec = item.get("xsec_token", "")
                nr = _fetch_note_via_router(page, nid, xsec)
                if nr and len(nr.text) > 20:
                    results.append(nr)

        finally:
            ctx.close()

    return results


def fetch_note(note_url: str, *, profile_dir: Path) -> Optional[NoteResult]:
    """Re-fetch a single XHS note and return a :class:`NoteResult`.

    Used by the cron loop to re-check a cached post for newly-revealed
    timed codes without repeating the full search.

    Strategy: load the XHS search page (to bootstrap the Vue 3 app),
    then use the client-side router to navigate to the note.  This makes
    XHS's own JS issue the feed API call with proper signed headers.
    """
    _ensure_auth(profile_dir)

    m = re.search(r"/explore/([a-f0-9]{24})", note_url)
    note_id = m.group(1) if m else ""
    if not note_id:
        return None

    with sync_playwright() as pw:
        ctx = _open_persistent(pw, profile_dir, headless=True)
        try:
            page = ctx.new_page()

            # Load a search page so Vue app + router are available
            page.goto(
                _XHS_SEARCH_URL.format(keyword="花园世界 兑换码"),
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT,
            )
            time.sleep(2)
            _dismiss_overlays(page)

            # --- Primary: Vue router → feed API ---
            nr = _fetch_note_via_router(page, note_id)
            if nr and len(nr.text) > 20:
                return nr

            # --- Fallback: navigate to note page (works in some cases) ---
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
                    return NoteResult(
                        url=note_url,
                        text=nd.text,
                        user_id=nd.user_id,
                        nickname=nd.nickname,
                    )
            except Exception:
                pass

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
