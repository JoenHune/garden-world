"""Core logic for garden-world — standalone daemon or one-shot runner.

Standalone mode (``garden-world daemon``):
  - Built-in scheduler: 19:05 universal, every-5-min timed detection
  - Direct WeChat push via iLink API (no OpenClaw)
  - Auto-reply on incoming WeChat messages
  - Stops polling after 3rd timed code obtained or 23:30

One-shot mode (``garden-world --now``):
  - Original stdout-based NOTIFY/STATUS protocol
  - Compatible with external cron

Multi-source cross-validation:
  - Fetches top N candidate posts (not just best-1)
  - Codes confirmed by ≥2 sources → high confidence
  - Single-source codes → pushed with low-confidence warning
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from zoneinfo import ZoneInfo

from .browser import AuthRequired, NoteResult, fetch_note, login, search_and_fetch
from .config import Settings
from .models import BloggerScore, CodeBundle, TimedCodeWindow

logger = logging.getLogger("garden_world.main")


def _now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

_EMPTY_STATE: dict = {
    "sent": {},
    "cached_post_url": "",
    "cached_date": "",
    "cached_post_user_id": "",
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

    **Slow path** — full search + multi-source cross-validation.
    """
    date_hint = f"{now.month}.{now.day}"
    today_key = now.strftime("%Y-%m-%d")
    bloggers = state.setdefault("trusted_bloggers", {})

    # --- Fast path: refetch cached post ---
    cached_url = state.get("cached_post_url", "")
    cached_date = state.get("cached_date", "")
    if cached_url and cached_date == today_key and not force_refresh:
        nr = fetch_note(cached_url, profile_dir=settings.profile_dir)
        if nr and _is_guanfu_post(nr.text):
            bundle = _parse_codes(nr.text, cached_url)
            if bundle:
                return bundle

    # --- Slow path: search + multi-source cross-validation ---
    notes = search_and_fetch(
        settings.keyword, date_hint,
        limit=settings.max_candidates,
        profile_dir=settings.profile_dir,
    )

    # Parse all valid bundles (not just best)
    parsed: list[tuple[CodeBundle, NoteResult, int]] = []
    for nr in notes:
        if not _is_guanfu_post(nr.text):
            logger.debug("跳过非官服帖: %s", nr.url)
            continue

        bundle = _parse_codes(nr.text, nr.url)
        if not bundle:
            continue
        if bundle.date_label and bundle.date_label != date_hint:
            continue

        bundle.user_id = nr.user_id
        bundle.nickname = nr.nickname

        score = _score_bundle(bundle)
        # Dynamic trust bonus from multi-dimensional scoring
        if nr.user_id and nr.user_id in bloggers:
            bdata = bloggers[nr.user_id]
            bs = BloggerScore.from_dict(bdata) if isinstance(bdata, dict) else BloggerScore()
            score += bs.trust_bonus()

        parsed.append((bundle, nr, score))

    if not parsed:
        return None

    # Sort by score descending
    parsed.sort(key=lambda x: x[2], reverse=True)

    # Cross-validate if we have enough sources
    if len(parsed) >= settings.min_cross_validate:
        best = _cross_validate([b for b, _, _ in parsed])
    else:
        best = parsed[0][0]
        if len(parsed) == 1:
            best.confidence = "low"

    # Enrich: fill missing time windows from any parsed bundle
    _enrich_time_windows(best, [b for b, _, _ in parsed])

    best_nr = parsed[0][1]
    best_score = parsed[0][2]

    # Cache the winning post URL for fast-path refetch
    state["cached_post_url"] = best.post_url
    state["cached_date"] = today_key
    state["cached_post_user_id"] = best_nr.user_id

    # Update trusted blogger records for ALL sources that scored well
    for bundle, nr, score in parsed:
        _update_trusted_blogger(
            bloggers, (nr.user_id, nr.nickname), score, today_key,
            bundle=bundle, all_bundles=[b for b, _, _ in parsed],
        )

    return best


# ---------------------------------------------------------------------------
# Cross-validation
# ---------------------------------------------------------------------------

def _cross_validate(bundles: list[CodeBundle]) -> CodeBundle:
    """Merge multiple CodeBundles via majority-vote cross-validation.

    For each code slot, pick the value agreed upon by most sources.
    Codes from ≥2 sources → high confidence.
    Single-source codes → low confidence, still included (with warning).
    """
    if not bundles:
        raise ValueError("No bundles to validate")

    # Use the highest-scoring bundle as base
    best = bundles[0]

    # Universal code: majority vote
    universal_votes: Counter[str] = Counter()
    for b in bundles:
        if b.universal_code:
            universal_votes[b.universal_code] += 1
    if universal_votes:
        winner, count = universal_votes.most_common(1)[0]
        best.universal_code = winner
        if count < 2:
            best.confidence = "low"

    # Weekly code: majority vote
    weekly_votes: Counter[str] = Counter()
    for b in bundles:
        if b.weekly_code:
            weekly_votes[b.weekly_code] += 1
    if weekly_votes:
        winner, count = weekly_votes.most_common(1)[0]
        best.weekly_code = winner

    # Timed codes: per-slot majority vote
    # Collect time windows from ALL sources, not just those with matching codes
    all_slot_windows: dict[int, list[tuple[str, str]]] = {}
    for b in bundles:
        for t in b.timed:
            if t.start:
                all_slot_windows.setdefault(t.number, []).append((t.start, t.end or ""))

    for slot_num in range(1, 5):  # codes 1-4
        slot_votes: Counter[str] = Counter()
        for b in bundles:
            for t in b.timed:
                if t.number == slot_num and t.code:
                    slot_votes[t.code] += 1

        if slot_votes:
            winner, count = slot_votes.most_common(1)[0]
            fallback_windows = all_slot_windows.get(slot_num, [])
            # Update the corresponding timed entry in best
            for i, t in enumerate(best.timed):
                if t.number == slot_num:
                    best.timed[i] = TimedCodeWindow(
                        number=slot_num,
                        start=t.start or (fallback_windows[0][0] if fallback_windows else ""),
                        end=t.end or (fallback_windows[0][1] if fallback_windows else ""),
                        code=winner,
                    )
                    break
        else:
            # No code votes, but still try to enrich time windows on existing slots
            fallback_windows = all_slot_windows.get(slot_num, [])
            if fallback_windows:
                for i, t in enumerate(best.timed):
                    if t.number == slot_num and not t.start:
                        best.timed[i] = TimedCodeWindow(
                            number=slot_num,
                            start=fallback_windows[0][0],
                            end=fallback_windows[0][1],
                            code=t.code,
                        )
                        break

    return best


def _enrich_time_windows(best: CodeBundle, all_bundles: list[CodeBundle]) -> None:
    """Fill missing time windows on *best* from any source in *all_bundles*.

    This runs AFTER cross-validation as a final pass — ensures that even if
    the winning bundle (e.g. high-trust blogger) omits time windows, they
    are still populated from other sources that do include them.
    """
    # Collect all known windows from all bundles (prefer precise over approx)
    pool: dict[int, list[tuple[str, str]]] = {}
    for b in all_bundles:
        for t in b.timed:
            if t.start:
                pool.setdefault(t.number, []).append((t.start, t.end or ""))

    for i, t in enumerate(best.timed):
        if t.start and t.end:
            continue  # already has precise window
        candidates = pool.get(t.number, [])
        if not candidates:
            continue
        # Prefer candidates with both start and end
        precise = [(s, e) for s, e in candidates if s and e]
        if precise:
            best.timed[i] = TimedCodeWindow(
                number=t.number, start=precise[0][0], end=precise[0][1], code=t.code,
            )
        elif not t.start:
            # Use approximate (start only)
            best.timed[i] = TimedCodeWindow(
                number=t.number, start=candidates[0][0], end=candidates[0][1], code=t.code,
            )


# ---------------------------------------------------------------------------
# Trusted blogger management
# ---------------------------------------------------------------------------

_BLOGGER_TRUST_THRESHOLD = 7   # minimum base score (without bonus) to record
_BLOGGER_MAX_ENTRIES = 20      # max bloggers to track (raised for cross-validation)
_BLOGGER_STALE_DAYS = 14       # prune if not seen in N days
_SCORE_ALPHA = 0.3             # sliding average weight for new observations


def _update_trusted_blogger(
    bloggers: dict,
    user: tuple[str, str],
    score: int,
    today_key: str,
    *,
    bundle: Optional[CodeBundle] = None,
    all_bundles: Optional[list[CodeBundle]] = None,
) -> None:
    """Record or update a blogger with multi-dimensional scoring."""
    uid, nick = user
    if not uid:
        return

    # Compute base score excluding trust bonus
    existing = bloggers.get(uid)
    if existing:
        bs = BloggerScore.from_dict(existing) if isinstance(existing, dict) else BloggerScore()
        base_score = score - bs.trust_bonus()
    else:
        base_score = score
    if base_score < _BLOGGER_TRUST_THRESHOLD:
        return

    # Initialize or load existing scores
    if existing and isinstance(existing, dict):
        bs = BloggerScore.from_dict(existing)
    else:
        bs = BloggerScore()

    bs.nickname = nick or bs.nickname
    bs.success_count += 1
    bs.last_seen = today_key
    bs.total_checks += 1

    # Update multi-dimensional scores if bundle data available
    if bundle:
        # Format score: did we use a primary parse path? (heuristic: bundle.parse_clean)
        new_format = 1.0 if bundle.parse_clean else 0.3
        bs.format_score = _sliding_avg(bs.format_score, new_format)

        # Time window score: does this blogger include actual time windows?
        timed_with_code = [t for t in bundle.timed if t.code]
        if timed_with_code:
            timed_with_window = sum(1 for t in timed_with_code if t.start and t.end)
            new_tw = timed_with_window / len(timed_with_code)
            bs.time_window_score = _sliding_avg(bs.time_window_score, new_tw)

        # Count valid codes in this bundle
        code_count = sum(1 for t in bundle.timed if t.code) + (1 if bundle.universal_code else 0)
        if code_count > 0:
            bs.valid_codes += code_count

    # Reliability: cross-check against other bundles' agreed codes
    if bundle and all_bundles and len(all_bundles) > 1:
        agreements = 0
        total_checks_now = 0

        if bundle.universal_code:
            total_checks_now += 1
            others = [b.universal_code for b in all_bundles if b.universal_code and b is not bundle]
            if bundle.universal_code in others:
                agreements += 1

        for t in bundle.timed:
            if t.code:
                total_checks_now += 1
                other_codes = [
                    ot.code for b in all_bundles if b is not bundle
                    for ot in b.timed if ot.number == t.number and ot.code
                ]
                if t.code in other_codes:
                    agreements += 1

        if total_checks_now > 0:
            new_reliability = agreements / total_checks_now
            bs.reliability_score = _sliding_avg(bs.reliability_score, new_reliability)

    bloggers[uid] = bs.to_dict()
    _prune_bloggers(bloggers, today_key)


def _sliding_avg(old: float, new: float) -> float:
    """Exponential sliding average."""
    return old * (1 - _SCORE_ALPHA) + new * _SCORE_ALPHA


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

    # If still over limit, drop by lowest composite score
    if len(bloggers) > _BLOGGER_MAX_ENTRIES:
        def _composite(info: dict) -> float:
            bs = BloggerScore.from_dict(info) if isinstance(info, dict) else BloggerScore()
            return bs.success_count + bs.trust_bonus()
        sorted_uids = sorted(
            bloggers.keys(),
            key=lambda k: _composite(bloggers[k]),
        )
        for uid in sorted_uids[:len(bloggers) - _BLOGGER_MAX_ENTRIES]:
            del bloggers[uid]


# Posts for non-官服 servers (支服/B服/渠道服) have different codes & times.
# We only want 官服 (the default official server).
_RE_NON_GUANFU = re.compile(r'支服|支付宝服|[Bb]服|渠道服|ZFB|zfb')


def _is_guanfu_post(text: str) -> bool:
    """Return True if the post text is for 官服 (official server) or unspecified."""
    # Check only the first ~300 chars (title + header area)
    header = text[:300]
    return not _RE_NON_GUANFU.search(header)


def _score_bundle(b: CodeBundle) -> int:
    """Higher = more complete.  Time-window presence matters even without codes."""
    s = 0
    if b.universal_code:
        s += 3
    if b.weekly_code:
        s += 1
    s += len(b.timed) * 1             # having timed slots at all
    for t in b.timed:
        if t.code:
            s += 2                    # having actual code values
        if t.start and t.end:
            s += 2                    # bonus for precise time windows
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


_RE_BRACKET_ANNOTATION = re.compile(r'\[[^\]]*\]')


def _sanitize_code(val: str) -> str:
    """Keep only CJK characters — redemption codes are pure Chinese text.

    Strips XHS emoji annotations like [蹲后续H], English text, symbols, etc.
    Returns empty string for placeholder/descriptive text that isn't actually
    a code (e.g. "限时码特定分钟之内有效").
    """
    if not val:
        return ""
    # Remove bracketed annotations (XHS emoji placeholders)
    val = _RE_BRACKET_ANNOTATION.sub('', val)
    # Remove trailing incomplete brackets: [text-without-closing
    val = re.sub(r'\[[^\]]*$', '', val)
    # Keep only CJK unified ideographs
    code = re.sub(r'[^\u4e00-\u9fff]', '', val)
    # Reject placeholder / descriptive text that isn't an actual code
    if _is_placeholder(code):
        return ""
    return code


# Words that appear in descriptive/placeholder text but never in real codes.
# Examples of rejected text: "限时码特定分钟之内有效", "需要在特定分钟之内使用才有效"
_PLACEHOLDER_WORDS = re.compile(
    r"待更新|待公布|待发布|未公布|未发布|未更新|敬请期待"
    r"|有效期?|过期|分钟|小时"
    r"|限时码|通用码|周码|兑换码|兑换"
    r"|需要|使用|特定|之内|才能|请在|仅可|每个"
)


def _is_placeholder(code: str) -> bool:
    """Return True if *code* looks like descriptive text rather than a real code."""
    if not code:
        return True
    # Real codes are short poetic phrases, typically 4-8 CJK characters.
    # Descriptions/placeholders are longer and contain functional words.
    if _PLACEHOLDER_WORDS.search(code):
        return True
    return False


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
        u3 = re.search(r"\d+\s*点兑换码\s*:[ \t]*([^\n\r]+)", norm)
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
            r"(\d+)\s*点限时码\s*:[ \t]*([^\n\r]*)"
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
    #   Also extracts approximate time hints from "N点" or "HH:MM" in raw text.
    if not timed:
        _time_hint_re = re.compile(
            rf"({_T})\s*[～~\-]\s*({_T})"       # explicit range: 20:00~20:15
            r"|(\d{1,2})\s*点"                   # approximate: 20点
        )
        pattern_f = re.compile(r"限时码\s*([一二三四])\s*:\s*([^\n\r]*)")
        for m in pattern_f.finditer(norm):
            n_txt = m.group(1).translate(_NUMERAL_MAP)
            raw_val = m.group(2)
            code_val = _clean_code(raw_val)
            if "待更新" in code_val or not code_val:
                code_val = ""
            # Try to extract a time hint from the raw text after ':'
            start_f, end_f = "", ""
            th = _time_hint_re.search(raw_val)
            if th:
                if th.group(1):  # explicit range HH:MM~HH:MM
                    start_f = _normalize_time(th.group(1))
                    end_f = _normalize_time(th.group(2))
                elif th.group(3):  # approximate N点
                    start_f = f"{int(th.group(3))}:00"
            timed.append(
                TimedCodeWindow(number=int(n_txt), start=start_f, end=end_f, code=code_val)
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

    # Format H: 限时码:HH:MM... — sequential time-only entries without ordinals
    #   e.g. "限时码:20:00评论区见\n限时码:21:00评论区见\n限时码:22:00评论区见"
    #   Produces time-hint-only entries (no codes) for cross-source enrichment.
    if not timed:
        pattern_h = re.compile(rf"限时码\s*:\s*({_T})\s*([^\n\r]*)")
        counter = 0
        for m in pattern_h.finditer(norm):
            raw_after = m.group(2)
            # Skip if this looks like it has a real code (not just "评论区见")
            code_val = ""
            if raw_after and "评论区" not in raw_after and "待更新" not in raw_after:
                cleaned = _clean_code(raw_after)
                if cleaned and not _is_placeholder(cleaned):
                    code_val = cleaned
            counter += 1
            timed.append(
                TimedCodeWindow(
                    number=counter,
                    start=_normalize_time(m.group(1)),
                    end="",
                    code=code_val,
                )
            )

    # ── Assemble bundle ───────────────────────────────────────
    if not universal and not timed and not weekly:
        return None

    # Sanitize code values — codes are pure Chinese text
    if weekly:
        weekly = _sanitize_code(weekly) or None
    if universal:
        universal = _sanitize_code(universal) or None
    timed = [
        TimedCodeWindow(
            number=t.number, start=t.start, end=t.end,
            code=_sanitize_code(t.code) if t.code else "",
        )
        for t in timed
    ]

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
# Unified output format
# ---------------------------------------------------------------------------

def _time_of(date_now: datetime, hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return date_now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


def _format_codes(bundle: CodeBundle, now: datetime) -> str:
    """Build the canonical code display — same format for stdout and WeChat.

    Format::

        🌸 花园世界兑换码 M.D

        ✅ 周码: XXXX
        ✅ 通用码: XXXX
        ⏱️ 限时码1 (HH:MM-HH:MM): XXXX
        ⏱️ 限时码2 (HH:MM-HH:MM): 未更新
        ⏱️ 限时码3 (未更新): 未更新
    """
    date_str = bundle.date_label or f"{now.month}.{now.day}"
    lines = [f"🌸 花园世界兑换码 {date_str}"]
    lines.append(f"✅ 周码: {bundle.weekly_code or '(未更新)'}")
    lines.append(f"✅ 通用码: {bundle.universal_code or '(未更新)'}")

    shown: set[int] = set()
    for t in sorted(bundle.timed, key=lambda x: x.number):
        shown.add(t.number)
        code = t.code or "(未更新)"
        if t.start and t.end:
            lines.append(f"⏱️ 限时码{t.number} ({t.start}-{t.end}): {code}")
        else:
            lines.append(f"⏱️ 限时码{t.number} (未更新): {code}")
    for i in range(1, 4):
        if i not in shown:
            lines.append(f"⏱️ 限时码{i} (未更新): (未更新)")

    return "\n".join(lines)


def _build_notifications(bundle: CodeBundle, state: dict, now: datetime) -> list[str]:
    """Return list of newly-due code lines (dedup via state). Same format as _format_codes."""
    sent = state.setdefault("sent", {})
    today_key = now.strftime("%Y-%m-%d")
    day_sent = sent.setdefault(today_key, {})
    notes: list[str] = []

    if bundle.weekly_code and not day_sent.get("weekly"):
        notes.append(f"✅ 周码: {bundle.weekly_code}")
        day_sent["weekly"] = bundle.weekly_code

    if bundle.universal_code and not day_sent.get("universal"):
        notes.append(f"✅ 通用码: {bundle.universal_code}")
        day_sent["universal"] = bundle.universal_code

    for item in bundle.timed:
        sent_key = f"timed_{item.number}"
        if not item.code:
            continue
        if day_sent.get(sent_key):
            continue

        if item.start:
            due = _time_of(now, item.start) + timedelta(minutes=5)
            if now < due:
                continue
            time_part = f" ({item.start}-{item.end})"
        else:
            time_part = " (未更新)"

        notes.append(f"⏱️ 限时码{item.number}{time_part}: {item.code}")
        day_sent[sent_key] = item.code

    state["cached_post_url"] = bundle.post_url
    state["cached_date"] = today_key

    # Cache timed-code windows so `push` can display them without browser.
    # Preserve previously-cached windows when the new parse has empty values
    # (e.g. time hints from the "待更新" phase shouldn't be lost).
    existing_windows = state.get("cached_timed_windows", {})
    windows = {}
    for t in bundle.timed:
        old = existing_windows.get(str(t.number), {})
        windows[str(t.number)] = {
            "start": t.start or old.get("start", ""),
            "end": t.end or old.get("end", ""),
        }
    state["cached_timed_windows"] = windows

    return notes


# ---------------------------------------------------------------------------
# Downranking helpers
# ---------------------------------------------------------------------------

def _downrank_stale_bloggers(
    bloggers: dict, bundle: CodeBundle, now: datetime,
    cached_user_id: str,
) -> list[str]:
    """Reduce trust for the cached-post blogger if codes are missing past windows.

    Returns info messages describing any downranking actions taken.
    """
    if not cached_user_id or cached_user_id not in bloggers:
        return []

    missing_past_window = 0
    for t in bundle.timed:
        if t.code:
            continue
        if not t.start or not t.end:
            continue
        try:
            window_end = _time_of(now, t.end)
        except (ValueError, IndexError):
            continue
        # Past window end + 10 min grace period → code should have appeared
        if now > window_end + timedelta(minutes=10):
            missing_past_window += 1

    if missing_past_window == 0:
        return []

    info = bloggers[cached_user_id]
    old_count = info.get("success_count", 1)
    new_count = max(0, old_count - missing_past_window)
    info["success_count"] = new_count
    nickname = info.get("nickname", "?")

    return [
        f"降权 {nickname}: {missing_past_window}个限时码超时未更新 "
        f"(trust: {old_count}→{new_count})"
    ]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _get_bridge(settings: Settings) -> object | None:
    """Load WeChat bridge; auto-import from OpenClaw on first use."""
    try:
        from .wechat import WeChatBridge, import_from_openclaw
    except ImportError:
        logger.debug("httpx not installed, WeChat push disabled")
        return None

    config = settings.config_dir / "wechat.json"
    bridge = WeChatBridge(config_path=config)

    if not bridge.has_accounts:
        logger.info("No WeChat accounts configured, trying OpenClaw import…")
        imported = import_from_openclaw(config_path=config)
        if imported:
            bridge.reload()
            logger.info("Imported %d account(s) from OpenClaw", len(imported))
        else:
            logger.info("No OpenClaw accounts found")

    if bridge.has_accounts:
        # Eagerly refresh context_token via a quick getUpdates poll.
        # One-shot commands (push, enrich) don't run a long-poll loop,
        # so the stored token may be stale.  A single poll picks up any
        # pending system/subscribe events that carry a fresh token.
        bridge.refresh_context()

    return bridge if bridge.has_accounts else None


def run(now_mode: bool, force_refresh: bool, auto_login: bool = False) -> int:
    try:
        settings = Settings.from_env()
        now = _now(settings.timezone)
        state = _read_state(settings.state_path)

        bundle = _find_today_bundle(settings, now, state, force_refresh=force_refresh)
        if not bundle:
            logger.info("未找到今日帖子 (北京时间 %s)", now.strftime("%Y-%m-%d %H:%M"))
            print("STATUS: no_today_post_found")
            _write_state(settings.state_path, state)
            return 0

        # ── Canonical code display (stdout) ──
        report = _format_codes(bundle, now)
        print(report)
        logger.info("来源: %s", bundle.post_url)

        # ── Downrank bloggers with missing codes past time windows ──
        cached_uid = state.get("cached_post_user_id", "")
        bloggers = state.get("trusted_bloggers", {})
        downrank_msgs = _downrank_stale_bloggers(bloggers, bundle, now, cached_uid)
        for msg in downrank_msgs:
            logger.info("降权: %s", msg)

        # ── Push notifications for newly-due codes ──
        notifications = _build_notifications(bundle, state, now)
        if not notifications:
            logger.info("无新到期兑换码")

        # Confidence warning
        if bundle.confidence == "low" and notifications:
            logger.warning("低置信度 — 当前仅单一来源确认，建议人工核验")

        # ── WeChat push (only in --now mode, separated from fetch) ──
        # --now only fetches & displays; use `push` command to send to WeChat
        _write_state(settings.state_path, state)

        if not now_mode:
            return 0

        # Auto-push in --now mode for backward compat
        bridge = _get_bridge(settings)
        if bridge and notifications:
            date_str = bundle.date_label or f"{now.month}.{now.day}"
            push_text = f"🌸 花园世界兑换码 {date_str}\n" + "\n".join(notifications)
            if bundle.confidence == "low":
                push_text = "⚠ [单源未验证]\n" + push_text
            results = bridge.broadcast_text(push_text)
            for acct_id, ok in results.items():
                if ok:
                    logger.info("微信推送成功: %s", acct_id)
                else:
                    logger.error("微信推送失败: %s", acct_id)
            if bridge.session_expired:
                logger.warning("微信 session 已过期，请运行 garden-world bind 重新扫码绑定")
                print("微信推送失败: session 已过期。请运行 garden-world bind 重新扫码绑定。")
            elif bridge.needs_context:
                logger.warning("微信 context_token 已失效，请向Bot发送任意消息以激活")
                print("微信推送失败: context_token 已失效。请在微信中向Bot发送一条消息后重试。")
        elif not bridge:
            logger.debug("无微信账号，跳过推送")

        # Trusted bloggers summary
        if bloggers:
            top = sorted(bloggers.values(), key=lambda x: x.get("success_count", 0), reverse=True)[:5]
            bl = ", ".join(f"{v.get('nickname', '?')}(×{v.get('success_count', 0)})" for v in top)
            logger.info("信任博主: %s", bl)

        _write_state(settings.state_path, state)
        return 0

    except AuthRequired as e:
        if auto_login:
            logger.warning("登录过期，自动启动 headless 登录…")
            settings = Settings.from_env()
            ok = login(settings.profile_dir, headless=True)
            if ok:
                logger.info("自动登录成功，重新获取兑换码")
                return run(now_mode=now_mode, force_refresh=True, auto_login=False)
            else:
                logger.error("自动登录超时")
                return 2
        logger.error("登录过期: %s", e)
        print(f"请运行 garden-world login 进行扫码登录")
        return 2

    except Exception:
        logger.exception("运行出错")
        return 1


# ---------------------------------------------------------------------------
# Daemon sub-task functions (used by scheduler)
# ---------------------------------------------------------------------------

def fetch_and_push_codes(settings: Settings, state: dict, bridge: object) -> tuple[bool, list[str]]:
    """Single fetch cycle: search/parse/validate, push new codes via bridge.

    Returns (has_new_notifications, notification_texts).
    """
    now = _now(settings.timezone)
    bundle = _find_today_bundle(settings, now, state)
    if not bundle:
        logger.info("fetch_and_push: 未找到今日帖子")
        return False, []

    notifications = _build_notifications(bundle, state, now)
    _write_state(settings.state_path, state)

    if notifications and bridge and hasattr(bridge, 'broadcast_text'):
        # First message: full formatted report with all code slots
        full_report = _format_codes(bundle, _now(settings.timezone))
        if bundle.confidence == "low":
            full_report = "⚠ [单源未验证]\n" + full_report
        bridge.broadcast_text(full_report)

        # Second message: only the latest new bare code for easy copy-paste
        latest_bare = None
        for n in notifications:
            if ": " in n:
                latest_bare = n.split(": ", 1)[1]
        if latest_bare:
            bridge.broadcast_text(latest_bare)

        logger.info("推送 %d 条新兑换码", len(notifications))

    return bool(notifications), notifications


def get_timed_windows(settings: Settings, state: dict) -> list[dict]:
    """Detect timed-code windows from cached/searched post.

    Returns list of {"number": N, "start": "HH:MM", "end": "HH:MM"}.
    """
    now = _now(settings.timezone)
    bundle = _find_today_bundle(settings, now, state)
    if not bundle:
        return []

    windows = []
    for t in bundle.timed:
        if t.start:
            windows.append({"number": t.number, "start": t.start, "end": t.end})
    return windows


def get_timed_windows_enriched(settings: Settings, state: dict) -> list[dict]:
    """Like get_timed_windows, but forces a multi-source search to find windows."""
    now = _now(settings.timezone)
    bundle = _find_today_bundle(settings, now, state, force_refresh=True)
    if not bundle:
        return []

    windows = []
    for t in bundle.timed:
        if t.start:
            windows.append({"number": t.number, "start": t.start, "end": t.end})
    return windows


def fetch_timed_code(settings: Settings, state: dict, bridge: object, window_number: int) -> bool:
    """Fetch and push a specific timed code. Returns True if pushed."""
    now = _now(settings.timezone)
    bundle = _find_today_bundle(settings, now, state)
    if not bundle:
        return False

    for t in bundle.timed:
        if t.number == window_number and t.code:
            sent = state.setdefault("sent", {})
            today_key = now.strftime("%Y-%m-%d")
            day_sent = sent.setdefault(today_key, {})
            sent_key = f"timed_{t.number}"
            if day_sent.get(sent_key):
                return True  # already sent

            day_sent[sent_key] = t.code
            _write_state(settings.state_path, state)

            if bridge and hasattr(bridge, 'broadcast_text'):
                # First message: full formatted report with all code slots
                full_report = _format_codes(bundle, _now(settings.timezone))
                bridge.broadcast_text(full_report)
                # Second message: bare code for easy copy-paste
                bridge.broadcast_text(t.code)
            logger.info("推送限时码%d: %s", t.number, t.code)
            return True

    return False


# ---------------------------------------------------------------------------
# Daemon mode
# ---------------------------------------------------------------------------

def run_daemon() -> int:
    """Start the long-running daemon with scheduler + auto-reply."""
    settings = Settings.from_env()
    state = _read_state(settings.state_path)

    # Initialize WeChat bridge (auto-import from OpenClaw)
    bridge = _get_bridge(settings)
    if not bridge:
        logger.warning("无微信账号，每日查询将跳过。请运行 garden-world bind 或 garden-world import-wechat")

    # Start auto-reply service
    autoreply_svc = None
    if bridge and bridge.has_accounts:
        try:
            from .autoreply import AutoReplyService
            autoreply_svc = AutoReplyService(bridge, settings.config_dir)
            autoreply_svc.start()
        except Exception:
            logger.exception("Failed to start AutoReply service")

    # Define scheduler callbacks
    def on_fetch_universal():
        if not bridge or not bridge.has_accounts:
            return False, set()
        ok, notes = fetch_and_push_codes(settings, state, bridge)
        # Report which timed codes are now in sent state
        now = _now(settings.timezone)
        today_key = now.strftime("%Y-%m-%d")
        day_sent = state.get("sent", {}).get(today_key, {})
        sent_timed = set()
        for i in range(1, 4):
            if day_sent.get(f"timed_{i}"):
                sent_timed.add(i)
        return ok, sent_timed

    def on_detect_timed() -> list[dict]:
        windows = get_timed_windows(settings, state)
        if not windows:
            # Fast path may have used a single cached source lacking time windows.
            # Force a multi-source search to discover windows from other bloggers.
            logger.info("No time windows from cached source, trying multi-source search…")
            windows = get_timed_windows_enriched(settings, state)
        return windows

    def on_fetch_timed(window_number: int) -> bool:
        if not bridge or not bridge.has_accounts:
            return False
        return fetch_timed_code(settings, state, bridge, window_number)

    def on_day_complete() -> None:
        _write_state(settings.state_path, state)

    # Start scheduler
    from .scheduler import DailyScheduler
    scheduler = DailyScheduler(
        tz_name=settings.timezone,
        on_fetch_universal=on_fetch_universal,
        on_detect_timed=on_detect_timed,
        on_fetch_timed=on_fetch_timed,
        on_day_complete=on_day_complete,
    )
    scheduler.start()

    logger.info("Daemon started. Press Ctrl+C to stop.")

    import signal
    stop_event = __import__("threading").Event()

    def _handle_signal(sig, frame):
        logger.info("Received signal %s, shutting down…", sig)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stop_event.wait()

    # Cleanup
    scheduler.stop()
    if autoreply_svc:
        autoreply_svc.stop()
    _write_state(settings.state_path, state)
    logger.info("Daemon stopped.")
    return 0


# ---------------------------------------------------------------------------
# WeChat account binding
# ---------------------------------------------------------------------------

def run_import_wechat() -> int:
    """Import WeChat accounts from OpenClaw credentials."""
    settings = Settings.from_env()
    try:
        from .wechat import import_from_openclaw, load_accounts
    except ImportError:
        logger.error("httpx is required: pip install httpx")
        return 1

    config = settings.config_dir / "wechat.json"
    imported = import_from_openclaw(config_path=config)
    all_accounts = load_accounts(config)

    if imported:
        for a in imported:
            logger.info("导入账号: %s (user=%s)", a.account_id, a.user_id)
        print(f"成功导入 {len(imported)} 个账号")
    else:
        print("未发现新账号可导入")

    if all_accounts:
        print(f"当前共 {len(all_accounts)} 个微信账号:")
        for a in all_accounts:
            print(f"  {a.account_id} → {a.user_id}")

    # Quick connectivity test
    if all_accounts:
        from .wechat import send_text
        acct = all_accounts[0]
        result = send_text(acct, "garden-world 微信连接测试成功")
        if result == "ok":
            print(f"连接测试成功 (发送到 {acct.account_id})")
        elif result == "session_expired":
            print(f"微信 session 已过期 (账号 {acct.account_id})。请运行 garden-world bind 重新扫码绑定。")
            return 1
        else:
            print(f"连接测试失败 (账号 {acct.account_id})，请检查网络或运行 garden-world bind 重新绑定")
            return 1

    return 0


def run_bind() -> int:
    """Bind or re-bind a WeChat account via QR code scan."""
    settings = Settings.from_env()

    try:
        from .wechat import (
            WeChatBridge,
            start_qr_login,
            wait_for_login,
            register_login_result,
        )
    except ImportError:
        logger.error("httpx is required: pip install httpx")
        return 1

    config = settings.config_dir / "wechat.json"
    bridge = WeChatBridge(config_path=config)

    logger.info("正在获取微信绑定二维码…")

    try:
        session = start_qr_login()
    except Exception:
        logger.exception("获取二维码失败")
        return 1

    if session.qrcode_url:
        from .wechat import render_qr_terminal
        print("请用微信扫描以下二维码:", flush=True)
        print(render_qr_terminal(session.qrcode_url), flush=True)
        logger.debug("QR URL: %s", session.qrcode_url)

    def on_status(s):
        if s.status == "scaned":
            print("已扫码，请在微信中确认…", flush=True)
        elif s.status == "expired":
            if s.qrcode_url:
                from .wechat import render_qr_terminal
                print("二维码已过期，已自动刷新:", flush=True)
                print(render_qr_terminal(s.qrcode_url), flush=True)

    session = wait_for_login(session, on_status=on_status)

    if session.status != "confirmed":
        logger.error("绑定失败 (status=%s)", session.status)
        return 1

    acct = register_login_result(session, bridge.accounts, config_path=config)
    if not acct:
        logger.error("保存账号失败")
        return 1

    logger.info("绑定成功 account_id=%s", acct.account_id)
    print(f"绑定成功！account_id={acct.account_id}")

    # Step 2: capture context_token — iLink requires user to message bot first
    from .wechat import wait_for_context_token, send_text

    print("\n请在微信中向此Bot发送任意一条消息（如\"你好\"）以激活推送", flush=True)
    got_ctx = wait_for_context_token(acct, timeout_s=120, config_path=config)

    if not got_ctx:
        print("未收到消息，跳过验证。请稍后手动发一条消息给Bot再运行 push")
        return 0

    # Step 3: send confirmation
    result = send_text(acct, "garden-world 绑定成功！兑换码将推送到此对话。")
    if result == "ok":
        print("绑定完成 — 测试消息已发送到微信")
    else:
        logger.warning("验证消息发送失败 (result=%s)，但绑定已保存", result)

    return 0


def run_login(headless: bool = False) -> int:
    """Login flow — opens browser for QR code scan.

    With ``headless=True``, no visible window is opened; the QR code
    screenshot is emitted via ``QR_IMAGE`` on stdout so the caller
    can relay it to the end-user.
    """
    settings = Settings.from_env()

    # Try to push QR to bound WeChat accounts
    bridge = _get_bridge(settings)

    # Wrap the login to intercept QR screenshots
    if bridge:
        from . import browser as _browser
        _original_fn = _browser._screenshot_login_wall

        def _patched_screenshot(page, dest):
            _original_fn(page, dest)
            if dest.exists():
                logger.info("推送登录二维码到微信…")
                bridge.broadcast_image(str(dest))
                bridge.broadcast_text("小红书登录二维码，请尽快用小红书App扫码")

        _browser._screenshot_login_wall = _patched_screenshot

    ok = login(settings.profile_dir, headless=headless)

    if bridge:
        if ok:
            bridge.broadcast_text("小红书登录成功，兑换码服务已恢复")
            logger.info("登录成功，已通知微信")
        else:
            bridge.broadcast_text("小红书登录超时，需要手动处理")
            logger.error("登录超时，已通知微信")

    return 0 if ok else 1


def run_push(force: bool = False) -> int:
    """Push today's codes via WeChat (skips dedup when force=True).

    Uses cached state data — does NOT launch the browser.
    Run ``--now`` first to fetch fresh codes.
    """
    settings = Settings.from_env()
    now = _now(settings.timezone)
    state = _read_state(settings.state_path)

    today_key = now.strftime("%Y-%m-%d")
    day_sent = state.get("sent", {}).get(today_key, {})

    if not day_sent:
        print(f"今日 ({today_key}) 没有缓存的兑换码，请先运行: garden-world --now")
        return 1

    bridge = _get_bridge(settings)
    if not bridge:
        print("未配置微信账号，请先运行: garden-world bind")
        return 1

    # Build push text from cached codes — always show all 5 slots
    date_str = f"{now.month}.{now.day}"
    windows = state.get("cached_timed_windows", {})
    lines = [f"🌸 花园世界兑换码 {date_str}"]
    lines.append(f"✅ 周码: {day_sent.get('weekly') or '(未更新)'}")
    lines.append(f"✅ 通用码: {day_sent.get('universal') or '(未更新)'}")
    for i in range(1, 4):
        code = day_sent.get(f"timed_{i}")
        w = windows.get(str(i), {})
        time_part = f" ({w['start']}-{w['end']})" if w.get("start") else " (未更新)"
        if code:
            lines.append(f"⏱️ 限时码{i}{time_part}: {code}")
        else:
            lines.append(f"⏱️ 限时码{i}{time_part}: (未更新)")

    push_text = "\n".join(lines)
    print(push_text)

    if not force and not _has_unsent_codes(state, now):
        print("\n所有已获取的兑换码均已推送，使用 --force 强制重发")
        return 0

    # Find the latest bare code for second message (last timed > universal > weekly)
    latest_bare = None
    for i in range(3, 0, -1):
        if day_sent.get(f"timed_{i}"):
            latest_bare = day_sent[f"timed_{i}"]
            break
    if not latest_bare:
        latest_bare = day_sent.get("universal") or day_sent.get("weekly")

    print()
    results = bridge.broadcast_text(push_text)

    # Second message: latest bare code for easy copy-paste
    if latest_bare:
        bridge.broadcast_text(latest_bare)

    ok_count = sum(1 for v in results.values() if v)
    fail_count = len(results) - ok_count

    for acct_id, ok in results.items():
        tag = "✓" if ok else "✗"
        print(f"  {tag} {acct_id}")

    if bridge.session_expired:
        print("微信 session 已过期，请运行 garden-world bind 重新绑定")
        return 1
    if bridge.needs_context:
        print("微信 context_token 已失效，请在微信中向Bot发送一条消息后重试")
        return 1

    if fail_count:
        print(f"推送完成: {ok_count} 成功, {fail_count} 失败")
        return 1

    print(f"推送完成: {ok_count} 个账号")
    return 0


def _has_unsent_codes(state: dict, now: datetime) -> bool:
    """Check if --now found codes that haven't been pushed yet (heuristic)."""
    # If run() built notifications but they were never broadcast, this is hard
    # to detect purely from state. For safety, always allow --force.
    return False


def run_enrich() -> int:
    """Re-search to enrich time windows, then push the enriched result.

    Forces a full slow-path search (skips cached post URL) so that
    multiple sources can contribute time windows via cross-validation.
    Always pushes regardless of dedup state.
    """
    settings = Settings.from_env()
    now = _now(settings.timezone)
    state = _read_state(settings.state_path)

    # Force slow-path search to gather multiple sources
    bundle = _find_today_bundle(settings, now, state, force_refresh=True)
    if not bundle:
        print("未找到今日帖子")
        return 1

    # Check enrichment result
    has_windows = sum(1 for t in bundle.timed if t.start and t.end)
    approx_only = sum(1 for t in bundle.timed if t.start and not t.end)
    missing = sum(1 for t in bundle.timed if not t.start)
    print(f"时间窗口: {has_windows} 精确, {approx_only} 近似, {missing} 缺失")

    # Update state with enriched data
    _build_notifications(bundle, state, now)
    _write_state(settings.state_path, state)

    report = _format_codes(bundle, now)
    print(report)

    # Push via WeChat
    bridge = _get_bridge(settings)
    if not bridge:
        print("未配置微信账号")
        return 0

    results = bridge.broadcast_text(report)

    # Second message: latest bare code
    latest_bare = None
    for t in reversed(bundle.timed):
        if t.code:
            latest_bare = t.code
            break
    if not latest_bare:
        latest_bare = bundle.universal_code or bundle.weekly_code
    if latest_bare:
        bridge.broadcast_text(latest_bare)

    ok_count = sum(1 for v in results.values() if v)
    fail_count = len(results) - ok_count
    for acct_id, ok in results.items():
        tag = "✓" if ok else "✗"
        print(f"  {tag} {acct_id}")

    if bridge.session_expired:
        print("微信 session 已过期，请运行 garden-world bind 重新绑定")
        return 1
    if bridge.needs_context:
        print("微信 context_token 已失效，请在微信中向Bot发送一条消息后重试")
        return 1

    print(f"推送完成: {ok_count} 个账号" + (f", {fail_count} 失败" if fail_count else ""))
    return 1 if fail_count else 0


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging for CLI usage."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    # Force line-buffered stdout
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="garden-world — auto redeem code fetcher")
    sub = parser.add_subparsers(dest="command")

    parser.add_argument("--now", action="store_true", help="run once for current time")
    parser.add_argument("--force-refresh", action="store_true", help="force re-search")
    parser.add_argument("--auto-login", action="store_true",
                        help="登录过期时自动启动 headless 登录流程")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    sub.add_parser("login", help="扫码登录小红书").add_argument(
        "--headless", action="store_true",
        help="无头模式：通过 QR_IMAGE 输出二维码",
    )

    sub.add_parser("daemon", help="启动守护进程模式（内置调度 + 微信推送 + 自动回复）")
    sub.add_parser("bind", help="绑定新的微信 ClawBot 账号（最多5个）")
    sub.add_parser("import-wechat", help="从 OpenClaw 导入微信账号")

    push_parser = sub.add_parser("push", help="立即推送今日兑换码到微信")
    push_parser.add_argument("--force", action="store_true",
                             help="强制重发所有码（忽略已推送记录）")

    sub.add_parser("enrich", help="重新搜索多源补充时间窗口并推送")

    args = parser.parse_args()
    _setup_logging(verbose=getattr(args, 'verbose', False))

    if args.command == "login":
        raise SystemExit(run_login(headless=args.headless))
    elif args.command == "daemon":
        raise SystemExit(run_daemon())
    elif args.command == "bind":
        raise SystemExit(run_bind())
    elif args.command == "import-wechat":
        raise SystemExit(run_import_wechat())
    elif args.command == "push":
        raise SystemExit(run_push(force=getattr(args, 'force', False)))
    elif args.command == "enrich":
        raise SystemExit(run_enrich())
    else:
        raise SystemExit(run(now_mode=args.now, force_refresh=args.force_refresh,
                             auto_login=getattr(args, 'auto_login', False)))


if __name__ == "__main__":
    main()
