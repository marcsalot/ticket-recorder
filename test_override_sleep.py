#!/usr/bin/env python3
"""
Tests: override detection and sleep_until behavior in recorder.py.

Key scenario under test:
  The recorder is sleeping through a skipped slot (e.g. Fox Sports Radio).
  A new entry is added to overrides.json mid-sleep.
  The recorder must detect it within one 60-second tick and start recording.

Run:
    pip install pytest
    pytest test_override_sleep.py -v
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# recorder.py lives alongside this file
sys.path.insert(0, str(Path(__file__).parent))
import recorder

TZ = recorder.TIMEZONE


# ── Helpers ───────────────────────────────────────────────────────────────────

def now():
    return datetime.now(TZ)


def fmt(dt: datetime) -> str:
    return dt.strftime(recorder.OVERRIDES_TIME_FMT)


def future(hours=1):
    return now() + timedelta(hours=hours)


def past(minutes=30):
    return now() - timedelta(minutes=minutes)


def write_overrides(path: Path, entries: list) -> None:
    path.write_text(json.dumps(entries))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_shutdown():
    """Guarantee _shutdown is False before and after every test."""
    recorder._shutdown = False
    yield
    recorder._shutdown = False


# ── sleep_until ───────────────────────────────────────────────────────────────

class TestSleepUntil:

    def test_breaks_after_one_tick_when_override_immediately_active(self):
        """
        If get_active_override returns a result after the very first sleep,
        sleep_until must exit after exactly one call to time.sleep.
        """
        end_dt = future(hours=2)
        sleep_calls = []

        with patch("recorder.time") as mock_time, \
             patch("recorder.get_active_override",
                   return_value=("Cowboys Game", future())):
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            recorder.sleep_until(end_dt)

        assert len(sleep_calls) == 1, (
            f"Expected exactly 1 sleep before override detected, got {len(sleep_calls)}"
        )
        # Verify sleep was for the full 60-second tick (remaining >> 60)
        assert sleep_calls[0] == pytest.approx(60, abs=2)

    def test_polls_overrides_after_every_tick(self):
        """
        get_active_override must be called after each sleep tick, not once.
        Here the override only becomes active on the 3rd check, verifying
        that the first two checks actually occurred.
        """
        end_dt = future(hours=2)
        check_count = [0]

        def side_effect(_now):
            check_count[0] += 1
            return ("Test Override", future()) if check_count[0] >= 3 else None

        with patch("recorder.time") as mock_time, \
             patch("recorder.get_active_override", side_effect=side_effect):
            mock_time.sleep.return_value = None
            recorder.sleep_until(end_dt)

        assert check_count[0] == 3, (
            f"Expected 3 override checks (2 misses then 1 hit), got {check_count[0]}"
        )
        assert mock_time.sleep.call_count == 3

    def test_runs_to_completion_when_no_override(self):
        """
        With no override ever active, sleep_until should run until
        end_dt passes naturally (remaining <= 0).
        """
        end_dt = now() + timedelta(seconds=0.05)   # real near-future time

        with patch("recorder.get_active_override", return_value=None):
            recorder.sleep_until(end_dt)             # must not hang

    def test_exits_immediately_when_shutdown_set_during_sleep(self):
        """
        If _shutdown is set to True while inside time.sleep, sleep_until
        should not enter another tick.
        """
        end_dt = future(hours=2)

        def set_shutdown(_secs):
            recorder._shutdown = True

        with patch("recorder.time") as mock_time, \
             patch("recorder.get_active_override", return_value=None):
            mock_time.sleep.side_effect = set_shutdown
            recorder.sleep_until(end_dt)

        mock_time.sleep.assert_called_once()

    def test_sleep_duration_capped_at_60_seconds(self):
        """
        Even when many hours remain, each sleep tick must be at most 60 s
        so that overrides are checked frequently.
        """
        end_dt = future(hours=10)
        sleep_calls = []

        with patch("recorder.time") as mock_time, \
             patch("recorder.get_active_override",
                   return_value=("Override", future())):
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            recorder.sleep_until(end_dt)

        assert all(s <= 60 for s in sleep_calls), (
            f"A sleep tick exceeded 60 s: {sleep_calls}"
        )


# ── get_active_override ───────────────────────────────────────────────────────

class TestGetActiveOverride:

    def test_returns_none_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", tmp_path / "missing.json")
        assert recorder.get_active_override(now()) is None

    def test_returns_none_for_empty_file(self, tmp_path, monkeypatch):
        f = tmp_path / "overrides.json"
        write_overrides(f, [])
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", f)
        assert recorder.get_active_override(now()) is None

    def test_returns_none_for_future_override(self, tmp_path, monkeypatch):
        entries = [{"name": "Future Show",
                    "start": fmt(future(1)), "end": fmt(future(2))}]
        f = tmp_path / "overrides.json"
        write_overrides(f, entries)
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", f)
        assert recorder.get_active_override(now()) is None

    def test_returns_none_for_expired_override(self, tmp_path, monkeypatch):
        entries = [{"name": "Past Show",
                    "start": fmt(past(60)), "end": fmt(past(30))}]
        f = tmp_path / "overrides.json"
        write_overrides(f, entries)
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", f)
        assert recorder.get_active_override(now()) is None

    def test_returns_active_override(self, tmp_path, monkeypatch):
        entries = [{"name": "Stars Playoff",
                    "start": fmt(past(30)), "end": fmt(future(2))}]
        f = tmp_path / "overrides.json"
        write_overrides(f, entries)
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", f)

        result = recorder.get_active_override(now())
        assert result is not None
        name, end_dt = result
        assert name == "Stars Playoff"
        assert end_dt > now()

    def test_skips_malformed_entry_returns_next_valid(self, tmp_path, monkeypatch):
        entries = [
            {"name": "Broken"},                                             # missing start/end
            {"name": "Good Entry", "start": fmt(past(10)), "end": fmt(future(1))},
        ]
        f = tmp_path / "overrides.json"
        write_overrides(f, entries)
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", f)

        result = recorder.get_active_override(now())
        assert result is not None
        assert result[0] == "Good Entry"

    def test_reloads_file_on_every_call(self, tmp_path, monkeypatch):
        """
        A second call to get_active_override must re-read the file from disk,
        picking up entries written after the first call.
        """
        f = tmp_path / "overrides.json"
        write_overrides(f, [])                                      # empty on first call
        monkeypatch.setattr(recorder, "OVERRIDES_FILE", f)

        assert recorder.get_active_override(now()) is None          # nothing yet

        write_overrides(f, [                                         # write active entry
            {"name": "Live Now", "start": fmt(past(5)), "end": fmt(future(1))}
        ])

        result = recorder.get_active_override(now())                 # must see new entry
        assert result is not None
        assert result[0] == "Live Now"


# ── Integration: override triggers recording during a skipped slot ────────────

class TestOverrideDuringSkippedSlot:

    def test_override_wakes_recorder_mid_sleep(self, tmp_path):
        """
        Full record_loop scenario:

          Tick  Who calls get_active_override       Returns
          ────  ──────────────────────────────────  ───────────────────────
          1     record_loop top-of-loop (iter 1)   None  →  enters sleep_until
          2     sleep_until tick 1                  None  →  keep sleeping
          3     sleep_until tick 2                  override  →  break early
          4     record_loop top-of-loop (iter 2)   override  →  record it

        Assertions:
          - run_ffmpeg called exactly once
          - output filename contains the override show name
          - output filename does NOT contain "Fox_Sports_Radio"
        """
        skipped_end  = future(hours=2)
        override_end = future(hours=1)
        override     = ("Stars Playoff Game", override_end)

        # None on calls 1-2, override on call 3+
        call_count   = [0]

        def mock_get_active_override(_now):
            call_count[0] += 1
            return override if call_count[0] >= 3 else None

        recorded_files = []

        def mock_run_ffmpeg(cmd):
            recorded_files.append(Path(cmd[-1]).name)
            recorder._shutdown = True           # stop loop after first recording
            return 0

        with patch("recorder.get_active_override", side_effect=mock_get_active_override), \
             patch("recorder.get_show_info",
                   return_value=("Fox Sports Radio", skipped_end, False)), \
             patch("recorder.run_ffmpeg", side_effect=mock_run_ffmpeg), \
             patch("recorder.time") as mock_time:

            mock_time.sleep.return_value = None # instant sleeps throughout

            recorder.record_loop(tmp_path, use_schedule=True)

        # ── Assertions ────────────────────────────────────────────────────────

        assert len(recorded_files) == 1, (
            f"Expected 1 recording, got {len(recorded_files)}: {recorded_files}"
        )

        fname = recorded_files[0]
        assert "Stars_Playoff_Game" in fname, (
            f"Output filename should contain override show name, got: {fname}"
        )
        assert "Fox_Sports_Radio" not in fname, (
            f"Should not have recorded the skipped slot, got: {fname}"
        )

        # Override was detected on tick 3, meaning 2 earlier checks happened
        assert call_count[0] >= 3, (
            f"Override should have been polled mid-sleep (call 3+), "
            f"but only {call_count[0]} calls were made"
        )

        # sleep_until was entered and slept at least twice before breaking
        assert mock_time.sleep.call_count >= 2, (
            f"Expected ≥2 sleep ticks inside sleep_until, got {mock_time.sleep.call_count}"
        )

    def test_no_recording_when_no_override_and_slot_skipped(self, tmp_path):
        """
        If a skipped slot runs to completion with no override, run_ffmpeg
        should never be called.
        """
        # Use a real near-future end to let sleep_until exit naturally
        skipped_end = now() + timedelta(seconds=0.05)

        with patch("recorder.get_active_override", return_value=None), \
             patch("recorder.get_show_info",
                   return_value=("Fox Sports Radio", skipped_end, False)), \
             patch("recorder.run_ffmpeg") as mock_ffmpeg:

            # After sleep_until returns, the next loop iteration should also
            # see no override and no recordable show — set shutdown to prevent
            # an infinite loop.
            original_get_show_info = recorder.get_show_info
            call_count = [0]

            def stopping_get_show_info(_now):
                call_count[0] += 1
                if call_count[0] > 1:
                    recorder._shutdown = True
                return ("Fox Sports Radio", now() + timedelta(seconds=0.05), False)

            with patch("recorder.get_show_info", side_effect=stopping_get_show_info):
                recorder.record_loop(tmp_path, use_schedule=True)

        mock_ffmpeg.assert_not_called()
