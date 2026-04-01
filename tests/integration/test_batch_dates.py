"""Integration test: batch search across multiple dates.

Requires a live browser + authenticated XHS session.
Run with: ``pytest tests/integration/test_batch_dates.py -v -m integration``
"""
from __future__ import annotations

import pytest

from garden_world.browser import search_and_fetch
from garden_world.config import Settings
from garden_world.main import _parse_codes, _score_bundle

DATES = ["3.22", "3.24", "3.26", "3.28", "3.29"]


@pytest.mark.integration
def test_batch_dates():
    """Search 5 historical dates and verify parsing succeeds for most."""
    settings = Settings.from_env()
    ok_count = 0

    for date_hint in DATES:
        pairs = search_and_fetch(
            settings.keyword, date_hint, limit=8, profile_dir=settings.profile_dir,
        )

        best = None
        best_score = -1
        for url, text, *_ in pairs:
            bundle = _parse_codes(text, url)
            if not bundle:
                continue
            if bundle.date_label and bundle.date_label != date_hint:
                continue
            score = _score_bundle(bundle)
            if score > best_score:
                best_score = score
                best = bundle

        if best:
            ok_count += 1

    # We expect at least 3 out of 5 dates to work
    assert ok_count >= 3, f"Only {ok_count}/{len(DATES)} dates found valid bundles"
