"""Integration test: headless QR screenshot capture.

Uses a FRESH (empty) profile so the XHS login wall appears.
Requires a live browser + network.
Run with: ``pytest tests/integration/test_headless_qr.py -v -m integration``
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import garden_world.browser as bmod


@pytest.fixture()
def fresh_profile(tmp_path):
    profile = tmp_path / "fresh_profile"
    profile.mkdir()
    yield profile
    shutil.rmtree(profile, ignore_errors=True)


@pytest.mark.integration
def test_headless_qr_screenshot(fresh_profile):
    """Headless Playwright can screenshot the XHS login wall."""
    with sync_playwright() as pw:
        ctx = bmod._open_persistent(pw, fresh_profile, headless=True, block_resources=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            url = (
                "https://www.xiaohongshu.com/search_result"
                "?keyword=%E8%8A%B1%E5%9B%AD%E4%B8%96%E7%95%8C+%E5%85%91%E6%8D%A2%E7%A0%81"
                "&source=web_search_result_notes"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            import time; time.sleep(5)

            body_text = page.inner_text("body")
            assert "登录" in body_text, "Expected login wall on fresh profile"

            qr_path = fresh_profile / "qr_test.png"
            bmod._screenshot_login_wall(page, qr_path)
            assert qr_path.exists(), "QR screenshot file should be created"
            assert qr_path.stat().st_size > 0
        finally:
            ctx.close()
