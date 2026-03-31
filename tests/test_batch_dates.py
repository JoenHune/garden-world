"""Batch test: search 5 random March dates and report parsing results."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from garden_world.main import _parse_codes, _score_bundle
from garden_world.browser import search_and_fetch
from garden_world.config import Settings

DATES = ["3.22", "3.24", "3.26", "3.28", "3.29"]

settings = Settings.from_env()
summary = []

for date_hint in DATES:
    print(f"\n{'='*60}")
    print(f"  DATE: {date_hint}")
    print(f"{'='*60}")

    pairs = search_and_fetch(
        settings.keyword, date_hint, limit=8, profile_dir=settings.profile_dir
    )
    print(f"  search returned {len(pairs)} notes")

    best = None
    best_score = -1
    all_bundles = []

    for i, (url, text) in enumerate(pairs):
        bundle = _parse_codes(text, url)
        if not bundle:
            continue
        if bundle.date_label and bundle.date_label != date_hint:
            # Wrong date — still show but skip
            print(f"  [{i+1}] date={bundle.date_label} (want {date_hint}), skip")
            continue

        score = _score_bundle(bundle)
        all_bundles.append((score, bundle))

        # Extract blogger from URL or title
        preview = bundle.post_title[:40]
        uni = bundle.universal_code or "?"
        wk = bundle.weekly_code or "?"
        timed_info = ", ".join(
            f"{t.number}:{t.start or '?'}{'✓' if t.code else '?'}"
            for t in bundle.timed
        )
        print(f"  [{i+1}] score={score} date={bundle.date_label} uni={uni[:15]} wk={wk[:15]} timed=[{timed_info}]")
        print(f"       title: {preview}")
        print(f"       url: {url}")

        if score > best_score:
            best_score = score
            best = bundle

    if best:
        print(f"\n  BEST: score={best_score} title={best.post_title[:50]}")
        print(f"  universal: {best.universal_code}")
        print(f"  weekly:    {best.weekly_code}")
        for t in best.timed:
            print(f"  timed {t.number}: {t.start}~{t.end} code={repr(t.code)}")
        summary.append({"date": date_hint, "ok": True, "score": best_score,
                        "url": best.post_url, "title": best.post_title[:50]})
    else:
        print(f"\n  NO VALID BUNDLE FOUND for {date_hint}")
        # Show what we got for debugging
        for i, (url, text) in enumerate(pairs[:2]):
            preview = text[:150].replace("\n", " | ")
            print(f"  raw [{i+1}]: {preview}")
        summary.append({"date": date_hint, "ok": False, "score": 0})

print(f"\n\n{'='*60}")
print("  SUMMARY")
print(f"{'='*60}")
for s in summary:
    status = "✓" if s["ok"] else "✗"
    msg = f"  {status} {s['date']}  score={s['score']}"
    if s.get("title"):
        msg += f"  {s['title']}"
    print(msg)

ok_count = sum(1 for s in summary if s["ok"])
print(f"\n  {ok_count}/{len(summary)} dates found valid bundles")
