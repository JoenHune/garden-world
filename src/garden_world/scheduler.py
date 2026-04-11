"""Built-in scheduler — replaces external cron with APScheduler.

Daily schedule (Asia/Shanghai):
  19:05         → fetch universal code + push
  19:00-23:30   → every 5 min, detect timed-code windows
  dynamic       → window_start + 5min, fetch specific timed code
  23:30         → stop daily polling

Stops after either:
  - all 3 timed codes obtained and pushed, OR
  - 23:30 reached.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from zoneinfo import ZoneInfo

logger = logging.getLogger("garden_world.scheduler")


class DailyScheduler:
    """Single-day task scheduler using plain threading + sleep.

    Lower-weight than APScheduler; no extra dependency needed.
    """

    def __init__(
        self,
        *,
        tz_name: str = "Asia/Shanghai",
        on_fetch_universal: object = None,  # callable() → bool
        on_detect_timed: object = None,  # callable() → list[dict] (windows)
        on_fetch_timed: object = None,  # callable(window_number: int) → bool
        on_day_complete: object = None,  # callable()
    ):
        self._tz = ZoneInfo(tz_name)
        self._on_fetch_universal = on_fetch_universal
        self._on_detect_timed = on_detect_timed
        self._on_fetch_timed = on_fetch_timed
        self._on_day_complete = on_day_complete

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._timed_codes_obtained: set[int] = set()
        self._timed_windows: dict[int, dict] = {}  # number → {start, end}
        self._universal_done = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="scheduler")
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Scheduler stopped")

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _sleep_until(self, target: datetime) -> bool:
        """Sleep until target time. Returns False if stopped early."""
        while self._running:
            remaining = (target - self._now()).total_seconds()
            if remaining <= 0:
                return True
            import time
            time.sleep(min(remaining, 5.0))  # check every 5s
        return False

    def _today_at(self, hour: int, minute: int) -> datetime:
        now = self._now()
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _run_loop(self) -> None:
        """Main scheduling loop — runs forever, one day at a time."""
        while self._running:
            self._run_one_day()

            # Sleep until next day's 18:55 (5 min before first task)
            now = self._now()
            tomorrow_start = (now + timedelta(days=1)).replace(
                hour=18, minute=55, second=0, microsecond=0,
            )
            logger.info("Day complete. Next run at %s", tomorrow_start.isoformat())
            self._sleep_until(tomorrow_start)

    def _run_one_day(self) -> None:
        """Execute one day's schedule."""
        self._timed_codes_obtained.clear()
        self._timed_windows.clear()

        now = self._now()
        cutoff = self._today_at(23, 30)
        universal_time = self._today_at(19, 5)
        poll_start = self._today_at(19, 0)

        # If we start after cutoff, skip today
        if now > cutoff:
            return

        # Wait for poll_start if needed
        if now < poll_start:
            logger.info("Waiting until 19:00 to begin...")
            if not self._sleep_until(poll_start):
                return

        self._universal_done = False

        while self._running and self._now() < cutoff:
            now = self._now()

            # Fetch universal code once it's time
            if not self._universal_done and now >= universal_time:
                if callable(self._on_fetch_universal):
                    try:
                        result = self._on_fetch_universal()
                        # Callback returns (bool, set[int]) — ok flag + sent timed numbers
                        if isinstance(result, tuple):
                            self._universal_done = result[0]
                            sent_timed = result[1] if len(result) > 1 else set()
                        else:
                            self._universal_done = bool(result)
                            sent_timed = set()
                        # Track timed codes found during universal fetch
                        for num in sent_timed:
                            if num not in self._timed_codes_obtained:
                                self._timed_codes_obtained.add(num)
                                logger.info("Timed code %d already sent (via universal fetch, %d/3)",
                                            num, len(self._timed_codes_obtained))
                    except Exception:
                        logger.exception("on_fetch_universal error")

            # Early exit if all codes already obtained
            if len(self._timed_codes_obtained) >= 3:
                logger.info("All 3 timed codes obtained, stopping today's polling")
                break

            # Detect timed windows
            if callable(self._on_detect_timed):
                try:
                    windows = self._on_detect_timed()
                    if windows:
                        for w in windows:
                            num = w.get("number", 0)
                            if num and num not in self._timed_windows:
                                self._timed_windows[num] = w
                                logger.info("Discovered timed window %d: %s~%s",
                                            num, w.get("start", "?"), w.get("end", "?"))
                except Exception:
                    logger.exception("on_detect_timed error")

            # Check if any timed codes are due
            for num, window in list(self._timed_windows.items()):
                if num in self._timed_codes_obtained:
                    continue

                # Determine if it's time to fetch this code
                start_str = window.get("start", "")
                should_fetch = False
                if not start_str:
                    # No time window known — try fetching immediately
                    # (code may already be in the bundle)
                    should_fetch = True
                else:
                    try:
                        h, m = start_str.split(":")
                        due_time = self._today_at(int(h), int(m)) + timedelta(minutes=5)
                        should_fetch = now >= due_time
                    except (ValueError, IndexError):
                        should_fetch = True  # malformed time, just try

                if should_fetch and callable(self._on_fetch_timed):
                    try:
                        ok = self._on_fetch_timed(num)
                        if ok:
                            self._timed_codes_obtained.add(num)
                            logger.info("Timed code %d obtained (%d/3)",
                                        num, len(self._timed_codes_obtained))
                    except Exception:
                        logger.exception("on_fetch_timed(%d) error", num)

            # Stop if all 3 timed codes obtained
            if len(self._timed_codes_obtained) >= 3:
                logger.info("All 3 timed codes obtained, stopping today's polling")
                break

            # Wait 5 minutes before next poll (but check stop flag)
            next_poll = now + timedelta(minutes=5)
            if not self._sleep_until(min(next_poll, cutoff)):
                return

        # Day complete callback
        if callable(self._on_day_complete):
            try:
                self._on_day_complete()
            except Exception:
                logger.exception("on_day_complete error")
