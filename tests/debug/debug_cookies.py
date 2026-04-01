"""Check what cookies XHS sets WITHOUT logging in."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

profile = Path(".garden_world/test_profile")
profile.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        str(profile),
        headless=False,
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
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=30000)

        # Check cookies immediately (before any login)
        print("\n=== Cookies IMMEDIATELY after page load (NO LOGIN) ===")
        for c in ctx.cookies("https://www.xiaohongshu.com"):
            print(f"  {c['name']:30s} = {str(c['value'])[:40]}")

        time.sleep(5)
        print("\n=== Cookies after 5s wait (still NO LOGIN) ===")
        for c in ctx.cookies("https://www.xiaohongshu.com"):
            print(f"  {c['name']:30s} = {str(c['value'])[:40]}")

        cookie_names = {c["name"] for c in ctx.cookies("https://www.xiaohongshu.com")}
        print(f"\nweb_session present WITHOUT login? -> {'web_session' in cookie_names}")

        # Check page state
        body = page.inner_text("body")
        has_login_wall = "登录" in body[:200]
        print(f"Page shows login prompt? -> {has_login_wall}")
        print(f"\nBody first 300 chars:\n{body[:300]}")

        input("\n按 Enter 关闭浏览器...")
    finally:
        ctx.close()

import shutil
shutil.rmtree(str(profile), ignore_errors=True)
