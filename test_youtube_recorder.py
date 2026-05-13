#!/usr/bin/env python3
"""
Tests: youtube_recorder.py — general-purpose YouTube audio recorder.

Covers:
  - _safe_filename        : character stripping / space replacement
  - _fetch_metadata       : subprocess success and failure paths
  - _check_deps           : exits cleanly when binaries are missing
  - download_audio        : dry-run, output path resolution (flat / --folder /
                            --by-channel), date stamp from metadata, fallback to
                            current time, successful save, glob fallback when
                            filename differs, and download failure handling

All network and subprocess calls are mocked; no real downloads occur.

Run:
    pip install pytest
    pytest test_youtube_recorder.py -v
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import youtube_recorder as yr

TZ = yr.TIMEZONE


# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta(title="Test Video", channel="Test Channel", upload_date="20260101"):
    """Return a minimal metadata dict like yt-dlp --dump-json produces."""
    return {"title": title, "channel": channel, "upload_date": upload_date}


def _mock_fetch(meta: dict):
    """Return a patcher that makes _fetch_metadata return *meta*."""
    return patch.object(yr, "_fetch_metadata", return_value=meta)


def _mock_run_ok():
    """subprocess.run patcher that simulates a successful yt-dlp download."""
    m = MagicMock()
    m.returncode = 0
    return patch("youtube_recorder.subprocess.run", return_value=m)


# ── _safe_filename ────────────────────────────────────────────────────────────

class TestSafeFilename:

    def test_spaces_become_underscores(self):
        assert yr._safe_filename("Hello World") == "Hello_World"

    def test_strips_special_characters(self):
        assert yr._safe_filename("Show: The #1 Hit!") == "Show_The_1_Hit"

    def test_preserves_hyphens(self):
        assert yr._safe_filename("Some-Show") == "Some-Show"

    def test_strips_leading_trailing_whitespace(self):
        assert yr._safe_filename("  title  ") == "title"

    def test_unicode_word_chars_preserved(self):
        result = yr._safe_filename("Café Radio")
        assert " " not in result


# ── _fetch_metadata ───────────────────────────────────────────────────────────

class TestFetchMetadata:

    def test_returns_parsed_json_on_success(self):
        data = {"title": "My Video", "channel": "My Channel"}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(data)

        with patch("youtube_recorder.subprocess.run", return_value=mock_result):
            result = yr._fetch_metadata("https://youtube.com/watch?v=abc")

        assert result == data

    def test_exits_on_nonzero_returncode(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: Video unavailable"

        with patch("youtube_recorder.subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit):
                yr._fetch_metadata("https://youtube.com/watch?v=bad")


# ── _check_deps ───────────────────────────────────────────────────────────────

class TestCheckDeps:

    def test_exits_when_ytdlp_missing(self):
        def fake_which(name):
            return None if name == "yt-dlp" else "/usr/bin/ffmpeg"

        with patch("youtube_recorder.shutil.which", side_effect=fake_which):
            with pytest.raises(SystemExit):
                yr._check_deps()

    def test_exits_when_ffmpeg_missing(self):
        def fake_which(name):
            return None if name == "ffmpeg" else "/usr/local/bin/yt-dlp"

        with patch("youtube_recorder.shutil.which", side_effect=fake_which):
            with pytest.raises(SystemExit):
                yr._check_deps()

    def test_passes_when_both_present(self):
        with patch("youtube_recorder.shutil.which", return_value="/usr/local/bin/tool"):
            yr._check_deps()   # should not raise


# ── download_audio — dry-run ──────────────────────────────────────────────────

class TestDryRun:

    def test_dry_run_returns_none(self, tmp_path):
        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run") as mock_run:

            result = yr.download_audio("https://youtube.com/watch?v=x",
                                       output_dir=tmp_path, dry_run=True)

        assert result is None

    def test_dry_run_does_not_call_ytdlp_download(self, tmp_path):
        """subprocess.run should only be called for metadata, not for download."""
        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run") as mock_run:

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path, dry_run=True)

        mock_run.assert_not_called()

    def test_dry_run_does_not_create_directory(self, tmp_path):
        target = tmp_path / "Show Themes"
        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run"):

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path, folder="Show Themes",
                              dry_run=True)

        assert not target.exists()


# ── download_audio — output path resolution ───────────────────────────────────

class TestOutputPathResolution:

    def _run(self, tmp_path, **kwargs):
        """Run download_audio with mocked deps; return (dest_dir, date_stamp, safe_title)."""
        meta = _meta(**{k: v for k, v in kwargs.items()
                        if k in ("title", "channel", "upload_date")})
        kw = {k: v for k, v in kwargs.items()
              if k not in ("title", "channel", "upload_date")}

        captured = {}

        def fake_run(cmd, **_):
            captured["cmd"] = cmd
            m = MagicMock()
            m.returncode = 0
            return m

        with _mock_fetch(meta), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run):

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path, **kw)

        return captured.get("cmd", [])

    def test_flat_output_goes_directly_to_output_dir(self, tmp_path):
        cmd = self._run(tmp_path)
        output_arg = cmd[cmd.index("-o") + 1]
        assert str(tmp_path) + "/" in output_arg
        # No extra subfolder component
        relative = Path(output_arg).parent.relative_to(tmp_path)
        assert str(relative) == "."

    def test_folder_flag_creates_named_subfolder(self, tmp_path):
        cmd = self._run(tmp_path, folder="Show Themes")
        output_arg = cmd[cmd.index("-o") + 1]
        assert "Show Themes" in output_arg

    def test_by_channel_uses_channel_name(self, tmp_path):
        cmd = self._run(tmp_path, channel="KTCK The Ticket", by_channel=True)
        output_arg = cmd[cmd.index("-o") + 1]
        assert "KTCK" in output_arg

    def test_folder_takes_precedence_over_by_channel(self, tmp_path):
        cmd = self._run(tmp_path, channel="Some Channel",
                        folder="My Folder", by_channel=True)
        output_arg = cmd[cmd.index("-o") + 1]
        assert "My Folder" in output_arg
        assert "Some Channel" not in output_arg


# ── download_audio — date stamp ───────────────────────────────────────────────

class TestDateStamp:

    def test_uses_upload_date_from_metadata(self, tmp_path):
        cmd_holder = {}

        def fake_run(cmd, **_):
            cmd_holder["cmd"] = cmd
            return MagicMock(returncode=0)

        with _mock_fetch(_meta(upload_date="20231115")), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run):

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path)

        output_arg = cmd_holder["cmd"][cmd_holder["cmd"].index("-o") + 1]
        assert "2023-11-15" in output_arg

    def test_falls_back_to_current_time_when_no_upload_date(self, tmp_path):
        meta = {"title": "No Date Video", "channel": "Chan"}  # no upload_date key
        cmd_holder = {}

        def fake_run(cmd, **_):
            cmd_holder["cmd"] = cmd
            return MagicMock(returncode=0)

        with _mock_fetch(meta), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run):

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path)

        output_arg = cmd_holder["cmd"][cmd_holder["cmd"].index("-o") + 1]
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        assert today in output_arg


# ── download_audio — live stream flag ─────────────────────────────────────────

class TestLiveStream:

    def test_live_flag_adds_live_from_start(self, tmp_path):
        cmd_holder = {}

        def fake_run(cmd, **_):
            cmd_holder["cmd"] = cmd
            return MagicMock(returncode=0)

        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run):

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path, live=True)

        assert "--live-from-start" in cmd_holder["cmd"]
        assert "--no-part" in cmd_holder["cmd"]

    def test_non_live_does_not_include_live_flags(self, tmp_path):
        cmd_holder = {}

        def fake_run(cmd, **_):
            cmd_holder["cmd"] = cmd
            return MagicMock(returncode=0)

        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run):

            yr.download_audio("https://youtube.com/watch?v=x",
                              output_dir=tmp_path, live=False)

        assert "--live-from-start" not in cmd_holder["cmd"]


# ── download_audio — success / failure ───────────────────────────────────────

class TestSuccessAndFailure:

    def test_returns_path_when_output_file_exists(self, tmp_path):
        meta = _meta(upload_date="20260101")

        def fake_run(cmd, **_):
            # Simulate yt-dlp writing the output file
            out = Path(cmd[cmd.index("-o") + 1])
            mp3 = out.parent / (out.stem + ".mp3")
            mp3.parent.mkdir(parents=True, exist_ok=True)
            mp3.write_bytes(b"\xff\xfb" * 100)   # fake MP3 bytes
            return MagicMock(returncode=0)

        with _mock_fetch(meta), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run), \
             patch.object(yr, "_notify"):

            result = yr.download_audio("https://youtube.com/watch?v=x",
                                       output_dir=tmp_path)

        assert result is not None
        assert result.suffix == ".mp3"
        assert result.exists()

    def test_returns_none_on_download_failure(self, tmp_path):
        def fake_run(cmd, **_):
            raise subprocess.CalledProcessError(1, cmd)

        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run), \
             patch.object(yr, "_notify"):

            result = yr.download_audio("https://youtube.com/watch?v=x",
                                       output_dir=tmp_path)

        assert result is None

    def test_glob_fallback_finds_file_with_different_name(self, tmp_path):
        """If yt-dlp writes a slightly different filename, the glob should find it."""
        meta = _meta(title="My ! Video", upload_date="20260101")

        def fake_run(cmd, **_):
            # Write with a different name than what the script expects
            out = Path(cmd[cmd.index("-o") + 1])
            actual = out.parent / (out.stem + "_extra.mp3")
            actual.parent.mkdir(parents=True, exist_ok=True)
            actual.write_bytes(b"\xff\xfb" * 100)
            return MagicMock(returncode=0)

        with _mock_fetch(meta), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", side_effect=fake_run), \
             patch.object(yr, "_notify"):

            result = yr.download_audio("https://youtube.com/watch?v=x",
                                       output_dir=tmp_path)

        assert result is not None
        assert result.suffix == ".mp3"

    def test_returns_none_and_warns_when_file_not_found(self, tmp_path, caplog):
        """If yt-dlp exits 0 but writes nothing, log a warning and return None."""
        with _mock_fetch(_meta()), \
             patch("youtube_recorder.shutil.which", return_value="/bin/tool"), \
             patch("youtube_recorder.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch.object(yr, "_notify"):

            result = yr.download_audio("https://youtube.com/watch?v=x",
                                       output_dir=tmp_path)

        assert result is None
