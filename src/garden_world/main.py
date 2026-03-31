from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

from zoneinfo import ZoneInfo

from .config import Settings
from .models import CodeBundle, TimedCodeWindow


SEARCH_URL = "https://duckduckgo.com/html/?q={query}"
BING_URL = "https://www.bing.com/search?q={query}"

# Lenient SSL context for environments with outdated certs
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds


def _http_get(url: str, timeout: int = 20) -> str:
    """HTTP GET with retry and lenient SSL."""
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except (URLError, TimeoutError, OSError) as exc:
            last_err = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
    raise last_err  # type: ignore[misc]


def _now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


_EMPTY_STATE = {"sent": {}, "last_post_url": "", "last_date": ""}


def _read_state(path: Path) -> dict:
    if not path.exists():
        return dict(_EMPTY_STATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Ensure expected keys
        for k, v in _EMPTY_STATE.items():
            data.setdefault(k, v if not isinstance(v, dict) else {})
        return data
    except Exception:
        return dict(_EMPTY_STATE)


def _write_state(path: Path, data: dict) -> None:
    # Prune sent records older than 7 days
    sent = data.get("sent", {})
    if len(sent) > 7:
        keys = sorted(sent.keys())
        for old_key in keys[:-7]:
            del sent[old_key]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX


def _extract_links_from_ddg(html: str) -> list[str]:
    links = []
    for m in re.finditer(r'href="//duckduckgo.com/l/\?uddg=([^"]+)"', html):
        raw = unquote(m.group(1))
        links.append(raw)
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        links.append(m.group(1))
    cleaned = []
    seen = set()
    for link in links:
        if "xiaohongshu.com" not in link:
            continue
        if "explore/" not in link and "/discovery/item/" not in link:
            continue
        if link in seen:
            continue
        seen.add(link)
        cleaned.append(link)
    return cleaned


def _extract_links_from_bing(html: str) -> list[str]:
    links = []
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        links.append(m.group(1))
    cleaned = []
    seen = set()
    for link in links:
        if "xiaohongshu.com" not in link:
            continue
        if "explore/" not in link and "/discovery/item/" not in link:
            continue
        if link in seen:
            continue
        seen.add(link)
        cleaned.append(link)
    return cleaned


def _search_candidate_posts(keyword: str, date_hint: str, limit: int) -> list[str]:
    queries = [
        f"site:xiaohongshu.com {keyword} {date_hint}",
        f"site:xiaohongshu.com {keyword}",
        f"site:xiaohongshu.com 我的花园世界 兑换码 {date_hint}",
        "site:xiaohongshu.com 我的花园世界 兑换码",
    ]
    merged = []
    seen = set()
    for q in queries:
        encoded = quote(q)
        for url_tmpl, extractor in (
            (SEARCH_URL, _extract_links_from_ddg),
            (BING_URL, _extract_links_from_bing),
        ):
            try:
                html = _http_get(url_tmpl.format(query=encoded), timeout=20)
            except Exception:
                continue
            for link in extractor(html):
                if link in seen:
                    continue
                seen.add(link)
                merged.append(link)
                if len(merged) >= limit:
                    return merged
    return merged[:limit]


def _fetch_text_via_jina(url: str) -> str:
    """Use Jina Reader to extract text from a URL. Validates response contains XHS content."""
    bare = url.removeprefix("https://").removeprefix("http://")
    reader_url = f"https://r.jina.ai/{bare}"
    text = _http_get(reader_url, timeout=30)
    # Validate we got useful content, not an error page
    if len(text) < 50 or "jina" in text[:200].lower() and "error" in text[:200].lower():
        raise ValueError(f"Jina Reader returned invalid content for {url}")
    return text


def _parse_codes(text: str, post_url: str) -> Optional[CodeBundle]:
    title_match = re.search(r"^#?\s*(.+兑换码[^\n]*)", text, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "我的花园世界兑换码"

    # Match dates: 3.31, 03.31, 3/31, 3月31日 — normalize to M.D (no leading zero)
    date_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日)?", title)
    if not date_match:
        # Also try in body text first 200 chars
        date_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日)?", text[:200])
    date_label = f"{int(date_match.group(1))}.{int(date_match.group(2))}" if date_match else ""

    weekly = None
    w = re.search(r"周码[^:：\n]*[:：]\s*([^\n\r]+)", text)
    if w:
        weekly = w.group(1).strip()

    universal = None
    u = re.search(r"(?:今日)?通用[^:：\n]*[:：]\s*([^\n\r]+)", text)
    if u:
        universal = u.group(1).strip()

    timed: list[TimedCodeWindow] = []
    pattern = re.compile(
        r"限时码\s*([1-3１-３])\s*[（(]\s*(\d{1,2}:\d{2})\s*[～~\-]\s*(\d{1,2}:\d{2})\s*[）)]\s*[:：]\s*([^\n\r]*)"
    )
    for m in pattern.finditer(text):
        n_txt = m.group(1).translate(str.maketrans("１２３", "123"))
        code_val = m.group(4).strip()
        timed.append(
            TimedCodeWindow(number=int(n_txt), start=m.group(2), end=m.group(3), code=code_val)
        )

    if not universal and not timed and not weekly:
        return None

    return CodeBundle(
        date_label=date_label,
        post_title=title,
        post_url=post_url,
        weekly_code=weekly,
        universal_code=universal,
        timed=sorted(timed, key=lambda x: x.number),
    )


def _find_today_bundle(settings: Settings, now: datetime, state: dict, force_refresh: bool = False) -> Optional[CodeBundle]:
    date_hint = f"{now.month}.{now.day}"
    candidates = _search_candidate_posts(settings.keyword, date_hint, settings.max_candidates)

    last_url = state.get("last_post_url", "")
    if last_url and last_url not in candidates:
        candidates.append(last_url)

    for link in candidates:
        try:
            txt = _fetch_text_via_jina(link)
        except Exception:
            continue
        bundle = _parse_codes(txt, link)
        if not bundle:
            continue
        if bundle.date_label and bundle.date_label != date_hint:
            continue
        return bundle
    return None


def _time_of(date_now: datetime, hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return date_now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


def _build_notifications(bundle: CodeBundle, state: dict, now: datetime) -> list[str]:
    sent = state.setdefault("sent", {})
    today_key = now.strftime("%Y-%m-%d")
    day_sent = sent.setdefault(today_key, {})
    notes: list[str] = []

    if bundle.universal_code and not day_sent.get("universal"):
        notes.append(f"【通用码】{bundle.universal_code}\n来源：{bundle.post_title}")
        day_sent["universal"] = bundle.universal_code

    if bundle.weekly_code and not day_sent.get("weekly"):
        notes.append(f"【周码】{bundle.weekly_code}")
        day_sent["weekly"] = bundle.weekly_code

    for item in bundle.timed:
        due = _time_of(now, item.start) + timedelta(minutes=5)
        sent_key = f"timed_{item.number}"
        if now >= due and item.code and not day_sent.get(sent_key):
            notes.append(
                f"【限时码{item.number}】{item.code}\n时间窗：{item.start}~{item.end}（开始后5分钟抓取）"
            )
            day_sent[sent_key] = item.code

    state["last_post_url"] = bundle.post_url
    state["last_date"] = today_key
    return notes


def run(now_mode: bool, force_refresh: bool) -> int:
    try:
        settings = Settings.from_env()
        now = _now(settings.timezone)
        state = _read_state(settings.state_path)

        bundle = _find_today_bundle(settings, now, state, force_refresh=force_refresh)
        if not bundle:
            print("STATUS: no_today_post_found")
            print("SCHEDULE_HINT: 建议QClaw cron在19:00-23:30每5分钟运行一次本技能")
            _write_state(settings.state_path, state)
            return 0

        notifications = _build_notifications(bundle, state, now)

        print(f"STATUS: ok date={now.strftime('%Y-%m-%d')} source={bundle.post_url}")
        if not notifications:
            print("STATUS: no_new_code_due")
        for line in notifications:
            print(f"NOTIFY: {line}")

        windows = ", ".join([f"{t.number}:{t.start}-{t.end}" for t in bundle.timed])
        if windows:
            print(f"INFO: windows={windows}")
        print("SCHEDULE_HINT: QClaw cron 每5分钟运行一次；首次19:00开始")

        _write_state(settings.state_path, state)
        return 0

    except Exception:
        traceback.print_exc()
        print("STATUS: error", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="garden-world qclaw skill runner")
    parser.add_argument("--now", action="store_true", help="run once for current time")
    parser.add_argument("--force-refresh", action="store_true", help="force refresh source")
    args = parser.parse_args()
    raise SystemExit(run(now_mode=args.now, force_refresh=args.force_refresh))


if __name__ == "__main__":
    main()
