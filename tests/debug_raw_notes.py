"""Inspect raw note content for specific dates to debug parsing."""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from garden_world.main import _parse_codes, _score_bundle
from garden_world.browser import _open_persistent, _extract_note_text
from garden_world.config import Settings
from playwright.sync_api import sync_playwright

settings = Settings.from_env()

# Test dates that had issues
for date_hint in ["3.28", "3.29"]:
    print(f"\n{'='*60}")
    print(f"  RAW NOTES FOR: {date_hint}")
    print(f"{'='*60}")

    search_kw = f"{settings.keyword} {date_hint}"
    search_url = (
        "https://www.xiaohongshu.com/search_result"
        f"?keyword={search_kw}&source=web_search_result_notes"
    )

    with sync_playwright() as pw:
        ctx = _open_persistent(pw, settings.profile_dir, headless=True)
        try:
            page = ctx.new_page()
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(8)

            links = page.eval_on_selector_all(
                'a[href*="/explore/"]', "els => els.map(el => el.href)"
            )
            note_ids = []
            seen = set()
            for href in links:
                m = re.search(r"/explore/([a-f0-9]{24})", href)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    note_ids.append(m.group(1))

            for idx, nid in enumerate(note_ids[:6]):
                url = f"https://www.xiaohongshu.com/explore/{nid}"
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
                text = _extract_note_text(page)
                if not text or len(text) < 20:
                    continue

                # Show full text for posts that mention this date
                if date_hint.replace(".", "") in text.replace(".", "") or date_hint in text:
                    print(f"\n--- Note [{idx+1}] {url} ---")
                    # Show first 800 chars
                    print(text[:800])
                    print("---")

                    bundle = _parse_codes(text, url)
                    if bundle:
                        score = _score_bundle(bundle)
                        print(f"  PARSED: date={bundle.date_label} score={score}")
                        print(f"  uni={bundle.universal_code}")
                        print(f"  wk={bundle.weekly_code}")
                        for t in bundle.timed:
                            print(f"  timed {t.number}: {t.start}~{t.end} code={repr(t.code)}")
                    else:
                        print("  PARSE FAILED")
        finally:
            ctx.close()
