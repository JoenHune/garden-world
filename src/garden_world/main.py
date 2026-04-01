"""Core logic for the garden-world QClaw skill.

Workflow (designed for cron every 5 min, 19:00-23:30):

1. If we already know today's post URL → **refetch** it directly to check
   for newly-revealed timed codes (博主 updates the post progressively).
2. Otherwise → **search** XHS, pick the best candidate, cache its URL.
3. Parse codes, compare against sent-state, emit NOTIFY lines for any new
   codes that are due (timed codes: window_start + 5 min).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from zoneinfo import ZoneInfo

from .browser import AuthRequired, NoteResult, fetch_note, login, search_and_fetch
from .config import Settings
from .models import CodeBundle, TimedCodeWindow


def _now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict = {
    "sent": {},
    "cached_post_url": "",
    "cached_date": "",
    "trusted_bloggers": {},  # user_id → {nickname, success_count, last_seen}
}


def _read_state(path: Path) -> dict:
    if not path.exists():
        return dict(_EMPTY_STATE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in _EMPTY_STATE.items():
            data.setdefault(k, v if not isinstance(v, dict) else {})
        return data
    except Exception:
        return dict(_EMPTY_STATE)


def _write_state(path: Path, data: dict) -> None:
    sent = data.get("sent", {})
    if len(sent) > 7:
        keys = sorted(sent.keys())
        for old_key in keys[:-7]:
            del sent[old_key]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX


# ---------------------------------------------------------------------------
# Find today's code bundle
# ---------------------------------------------------------------------------

def _find_today_bundle(
    settings: Settings,
    now: datetime,
    state: dict,
    force_refresh: bool = False,
) -> Optional[CodeBundle]:
    """Return today's CodeBundle.

    **Fast path** — if ``state`` already has a cached post URL for today,
    refetch that single note (no search needed).

    **Trusted-blogger path** — search specifically for known-good bloggers
    who consistently post daily codes, boosting their results in scoring.

    **Slow path** — full search + score-based selection.  Used on first
    run of the day or when ``--force-refresh`` is given.
    """
    date_hint = f"{now.month}.{now.day}"
    today_key = now.strftime("%Y-%m-%d")
    bloggers = state.setdefault("trusted_bloggers", {})

    # --- Fast path: refetch cached post ---
    cached_url = state.get("cached_post_url", "")
    cached_date = state.get("cached_date", "")
    if cached_url and cached_date == today_key and not force_refresh:
        nr = fetch_note(cached_url, profile_dir=settings.profile_dir)
        if nr:
            bundle = _parse_codes(nr.text, cached_url)
            if bundle:
                return bundle

    # --- Slow path: search + pick best (with trusted-blogger bonus) ---
    notes = search_and_fetch(
        settings.keyword, date_hint,
        limit=settings.max_candidates,
        profile_dir=settings.profile_dir,
    )

    best: Optional[CodeBundle] = None
    best_score = -1
    best_user: tuple[str, str] = ("", "")  # user_id, nickname

    for nr in notes:
        bundle = _parse_codes(nr.text, nr.url)
        if not bundle:
            continue
        if bundle.date_label and bundle.date_label != date_hint:
            continue

        score = _score_bundle(bundle)

        # Trusted-blogger bonus: +5 for known reliable authors
        if nr.user_id and nr.user_id in bloggers:
            score += 5

        if score > best_score:
            best_score = score
            best = bundle
            best_user = (nr.user_id, nr.nickname)

    # Cache the winning post URL for fast-path refetch next cron run
    if best:
        state["cached_post_url"] = best.post_url
        state["cached_date"] = today_key
        # Update trusted blogger records
        _update_trusted_blogger(bloggers, best_user, best_score, today_key)

    return best


# ---------------------------------------------------------------------------
# Trusted blogger management
# ---------------------------------------------------------------------------

_BLOGGER_TRUST_THRESHOLD = 7   # minimum base score (without bonus) to record
_BLOGGER_MAX_ENTRIES = 10      # max bloggers to track
_BLOGGER_STALE_DAYS = 14       # prune if not seen in N days


def _update_trusted_blogger(
    bloggers: dict, user: tuple[str, str], score: int, today_key: str,
) -> None:
    """Record or update a blogger after a successful parse."""
    uid, nick = user
    if not uid:
        return
    # Only record bloggers whose posts score well (excluding their own bonus)
    base_score = score - (5 if uid in bloggers else 0)
    if base_score < _BLOGGER_TRUST_THRESHOLD:
        return

    if uid in bloggers:
        bloggers[uid]["success_count"] = bloggers[uid].get("success_count", 0) + 1
        bloggers[uid]["last_seen"] = today_key
        if nick:
            bloggers[uid]["nickname"] = nick
    else:
        bloggers[uid] = {
            "nickname": nick,
            "success_count": 1,
            "last_seen": today_key,
        }

    # Prune stale entries
    _prune_bloggers(bloggers, today_key)


def _prune_bloggers(bloggers: dict, today_key: str) -> None:
    """Remove stale bloggers and cap total entries."""
    from datetime import date

    today = date.fromisoformat(today_key)
    stale = [
        uid for uid, info in bloggers.items()
        if (today - date.fromisoformat(info.get("last_seen", today_key))).days
        > _BLOGGER_STALE_DAYS
    ]
    for uid in stale:
        del bloggers[uid]

    # If still over limit, drop lowest success_count
    if len(bloggers) > _BLOGGER_MAX_ENTRIES:
        sorted_uids = sorted(
            bloggers.keys(),
            key=lambda k: bloggers[k].get("success_count", 0),
        )
        for uid in sorted_uids[:len(bloggers) - _BLOGGER_MAX_ENTRIES]:
            del bloggers[uid]


def _score_bundle(b: CodeBundle) -> int:
    """Higher = more complete.  Time-window presence matters even without codes."""
    s = 0
    if b.universal_code:
        s += 3
    if b.weekly_code:
        s += 1
    s += len(b.timed) * 1             # having time windows at all
    for t in b.timed:
        if t.code:
            s += 2                    # having actual code values
    return s


# ---------------------------------------------------------------------------
# Code parser helpers
# ---------------------------------------------------------------------------

# Mapping of CJK/fullwidth numerals to ASCII digits
_NUMERAL_MAP = str.maketrans("１２３４一二三四", "12341234")
# Common time pattern — accepts both : and . as separator (e.g. 20:12, 20.12)
_T = r"\d{1,2}[:.]\d{2}"


def _clean_code(val: str) -> str:
    """Strip decorative brackets and whitespace from code values."""
    return val.strip().strip("【】「」『』[]")


def _normalize_time(t: str) -> str:
    """Normalize dot time separator: 20.12 → 20:12"""
    return t.replace(".", ":")


# ---------------------------------------------------------------------------
# Code parser
# ---------------------------------------------------------------------------

def _parse_codes(text: str, post_url: str) -> Optional[CodeBundle]:
    # Normalise fullwidth colons/parens to halfwidth for easier matching
    norm = text.replace("：", ":").replace("（", "(").replace("）", ")")

    # ── Title ─────────────────────────────────────────────────
    title_match = re.search(r"^#?\s*(.+兑换码[^\n]*)", norm, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else ""
    # Discard titles that are mainly hashtag/topic markers
    if title and ("[话题]" in title and len(re.sub(r"[#\[\]话题\s]", "", title)) < 6):
        title = ""
    if not title:
        for line in norm.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "[话题]" not in line and len(line) > 4:
                title = line
                break
    if not title:
        title = "我的花园世界兑换码"

    # ── Date — M.D / M/D / M月D日 ────────────────────────────
    date_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日)?", title)
    if not date_match:
        date_match = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日)?", norm[:200])
    date_label = (
        f"{int(date_match.group(1))}.{int(date_match.group(2))}"
        if date_match else ""
    )

    # ── Weekly code (optional) ────────────────────────────────
    # Variants: 周码, 周兑换码, 本周周码, 本周通码
    weekly = None
    w = re.search(
        r"(?:本周)?周(?:兑换)?码[^(:\n]*(?:\([^)]*\))?[^:\n]*:\s*([^\n\r]+)", norm
    )
    if not w:
        # (M.D-M.D周码)CODE  — code follows closing paren without colon
        w = re.search(r"周(?:兑换)?码\s*\)\s*([^\n\r]+)", norm)
    if not w:
        w = re.search(r"本周通码[^:\n]*:\s*([^\n\r]+)", norm)
    if w:
        val = _clean_code(w.group(1))
        if val and "待更新" not in val:
            weekly = val

    # ── Universal / daily code ────────────────────────────────
    universal = None
    # (今日)?通用(兑换)?码?(...)?:CODE  — skip parenthesized time like (19:00)
    u = re.search(r"(?:今日)?通用[^(:\n]*(?:\([^)]*\))?[^:\n]*:\s*([^\n\r]+)", norm)
    if not u:
        # "通码" short form — but NOT "本周通码" (that is weekly)
        u = re.search(r"(?<!周)(?:今日)?通码[^:\n]*:\s*([^\n\r]+)", norm)
    if u:
        val = _clean_code(u.group(1))
        if val and "待更新" not in val:
            universal = val
    if not universal:
        # "(M.D当日)  CODE"  or "(M.D当日): CODE"
        u2 = re.search(
            r"\(\d{1,2}\.\d{1,2}当日\)\s*:?\s*([^\n\r]+)", norm
        )
        if u2:
            val = _clean_code(u2.group(1))
            if val and "待更新" not in val:
                universal = val
    if not universal:
        # "N 点兑换码: CODE" (daily code, not 限时)
        u3 = re.search(r"\d+\s*点兑换码\s*:\s*([^\n\r]+)", norm)
        if u3:
            val = _clean_code(u3.group(1))
            if val and "待更新" not in val:
                universal = val

    # ── Timed codes — multiple fallback formats ───────────────
    timed: list[TimedCodeWindow] = []

    # Format A: 限时码N(HH:MM~HH:MM): CODE
    #   N can be 1-4, fullwidth １-４, or CJK 一-四
    #   Time separator can be : or . (e.g. 20.12-20.27)
    pattern_a = re.compile(
        rf"限时码\s*([1-4１-４一二三四])\s*\(\s*({_T})\s*[～~\-]\s*({_T})\s*\)\s*:[ \t]*([^\n\r]*)"
    )
    for m in pattern_a.finditer(norm):
        n_txt = m.group(1).translate(_NUMERAL_MAP)
        code_val = _clean_code(m.group(4))
        if "待更新" in code_val:
            code_val = ""
        timed.append(
            TimedCodeWindow(
                number=int(n_txt),
                start=_normalize_time(m.group(2)),
                end=_normalize_time(m.group(3)),
                code=code_val,
            )
        )

    # Format B: (HH:MM-HH:MM): CODE  — no "限时码N" prefix
    # Guard: skip matches whose preceding context looks like a weekly/date label
    if not timed:
        pattern_b = re.compile(
            rf"\(\s*({_T})\s*[-~～]\s*({_T})\s*\)\s*:[ \t]*([^\n\r]*)"
        )
        _non_timed_ctx = re.compile(r"(?:周码|周兑换码|通用|通码|当日|有效期)")
        counter = 0
        for m in pattern_b.finditer(norm):
            pre = norm[max(0, m.start() - 30):m.start()]
            if _non_timed_ctx.search(pre):
                continue  # this paren is part of a weekly/universal label
            counter += 1
            code_val = _clean_code(m.group(3))
            if "待更新" in code_val:
                code_val = ""
            timed.append(
                TimedCodeWindow(
                    number=counter,
                    start=_normalize_time(m.group(1)),
                    end=_normalize_time(m.group(2)),
                    code=code_val,
                )
            )

    # Format C: "N 点限时码: CODE" — time range on next line "有效期: HH:MM-HH:MM"
    if not timed:
        pattern_c = re.compile(
            r"(\d+)\s*点限时码\s*:\s*([^\n\r]+)"
        )
        time_ranges = re.findall(
            rf"有效期\s*:\s*({_T})\s*[-~]\s*({_T})", norm
        )
        counter = 0
        for m in pattern_c.finditer(norm):
            code_val = _clean_code(m.group(2))
            if "待更新" in code_val:
                code_val = ""
            start = _normalize_time(time_ranges[counter][0]) if counter < len(time_ranges) else ""
            end = _normalize_time(time_ranges[counter][1]) if counter < len(time_ranges) else ""
            counter += 1
            timed.append(
                TimedCodeWindow(
                    number=counter,
                    start=start,
                    end=end,
                    code=code_val,
                )
            )

    # Format D: "第一个: CODE" — ordinal-numbered, no time windows
    if not timed:
        ordinal_map = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
        pattern_d = re.compile(
            r"第([一二三四1-4])\s*个\s*:\s*([^\n\r]+)"
        )
        for m in pattern_d.finditer(norm):
            num = ordinal_map.get(m.group(1), 0)
            if num == 0:
                continue
            code_val = _clean_code(m.group(2))
            if "待更新" in code_val:
                code_val = ""
            timed.append(
                TimedCodeWindow(number=num, start="", end="", code=code_val)
            )

    # Format E: 限时兑换码(...): followed by 兑换码N:CODE lines
    #   e.g. "限时兑换码(当天中午 12点到14点兑换有效):\n兑换码1:桃李争春满院香"
    if not timed:
        header_e = re.search(r"限时兑换码[^:\n]*:", norm)
        if header_e:
            hdr_text = norm[header_e.start():header_e.end()]
            # Extract time range — try HH:MM-HH:MM first, then N点到N点
            tr = re.search(rf"({_T})\s*[-~]\s*({_T})", hdr_text)
            if not tr:
                tr = re.search(r"(\d{1,2})\s*点\s*到\s*(\d{1,2})\s*点", hdr_text)
            start_h, end_h = "", ""
            if tr:
                s, e = tr.group(1), tr.group(2)
                if ":" in s or "." in s:
                    start_h, end_h = _normalize_time(s), _normalize_time(e)
                else:
                    start_h, end_h = f"{s}:00", f"{e}:00"
            remaining = norm[header_e.end():]
            # Stop before weekly/VIP/universal sections
            cut = re.search(r"(?:周|代言人|专属|福利码|通用码)", remaining)
            if cut:
                remaining = remaining[:cut.start()]
            for m in re.finditer(r"兑换码\s*(\d+)\s*:\s*([^\n\r]+)", remaining):
                code_val = _clean_code(m.group(2))
                if "待更新" in code_val:
                    code_val = ""
                timed.append(
                    TimedCodeWindow(
                        number=int(m.group(1)),
                        start=start_h, end=end_h, code=code_val,
                    )
                )

    # Format F: 限时码[一二三]:CODE — CJK ordinals without time-range parens
    #   e.g. "限时码一:20点左右，待更新"
    if not timed:
        pattern_f = re.compile(r"限时码\s*([一二三四])\s*:\s*([^\n\r]*)")
        for m in pattern_f.finditer(norm):
            n_txt = m.group(1).translate(_NUMERAL_MAP)
            code_val = _clean_code(m.group(2))
            if "待更新" in code_val or not code_val:
                code_val = ""
            timed.append(
                TimedCodeWindow(number=int(n_txt), start="", end="", code=code_val)
            )

    # Format G: 限时(HH:MM-HH:MM)兑换码: followed by bare code lines
    #   e.g. "限时(12:00-14:00)兑换码:\n红妆亦绽春风里\n..."
    if not timed:
        header_g = re.search(
            rf"限时\s*\(\s*({_T})\s*[-~]\s*({_T})\s*\)\s*兑换码\s*:", norm
        )
        if header_g:
            start_g = _normalize_time(header_g.group(1))
            end_g = _normalize_time(header_g.group(2))
            after = norm[header_g.end():]
            counter = 0
            for line in after.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#") or "[话题]" in line:
                    break
                if "待更新" in line:
                    continue
                counter += 1
                timed.append(
                    TimedCodeWindow(
                        number=counter, start=start_g, end=end_g,
                        code=_clean_code(line),
                    )
                )

    # ── Assemble bundle ───────────────────────────────────────
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


# ---------------------------------------------------------------------------
# Notification builder
# ---------------------------------------------------------------------------

def _time_of(date_now: datetime, hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return date_now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


def _build_notifications(bundle: CodeBundle, state: dict, now: datetime) -> list[str]:
    sent = state.setdefault("sent", {})
    today_key = now.strftime("%Y-%m-%d")
    day_sent = sent.setdefault(today_key, {})
    notes: list[str] = []

    if bundle.universal_code and not day_sent.get("universal"):
        notes.append(
            f"【通用码】{bundle.universal_code}\n来源：{bundle.post_title}"
        )
        day_sent["universal"] = bundle.universal_code

    if bundle.weekly_code and not day_sent.get("weekly"):
        notes.append(f"【周码】{bundle.weekly_code}")
        day_sent["weekly"] = bundle.weekly_code

    for item in bundle.timed:
        sent_key = f"timed_{item.number}"
        if not item.code:
            continue  # code not yet revealed by blogger
        if day_sent.get(sent_key):
            continue  # already pushed

        # If time window is known, wait until start + 5 min
        # If no time window (Format D), push immediately
        if item.start:
            due = _time_of(now, item.start) + timedelta(minutes=5)
            if now < due:
                continue
            time_info = f"\n时间窗：{item.start}~{item.end}（开始后5分钟抓取）"
        else:
            time_info = ""

        notes.append(
            f"【限时码{item.number}】{item.code}{time_info}"
        )
        day_sent[sent_key] = item.code

    state["cached_post_url"] = bundle.post_url
    state["cached_date"] = today_key
    return notes


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

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

        windows = ", ".join(
            f"{t.number}:{t.start}-{t.end}{'✓' if t.code else '?'}"
            for t in bundle.timed
        )
        if windows:
            print(f"INFO: windows={windows}")

        # Report trusted bloggers
        bloggers = state.get("trusted_bloggers", {})
        if bloggers:
            bl = ", ".join(
                f"{v.get('nickname', '?')}(×{v.get('success_count', 0)})"
                for v in sorted(
                    bloggers.values(),
                    key=lambda x: x.get("success_count", 0),
                    reverse=True,
                )
            )
            print(f"INFO: trusted_bloggers={bl}")

        print("SCHEDULE_HINT: QClaw cron 每5分钟运行一次；首次19:00开始")

        _write_state(settings.state_path, state)
        return 0

    except AuthRequired as e:
        print("STATUS: auth_required")
        print(f"ERROR: {e}")
        print("ACTION: 请运行 `garden-world login` 进行扫码登录。"
              "命令会输出 QR_IMAGE（文件路径）和 QR_BASE64（图片base64），"
              "请将二维码图片发送给用户用小红书或微信扫码。")
        sys.stdout.flush()
        return 2

    except Exception:
        traceback.print_exc()
        print("STATUS: error", file=sys.stderr)
        return 1


def run_login(headless: bool = False) -> int:
    """Login flow — opens browser for QR code scan.

    With ``headless=True``, no visible window is opened; the QR code
    screenshot is emitted via ``QR_IMAGE`` / ``QR_BASE64`` on stdout
    so QClaw can relay it to the user.
    """
    settings = Settings.from_env()
    ok = login(settings.profile_dir, headless=headless)
    return 0 if ok else 1


def main() -> None:
    # Force line-buffered stdout so QClaw poll sees output in real time
    # (when stdout is a pipe, Python defaults to full buffering)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="garden-world qclaw skill runner")
    sub = parser.add_subparsers(dest="command")

    parser.add_argument("--now", action="store_true", help="run once for current time")
    parser.add_argument("--force-refresh", action="store_true", help="force re-search")

    login_parser = sub.add_parser("login", help="扫码登录小红书，保存凭证供后续使用")
    login_parser.add_argument(
        "--headless", action="store_true",
        help="无头模式：不弹出浏览器窗口，通过 QR_IMAGE/QR_BASE64 输出二维码",
    )

    args = parser.parse_args()

    if args.command == "login":
        raise SystemExit(run_login(headless=args.headless))
    else:
        raise SystemExit(run(now_mode=args.now, force_refresh=args.force_refresh))


if __name__ == "__main__":
    main()
