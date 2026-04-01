"""Quick test: can headless Playwright screenshot the XHS login wall?

Uses a FRESH (empty) profile so the login wall actually appears.
Also dumps DOM info to help debug selector issues.
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from playwright.sync_api import sync_playwright
import garden_world.browser as bmod

# Use a temporary empty profile — NOT the real one (which is already logged in)
fresh_profile = Path(".garden_world/_test_fresh_profile")
if fresh_profile.exists():
    shutil.rmtree(fresh_profile)
fresh_profile.mkdir(parents=True, exist_ok=True)

print("Starting headless Playwright with FRESH profile...", flush=True)
with sync_playwright() as pw:
    ctx = bmod._open_persistent(pw, fresh_profile, headless=True, block_resources=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        url = (
            "https://www.xiaohongshu.com/search_result"
            "?keyword=%E8%8A%B1%E5%9B%AD%E4%B8%96%E7%95%8C+%E5%85%91%E6%8D%A2%E7%A0%81"
            "&source=web_search_result_notes"
        )
        print("Navigating to XHS search page...", flush=True)
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(5)

        # --- Debug: dump page state ---
        body_text = page.inner_text("body")
        has_login_wall = "登录" in body_text
        print(f"Has '登录' in body: {has_login_wall}", flush=True)
        print(f"Body text (first 500 chars): {body_text[:500]}", flush=True)

        # Dump all visible elements that might be the login overlay
        selectors_to_check = [
            '[class*="login"]',
            '[class*="overlay"]',
            '[class*="mask"]',
            '[class*="modal"]',
            '[class*="qrcode"]',
            '[class*="qr-code"]',
            '[class*="qr_code"]',
            '[id*="login"]',
            '[id*="qr"]',
            'img[src*="qr"]',
            'canvas',               # QR codes are often rendered on canvas
            'iframe',               # login may be in iframe
        ]
        print("\n--- Selector scan ---", flush=True)
        for sel in selectors_to_check:
            try:
                els = page.query_selector_all(sel)
                if els:
                    for i, el in enumerate(els):
                        vis = el.is_visible()
                        tag = el.evaluate("e => e.tagName")
                        cls = el.evaluate("e => e.className")
                        eid = el.evaluate("e => e.id")
                        bbox = el.bounding_box()
                        print(
                            f"  {sel} [{i}]: tag={tag} class={cls!r} id={eid!r} "
                            f"visible={vis} bbox={bbox}",
                            flush=True,
                        )
            except Exception as exc:
                print(f"  {sel}: ERROR {exc}", flush=True)

        # --- Take full-page screenshot regardless ---
        full_path = fresh_profile / "full_page.png"
        page.screenshot(path=str(full_path), full_page=True)
        print(f"\nFull-page screenshot: {full_path} ({full_path.stat().st_size} bytes)", flush=True)

        # --- Also try the production screenshot function ---
        qr_path = fresh_profile / "qr_test.png"
        bmod._screenshot_login_wall(page, qr_path)

        if qr_path.exists():
            size = qr_path.stat().st_size
            print(f"_screenshot_login_wall result: {qr_path} ({size} bytes)", flush=True)
        else:
            print("_screenshot_login_wall: no file created", flush=True)

    finally:
        ctx.close()

# Clean up temp profile
shutil.rmtree(fresh_profile, ignore_errors=True)
print("\nDone. Check full_page.png and qr_test.png above.", flush=True)
