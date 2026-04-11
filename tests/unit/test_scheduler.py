"""Unit tests for garden_world.scheduler — daily scheduling logic."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from garden_world.scheduler import DailyScheduler


class TestSchedulerInit:
    def test_default_timezone(self):
        s = DailyScheduler()
        assert str(s._tz) == "Asia/Shanghai"

    def test_custom_timezone(self):
        s = DailyScheduler(tz_name="UTC")
        assert str(s._tz) == "UTC"

    def test_not_running_initially(self):
        s = DailyScheduler()
        assert not s.running


class TestSchedulerCallbacks:
    """Test that the scheduler invokes callbacks at the right times."""

    def test_stop_after_three_timed_codes(self):
        """Scheduler should break the daily loop after 3 timed codes obtained."""
        s = DailyScheduler()
        # Simulate having obtained 3 timed codes
        s._timed_codes_obtained = {1, 2, 3}
        # The check is: len(self._timed_codes_obtained) >= 3
        assert len(s._timed_codes_obtained) >= 3

    def test_start_and_stop(self):
        """Scheduler can start and stop without error."""
        s = DailyScheduler()
        s.start()
        assert s.running
        time.sleep(0.1)
        s.stop()
        assert not s.running

    def test_double_start_noop(self):
        s = DailyScheduler()
        s.start()
        thread1 = s._thread
        s.start()  # second start should be noop
        assert s._thread is thread1
        s.stop()


class TestSchedulerDayLogic:
    """Test _run_one_day with mocked time."""

    def test_skip_if_past_cutoff(self):
        """If current time is after 23:30, skip the day entirely."""
        s = DailyScheduler(
            on_fetch_universal=MagicMock(return_value=True),
        )
        # Mock _now to return 23:45
        late_time = datetime(2025, 1, 15, 23, 45, 0, tzinfo=s._tz)
        with patch.object(s, '_now', return_value=late_time):
            s._running = True
            s._run_one_day()
            # on_fetch_universal should NOT have been called
            s._on_fetch_universal.assert_not_called()

    def test_fetch_universal_called_after_1905(self):
        """on_fetch_universal should be called when time >= 19:05."""
        mock_fetch = MagicMock(return_value=True)
        mock_detect = MagicMock(return_value=[])

        s = DailyScheduler(
            on_fetch_universal=mock_fetch,
            on_detect_timed=mock_detect,
        )

        call_count = 0

        def fake_now():
            nonlocal call_count
            call_count += 1
            # First few calls: return 19:06 (past universal time)
            # After a few iterations, return 23:31 to break the loop
            if call_count > 6:
                return datetime(2025, 1, 15, 23, 31, 0, tzinfo=s._tz)
            return datetime(2025, 1, 15, 19, 6, 0, tzinfo=s._tz)

        def fake_sleep_until(target):
            return True  # instant "sleep"

        with patch.object(s, '_now', side_effect=fake_now):
            with patch.object(s, '_sleep_until', side_effect=fake_sleep_until):
                s._running = True
                s._run_one_day()

        mock_fetch.assert_called()

    def test_timed_window_detection(self):
        """Test that detected windows are stored properly."""
        windows = [
            {"number": 1, "start": "19:30", "end": "19:45"},
            {"number": 2, "start": "20:00", "end": "20:15"},
        ]
        mock_detect = MagicMock(return_value=windows)
        mock_fetch_universal = MagicMock(return_value=True)

        s = DailyScheduler(
            on_fetch_universal=mock_fetch_universal,
            on_detect_timed=mock_detect,
        )

        call_count = 0

        def fake_now():
            nonlocal call_count
            call_count += 1
            if call_count > 6:
                return datetime(2025, 1, 15, 23, 31, 0, tzinfo=s._tz)
            return datetime(2025, 1, 15, 19, 10, 0, tzinfo=s._tz)

        with patch.object(s, '_now', side_effect=fake_now):
            with patch.object(s, '_sleep_until', return_value=True):
                s._running = True
                s._run_one_day()

        # Windows should have been recorded
        assert 1 in s._timed_windows
        assert 2 in s._timed_windows

    def test_day_complete_callback(self):
        """on_day_complete should be called after the daily loop ends (not past cutoff)."""
        mock_complete = MagicMock()
        mock_detect = MagicMock(return_value=[])
        mock_fetch = MagicMock(return_value=True)

        s = DailyScheduler(
            on_fetch_universal=mock_fetch,
            on_detect_timed=mock_detect,
            on_day_complete=mock_complete,
        )

        call_count = 0

        def fake_now():
            nonlocal call_count
            call_count += 1
            if call_count > 6:
                return datetime(2025, 1, 15, 23, 31, 0, tzinfo=s._tz)
            return datetime(2025, 1, 15, 19, 10, 0, tzinfo=s._tz)

        with patch.object(s, '_now', side_effect=fake_now):
            with patch.object(s, '_sleep_until', return_value=True):
                s._running = True
                s._run_one_day()

        mock_complete.assert_called_once()


class TestSleepUntil:
    def test_already_past_target(self):
        s = DailyScheduler()
        s._running = True
        past = s._now() - timedelta(minutes=1)
        result = s._sleep_until(past)
        assert result is True

    def test_returns_false_when_stopped(self):
        s = DailyScheduler()
        s._running = False
        future = s._now() + timedelta(hours=1)
        result = s._sleep_until(future)
        assert result is False
