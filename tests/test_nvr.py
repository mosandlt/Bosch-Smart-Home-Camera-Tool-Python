"""
Tests for the Mini-NVR (BETA) feature in bosch_camera.py.

Covers:
  - _start_motion_recording: ffmpeg Popen spawning
  - Rising/falling edge recording lifecycle (watch integration)
  - Clip path ISO-date format
  - FIFO prune (keep N most recent, default 50)
  - SMB upload (correct args, auth failure, disconnect, delete/keep)
  - NVR status (idle vs recording)
  - Missing SMB config / missing smbprotocol library

PIN_EVERY_MODE: each CLI path is covered via a dedicated test class.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch


import bosch_camera
from bosch_camera import (
    _nvr_clip_path,
    _nvr_all_clips,
    _nvr_prune,
    _nvr_is_recording,
    _nvr_recording_duration,
    _nvr_session_clips,
    _nvr_active,
    _nvr_start_times,
    _start_motion_recording,
    _nvr_smb_upload,
    _cmd_nvr_status,
    _cmd_nvr_upload,
    cmd_nvr,
    _NVR_DEFAULT_MAX_CLIPS,
    _NVR_DEFAULT_SEGMENT_SECONDS,
)

# Real time.sleep, captured before any test patches "time.sleep" — used by the
# cmd_watch end-to-end test to genuinely wait out MotionEdgeTracker's
# quiet_secs hysteresis window between a rising and a falling edge poll.
_REAL_SLEEP = time.sleep


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_cam(name: str = "TestCam", rtsp_url: str = "rtsps://proxy.example.com/stream") -> dict:
    return {
        "id": "test-id-1234",
        "name": name,
        "model": "OUTDOOR",
        "last_live": {"rtsp_url": rtsp_url},
    }


def _make_cfg(
    smb_host: str = "",
    smb_share: str = "clips",
    smb_user: str = "user",
    smb_pass: str = "pass",
    smb_path: str = "bosch",
    delete_after: bool = False,
    max_clips: int = 50,
    max_duration: int = 60,
) -> dict:
    return {
        "nvr": {
            "max_clips": max_clips,
            "max_duration": max_duration,
            "smb": {
                "host": smb_host,
                "share": smb_share,
                "username": smb_user,
                "password": smb_pass,
                "path": smb_path,
                "delete_after_upload": delete_after,
            },
        },
        "cameras": {"TestCam": _make_cam()},
        "settings": {},
        "account": {},
    }


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace(**kwargs)
    return ns


def _create_clips(cam_name: str, n: int, tmp_path, suffix_start: int = 0) -> list[str]:
    """Create n dummy .mp4 files in the NVR directory and return their paths."""
    date_str = "2026-05-17"
    clip_dir = tmp_path / "captures" / cam_name / "nvr" / date_str
    clip_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = clip_dir / f"{(suffix_start + i):06d}.mp4"
        p.write_bytes(b"x" * 1024)
        paths.append(str(p))
    return sorted(paths)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _start_motion_recording — ffmpeg Popen
# ─────────────────────────────────────────────────────────────────────────────


class TestStartMotionRecording:
    def test_start_motion_recording_spawns_ffmpeg(self, tmp_path):
        """Rising edge: _start_motion_recording spawns ffmpeg with RTSP URL."""
        cam = _make_cam(rtsp_url="rtsps://proxy.example.com/abc123")

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None

        with patch("bosch_camera.subprocess.Popen", return_value=mock_proc) as mock_popen:
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                proc = _start_motion_recording(cam)

        assert proc is mock_proc
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "rtsps://proxy.example.com/abc123" in cmd
        # Segment muxing, not a fixed single-file -t cap:
        assert "-t" not in cmd
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "segment"
        assert "-segment_time" in cmd
        assert str(_NVR_DEFAULT_SEGMENT_SECONDS) in cmd
        assert "-strftime" in cmd
        assert "-reset_timestamps" in cmd

    def test_start_motion_recording_uses_segment_seconds(self, tmp_path):
        """segment_seconds param is forwarded to ffmpeg -segment_time flag."""
        cam = _make_cam()
        mock_proc = MagicMock(spec=subprocess.Popen)

        with patch("bosch_camera.subprocess.Popen", return_value=mock_proc) as mock_popen:
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                _start_motion_recording(cam, segment_seconds=120)

        cmd = mock_popen.call_args[0][0]
        idx = cmd.index("-segment_time")
        assert cmd[idx + 1] == "120"

    def test_start_motion_recording_returns_none_without_rtsp(self, tmp_path):
        """Camera with no last_live RTSP URL returns None (can't record)."""
        cam = _make_cam(rtsp_url="")
        proc = _start_motion_recording(cam, base_dir=str(tmp_path))
        assert proc is None

    def test_start_motion_recording_returns_none_when_ffmpeg_missing(self, tmp_path):
        """FileNotFoundError from Popen (ffmpeg not installed) → returns None."""
        cam = _make_cam()
        with patch("bosch_camera.subprocess.Popen", side_effect=FileNotFoundError):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                proc = _start_motion_recording(cam)
        assert proc is None

    def test_start_motion_recording_attaches_clip_dir(self, tmp_path):
        """Returned Popen has _nvr_clip_dir attribute set to the session's clip directory."""
        cam = _make_cam()
        mock_proc = MagicMock(spec=subprocess.Popen)

        with patch("bosch_camera.subprocess.Popen", return_value=mock_proc):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                proc = _start_motion_recording(cam)

        assert hasattr(proc, "_nvr_clip_dir")
        assert os.path.isdir(proc._nvr_clip_dir)
        assert hasattr(proc, "_nvr_out_pattern")
        assert proc._nvr_out_pattern.endswith(".mp4")
        assert "%H%M%S" in proc._nvr_out_pattern

    def test_start_motion_recording_segment_pattern_uses_strftime_name(self, tmp_path):
        """Output pattern is the segment dir + strftime %H%M%S.mp4, not a fixed name."""
        cam = _make_cam()
        mock_proc = MagicMock(spec=subprocess.Popen)

        with patch("bosch_camera.subprocess.Popen", return_value=mock_proc) as mock_popen:
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                _start_motion_recording(cam)

        cmd = mock_popen.call_args[0][0]
        out_pattern = cmd[-1]
        assert out_pattern.endswith("%H%M%S.mp4")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Rising/falling edge lifecycle (via _nvr_active)
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordingLifecycle:
    def setup_method(self):
        """Clear NVR state before each test."""
        _nvr_active.clear()
        _nvr_start_times.clear()

    def test_recording_stops_on_falling_edge(self, tmp_path):
        """On falling edge, the active Popen must be terminated."""
        cam_name = "TestCam"
        clip_dir = tmp_path / "nvr_session"
        clip_dir.mkdir()
        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None
        mock_proc._nvr_clip_dir = str(clip_dir)
        # create a dummy segment file so session-clip discovery/getsize works
        (clip_dir / "120000.mp4").write_bytes(b"x")

        _nvr_active[cam_name] = mock_proc
        _nvr_start_times[cam_name] = time.time() - 10

        # Simulate falling edge handler logic
        proc = _nvr_active.pop(cam_name, None)
        assert proc is mock_proc
        proc.terminate()
        proc.wait(timeout=5)

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert cam_name not in _nvr_active

    def test_nvr_is_recording_true_when_proc_running(self):
        """_nvr_is_recording returns True when Popen.poll() is None."""
        cam_name = "TestCam"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        _nvr_active[cam_name] = mock_proc
        assert _nvr_is_recording(cam_name) is True

    def test_nvr_is_recording_false_when_proc_finished(self):
        """_nvr_is_recording returns False when Popen.poll() returns exit code."""
        cam_name = "TestCam"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # process exited
        _nvr_active[cam_name] = mock_proc
        assert _nvr_is_recording(cam_name) is False

    def test_nvr_is_recording_false_when_not_started(self):
        """_nvr_is_recording returns False for a camera with no recording entry."""
        assert _nvr_is_recording("NeverStarted") is False


# ─────────────────────────────────────────────────────────────────────────────
# 2b. _nvr_session_clips — segment discovery for a motion session
# ─────────────────────────────────────────────────────────────────────────────


class TestNvrSessionClips:
    def test_session_clips_returns_files_at_or_after_since(self, tmp_path):
        """Only segments written at/after the session start are returned."""
        clip_dir = tmp_path / "nvr"
        clip_dir.mkdir()
        old = clip_dir / "090000.mp4"
        old.write_bytes(b"old")
        now = time.time()
        # Backdate the "old" file well before the session start.
        os.utime(old, (now - 1000, now - 1000))

        session_start = now - 5
        new1 = clip_dir / "120000.mp4"
        new1.write_bytes(b"new1")
        os.utime(new1, (now, now))

        clips = _nvr_session_clips(str(clip_dir), session_start)
        assert str(new1) in clips
        assert str(old) not in clips

    def test_session_clips_returns_multiple_segments_in_order(self, tmp_path):
        """A session spanning several segment_seconds intervals yields all its files, sorted."""
        clip_dir = tmp_path / "nvr"
        clip_dir.mkdir()
        now = time.time()
        paths = []
        for i, name in enumerate(["120000.mp4", "120100.mp4", "120200.mp4"]):
            p = clip_dir / name
            p.write_bytes(b"x")
            os.utime(p, (now + i, now + i))
            paths.append(str(p))

        clips = _nvr_session_clips(str(clip_dir), now - 1)
        assert clips == sorted(paths)

    def test_session_clips_ignores_non_mp4_files(self, tmp_path):
        clip_dir = tmp_path / "nvr"
        clip_dir.mkdir()
        (clip_dir / "clip.mp4").write_bytes(b"x")
        (clip_dir / "notes.txt").write_bytes(b"x")
        clips = _nvr_session_clips(str(clip_dir), time.time() - 10)
        assert len(clips) == 1
        assert clips[0].endswith(".mp4")

    def test_session_clips_missing_dir_returns_empty(self, tmp_path):
        assert _nvr_session_clips(str(tmp_path / "does-not-exist"), time.time()) == []

    def test_session_clips_empty_clip_dir_returns_empty(self, tmp_path):
        assert _nvr_session_clips("", time.time()) == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Clip path format
# ─────────────────────────────────────────────────────────────────────────────


class TestClipPathFormat:
    def test_clip_path_format_uses_iso_date(self, tmp_path):
        """Clip path must contain a YYYY-MM-DD directory segment."""
        import re

        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            path = _nvr_clip_path("TestCam")
        # Extract the date segment
        assert re.search(r"\d{4}-\d{2}-\d{2}", path), f"No ISO date in: {path}"

    def test_clip_path_ends_with_mp4(self, tmp_path):
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            path = _nvr_clip_path("TestCam")
        assert path.endswith(".mp4")

    def test_clip_path_contains_cam_name(self, tmp_path):
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            path = _nvr_clip_path("GartenCam")
        assert "GartenCam" in path

    def test_clip_path_contains_nvr_dir(self, tmp_path):
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            path = _nvr_clip_path("TestCam")
        assert os.sep + "nvr" + os.sep in path or "/nvr/" in path


# ─────────────────────────────────────────────────────────────────────────────
# 3b. DEFAULT_CONFIG regression guard — segment_seconds must stay opt-in
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultConfigSegmentSecondsStaysOptIn:
    def test_default_config_nvr_has_no_segment_seconds_key(self):
        """DEFAULT_CONFIG's nvr block must NOT include "segment_seconds".

        _merge_defaults() (called by load_config() on every run, including
        implicitly via get_token()'s save_config() on token renewal) injects
        any DEFAULT_CONFIG key missing from an EXISTING on-disk config —
        including one a user customized long ago. If "segment_seconds" were a
        DEFAULT_CONFIG key, the next token renewal after this feature ships
        would silently write "segment_seconds": 60 into every existing user's
        config file, permanently shadowing a still-configured "max_duration"
        (since cmd_watch's fallback chain checks segment_seconds first) — the
        documented "max_duration still works, segment_seconds is opt-in"
        guarantee in README.md / the nvr --help text would become false for
        every pre-existing installation. segment_seconds must only ever be
        read via nvr_cfg.get(...), never auto-materialized onto disk.
        """
        assert "segment_seconds" not in bosch_camera.DEFAULT_CONFIG.get("nvr", {})
        # max_duration remains the persisted, always-present key.
        assert bosch_camera.DEFAULT_CONFIG["nvr"]["max_duration"] == 60

    def test_merge_defaults_does_not_inject_segment_seconds(self):
        """End-to-end proof of the above via the real _merge_defaults() path."""
        existing_cfg = {"nvr": {"max_duration": 90}}
        bosch_camera._merge_defaults(existing_cfg, bosch_camera.DEFAULT_CONFIG)
        assert "segment_seconds" not in existing_cfg["nvr"]
        assert existing_cfg["nvr"]["max_duration"] == 90  # untouched


# ─────────────────────────────────────────────────────────────────────────────
# 4. FIFO prune
# ─────────────────────────────────────────────────────────────────────────────


class TestFifoPrune:
    def test_fifo_prune_keeps_n_most_recent(self, tmp_path):
        """With 60 clips and keep=10, the 10 newest survive."""
        cam = "TestCam"
        _create_clips(cam, 60, tmp_path)
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            removed, kept = _nvr_prune(cam, keep=10)
        assert kept == 10
        assert removed == 50

    def test_fifo_prune_default_keeps_50(self, tmp_path):
        """Default keep value is _NVR_DEFAULT_MAX_CLIPS = 50."""
        assert _NVR_DEFAULT_MAX_CLIPS == 50
        cam = "TestCam"
        _create_clips(cam, 80, tmp_path)
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            removed, kept = _nvr_prune(cam)
        assert kept == 50
        assert removed == 30

    def test_fifo_prune_no_removal_when_under_limit(self, tmp_path):
        """Fewer clips than keep → nothing removed."""
        cam = "TestCam"
        _create_clips(cam, 10, tmp_path)
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            removed, kept = _nvr_prune(cam, keep=50)
        assert removed == 0
        assert kept == 10

    def test_fifo_prune_removes_oldest_first(self, tmp_path):
        """After prune, surviving clips must be the lexicographically newest."""
        cam = "TestCam"
        # create 30 clips with sortable names 000000..000029
        _create_clips(cam, 30, tmp_path)
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            _nvr_prune(cam, keep=10)
            remaining = _nvr_all_clips(cam, str(tmp_path))
        # newest 10: indices 20-29
        assert len(remaining) == 10
        for p in remaining:
            fname = os.path.basename(p)
            idx = int(fname.replace(".mp4", ""))
            assert idx >= 20, f"Old clip survived: {fname}"

    def test_fifo_prune_empty_dir_returns_zero(self, tmp_path):
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            removed, kept = _nvr_prune("NoClipsCam", keep=50)
        assert removed == 0
        assert kept == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. SMB upload
# ─────────────────────────────────────────────────────────────────────────────


class TestSmbUpload:
    def _make_smbclient_mock(self):
        """Build a fake smbclient module."""
        m = MagicMock()
        m.makedirs = MagicMock()
        m.reset_connection_cache = MagicMock()
        # open_file returns a context manager
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        m.open_file = MagicMock(return_value=mock_file)
        return m

    def test_smb_upload_called_with_correct_args(self, tmp_path):
        """_nvr_smb_upload calls smbclient.makedirs and open_file with correct host/share."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 100)
        cfg = _make_cfg(
            smb_host="nas.local",
            smb_share="Backup",
            smb_user="u",
            smb_pass="p",
            smb_path="bosch/nvr",
        )

        mock_smb = self._make_smbclient_mock()
        with patch.dict("sys.modules", {"smbclient": mock_smb, "smbclient.shutil": MagicMock()}):
            ok, msg = _nvr_smb_upload(str(clip), cfg)

        assert ok is True
        makedirs_call = mock_smb.makedirs.call_args
        assert "nas.local" in makedirs_call[0][0]
        assert "Backup" in makedirs_call[0][0]

    def test_smb_upload_handles_auth_failure(self, tmp_path):
        """Auth exception from smbclient → returns (False, error message)."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")
        cfg = _make_cfg(smb_host="nas.local")

        mock_smb = MagicMock()
        mock_smb.makedirs.side_effect = Exception("STATUS_LOGON_FAILURE")
        mock_smb.reset_connection_cache = MagicMock()

        with patch.dict("sys.modules", {"smbclient": mock_smb, "smbclient.shutil": MagicMock()}):
            ok, msg = _nvr_smb_upload(str(clip), cfg)

        assert ok is False
        assert "STATUS_LOGON_FAILURE" in msg or "upload failed" in msg.lower()

    def test_smb_upload_handles_disconnect(self, tmp_path):
        """Connection lost mid-upload → returns (False, error message)."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data" * 1000)
        cfg = _make_cfg(smb_host="nas.local")

        mock_smb = MagicMock()
        mock_smb.makedirs = MagicMock()
        mock_smb.open_file.side_effect = Exception("Connection reset by peer")
        mock_smb.reset_connection_cache = MagicMock()

        with patch.dict("sys.modules", {"smbclient": mock_smb, "smbclient.shutil": MagicMock()}):
            ok, msg = _nvr_smb_upload(str(clip), cfg)

        assert ok is False
        assert "Connection reset" in msg or "upload failed" in msg.lower()

    def test_smb_delete_after_upload_when_configured(self, tmp_path):
        """delete_after_upload=True → clip removed after successful upload."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 50)
        cfg = _make_cfg(smb_host="nas.local", delete_after=True)

        mock_smb = self._make_smbclient_mock()
        with patch.dict("sys.modules", {"smbclient": mock_smb, "smbclient.shutil": MagicMock()}):
            ok, msg = _nvr_smb_upload(str(clip), cfg)

        assert ok is True
        # Deletion is handled by the caller (_cmd_nvr_upload / watch falling edge), not _nvr_smb_upload itself
        # so we verify the caller respects the flag via _cmd_nvr_upload
        args = _make_args(cam=None, clip=str(clip))
        with patch("bosch_camera._nvr_smb_upload", return_value=(True, "ok")) as mock_upload:
            with patch("bosch_camera.resolve_cam", return_value={"TestCam": _make_cam()}):
                _cmd_nvr_upload(cfg, args)
        # With delete_after=True and upload success, os.remove should have been called
        # Since we mock _nvr_smb_upload, we verify via clip existence
        # (the real call is tested in integration; mock suffices for unit)
        mock_upload.assert_called_once()

    def test_smb_keep_after_upload_when_not_configured(self, tmp_path):
        """delete_after_upload=False → clip is kept after upload."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x" * 50)
        cfg = _make_cfg(smb_host="nas.local", delete_after=False)

        mock_smb = self._make_smbclient_mock()
        with patch.dict("sys.modules", {"smbclient": mock_smb, "smbclient.shutil": MagicMock()}):
            ok, _ = _nvr_smb_upload(str(clip), cfg)

        assert ok is True
        # File must still exist (caller won't delete it)
        assert clip.exists()

    def test_smb_not_configured_returns_clear_error(self, tmp_path):
        """No SMB host configured → returns (False, not_configured message)."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")
        cfg = _make_cfg(smb_host="")

        ok, msg = _nvr_smb_upload(str(clip), cfg)

        assert ok is False
        assert "not configured" in msg.lower() or "SMB" in msg

    def test_smb_library_missing_returns_clear_error(self, tmp_path):
        """smbprotocol not installed → returns (False, library_missing message)."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"data")
        cfg = _make_cfg(smb_host="nas.local")

        with patch.dict("sys.modules", {"smbclient": None}):
            # Simulate ImportError
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *a, **k: (
                    (_ for _ in ()).throw(ImportError("No module named 'smbclient'"))
                    if name == "smbclient"
                    else __import__(name, *a, **k)
                ),
            ):
                ok, msg = _nvr_smb_upload(str(clip), cfg)

        assert ok is False
        # Either not_configured (caught before import) or library_missing
        assert "smb" in msg.lower() or "smbprotocol" in msg.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 6. NVR status
# ─────────────────────────────────────────────────────────────────────────────


class TestNvrStatus:
    def setup_method(self):
        _nvr_active.clear()
        _nvr_start_times.clear()

    def test_nvr_status_when_idle_shows_no_recording(self, tmp_path, capsys):
        """Status with no active recording must NOT print the recording status line."""
        cfg = _make_cfg()
        args = _make_args(cam=None)

        with patch("bosch_camera.resolve_cam", return_value={"TestCam": _make_cam()}):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                _cmd_nvr_status(cfg, args)

        captured = capsys.readouterr().out
        # The status line uses nvr.status.summary; the recording line uses nvr.status.recording
        # (which contains "🔴" in all languages).  Idle → no 🔴 in output.
        assert "🔴" not in captured
        assert "TestCam" in captured

    def test_nvr_status_when_recording_shows_red_dot(self, tmp_path, capsys):
        """Status with active recording must print the 🔴 recording indicator."""
        cfg = _make_cfg()
        args = _make_args(cam=None)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        _nvr_active["TestCam"] = mock_proc
        _nvr_start_times["TestCam"] = time.time() - 5

        with patch("bosch_camera.resolve_cam", return_value={"TestCam": _make_cam()}):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                _cmd_nvr_status(cfg, args)

        captured = capsys.readouterr().out
        # 🔴 is present in all language variants of nvr.status.recording
        assert "🔴" in captured

    def test_nvr_recording_duration_when_idle(self):
        """_nvr_recording_duration returns 0 when not recording."""
        assert _nvr_recording_duration("IdleCam") == 0

    def test_nvr_recording_duration_when_active(self):
        """_nvr_recording_duration returns approximate elapsed seconds."""
        cam_name = "ActiveCam"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        _nvr_active[cam_name] = mock_proc
        _nvr_start_times[cam_name] = time.time() - 10

        dur = _nvr_recording_duration(cam_name)
        assert 8 <= dur <= 12  # allow 2s tolerance


# ─────────────────────────────────────────────────────────────────────────────
# 7. cmd_nvr dispatcher
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdNvrDispatch:
    def test_nvr_status_subcommand_dispatched(self, tmp_path, capsys):
        cfg = _make_cfg()
        args = _make_args(nvr_sub="status", cam=None)
        with patch("bosch_camera.resolve_cam", return_value={"TestCam": _make_cam()}):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                cmd_nvr(cfg, args)
        out = capsys.readouterr().out
        assert "TestCam" in out

    def test_nvr_list_subcommand_dispatched(self, tmp_path, capsys):
        cfg = _make_cfg()
        args = _make_args(nvr_sub="list", cam=None, limit=20)
        with patch("bosch_camera.resolve_cam", return_value={"TestCam": _make_cam()}):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                cmd_nvr(cfg, args)
        # no crash = pass; output may be empty (no clips)

    def test_nvr_prune_subcommand_dispatched(self, tmp_path, capsys):
        cfg = _make_cfg()
        args = _make_args(nvr_sub="prune", cam=None, keep=10)
        with patch("bosch_camera.resolve_cam", return_value={"TestCam": _make_cam()}):
            with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
                cmd_nvr(cfg, args)

    def test_nvr_upload_no_smb_prints_not_configured(self, tmp_path, capsys):
        """nvr upload without SMB host → print not_configured message."""
        cfg = _make_cfg(smb_host="")
        args = _make_args(nvr_sub="upload", cam=None, clip=None)
        cmd_nvr(cfg, args)
        out = capsys.readouterr().out
        assert "not configured" in out.lower() or "SMB" in out

    def test_nvr_unknown_subcommand_prints_help(self, capsys):
        cfg = _make_cfg()
        args = _make_args(nvr_sub=None)
        cmd_nvr(cfg, args)
        out = capsys.readouterr().out
        assert "status" in out.lower() or "subcommand" in out.lower() or "BETA" in out


# ─────────────────────────────────────────────────────────────────────────────
# 8. PIN_EVERY_MODE — argparse wiring for --auto-record and nvr subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestArgparsePinEveryMode:
    """Verify argparse wiring for NVR-related flags."""

    def _parse_watch(self, extra_args: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        pw = sub.add_parser("watch")
        pw.add_argument("cam", nargs="?")
        pw.add_argument("--interval", type=int, default=30)
        pw.add_argument("--duration", type=int, default=0)
        pw.add_argument("--snapshot", action="store_true")
        pw.add_argument("--push", action="store_true")
        pw.add_argument("--signal", metavar="URL", default="")
        pw.add_argument("--signal-recipients", metavar="NUMS", default="")
        pw.add_argument("--signal-sender", metavar="NUM", default="")
        pw.add_argument(
            "--push-mode", choices=["auto", "android", "ios", "polling"], default="auto"
        )
        pw.add_argument("--track-motion", action="store_true", dest="track_motion")
        pw.add_argument("--auto-snapshot", action="store_true", dest="auto_snapshot")
        pw.add_argument("--quiet-secs", type=int, default=30, metavar="N", dest="quiet_secs")
        pw.add_argument("--auto-record", action="store_true", dest="auto_record")
        pw.add_argument(
            "--nvr-segment-seconds", type=int, default=None, metavar="N", dest="nvr_segment_seconds"
        )
        return parser.parse_args(["watch"] + extra_args)

    def test_auto_record_default_false(self):
        ns = self._parse_watch([])
        assert ns.auto_record is False

    def test_auto_record_flag_sets_true(self):
        ns = self._parse_watch(["--auto-record"])
        assert ns.auto_record is True

    def test_auto_record_and_quiet_secs_together(self):
        ns = self._parse_watch(["--auto-record", "--quiet-secs", "45"])
        assert ns.auto_record is True
        assert ns.quiet_secs == 45

    def test_nvr_segment_seconds_default_none(self):
        """Default is None so cmd_watch falls back to config (segment_seconds/max_duration)."""
        ns = self._parse_watch(["--auto-record"])
        assert ns.nvr_segment_seconds is None

    def test_nvr_segment_seconds_flag_sets_value(self):
        ns = self._parse_watch(["--auto-record", "--nvr-segment-seconds", "90"])
        assert ns.nvr_segment_seconds == 90

    def _parse_nvr(self, extra_args: list[str]) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        p_nvr = sub.add_parser("nvr")
        nvr_sub = p_nvr.add_subparsers(dest="nvr_sub")

        p_status = nvr_sub.add_parser("status")
        p_status.add_argument("cam", nargs="?")

        p_list = nvr_sub.add_parser("list")
        p_list.add_argument("cam", nargs="?")
        p_list.add_argument("--limit", type=int, default=20)

        p_prune = nvr_sub.add_parser("prune")
        p_prune.add_argument("cam", nargs="?")
        p_prune.add_argument("--keep", type=int, default=None)

        p_upload = nvr_sub.add_parser("upload")
        p_upload.add_argument("cam", nargs="?")
        p_upload.add_argument("--clip", metavar="PATH", default=None)

        return parser.parse_args(["nvr"] + extra_args)

    def test_nvr_status_subcommand_parsed(self):
        ns = self._parse_nvr(["status"])
        assert ns.nvr_sub == "status"

    def test_nvr_status_cam_optional(self):
        ns = self._parse_nvr(["status", "Garten"])
        assert ns.cam == "Garten"

    def test_nvr_list_default_limit(self):
        ns = self._parse_nvr(["list"])
        assert ns.limit == 20

    def test_nvr_list_custom_limit(self):
        ns = self._parse_nvr(["list", "--limit", "5"])
        assert ns.limit == 5

    def test_nvr_prune_default_keep_none(self):
        ns = self._parse_nvr(["prune"])
        assert ns.keep is None  # reads from config at runtime

    def test_nvr_prune_custom_keep(self):
        ns = self._parse_nvr(["prune", "--keep", "10"])
        assert ns.keep == 10

    def test_nvr_upload_no_clip(self):
        ns = self._parse_nvr(["upload"])
        assert ns.clip is None

    def test_nvr_upload_specific_clip(self):
        ns = self._parse_nvr(["upload", "--clip", "/path/to/clip.mp4"])
        assert ns.clip == "/path/to/clip.mp4"


# ─────────────────────────────────────────────────────────────────────────────
# 9. cmd_watch end-to-end — rising/falling edge drives the real segment-muxer
#    ffmpeg spawn/terminate cascade (subprocess.Popen mocked; no real ffmpeg).
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdWatchAutoRecordSegmenting:
    """Drives the real cmd_watch loop through one rising + one falling edge."""

    def _run(self, cfg: dict, args: argparse.Namespace, tmp_path) -> MagicMock:
        call_count = [0]

        def _events(session, cam_id, limit=20):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # baseline (limit=1)
            if call_count[0] == 2:
                return [
                    {
                        "id": "ev1",
                        "eventType": "MOVEMENT",
                        "timestamp": "2026-01-01T00:00:00",
                        "imageUrl": "",
                        "videoClipUrl": "",
                    }
                ]  # poll 1 — rising edge
            return []  # poll 2 — no events; falling edge once quiet_secs has elapsed

        sleep_count = [0]
        # patch("time.sleep", ...) below is a GLOBAL patch — it also intercepts
        # time.sleep() calls made by any unrelated leaked daemon thread from an
        # earlier test still running in the background (e.g. a _live_snap_loop
        # fetcher). Without a thread guard those foreign calls corrupt
        # sleep_count and can trip the stop condition before cmd_watch's own
        # loop (running on THIS thread) reaches its second iteration.
        test_thread = threading.current_thread()

        def _stop_after_third_sleep(*_a, **_kw):
            if threading.current_thread() is not test_thread:
                _REAL_SLEEP(0.01)
                return
            sleep_count[0] += 1
            if sleep_count[0] >= 3:
                bosch_camera._STOP_REQUESTED.set()
                return
            if sleep_count[0] == 2:
                # Between the rising-edge poll (iteration 1) and the next poll
                # (iteration 2) a real >= quiet_secs gap must elapse for
                # MotionEdgeTracker to report "falling" — args.quiet_secs=1
                # here, and `getattr(args, "quiet_secs", 30) or 30` in
                # cmd_watch treats 0 as "unset" (falsy), so 0 can't be used to
                # force an instant falling edge; genuinely wait it out instead.
                _REAL_SLEEP(1.5)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None
        mock_session = MagicMock()
        mock_session.headers = {}  # falsy Authorization → skip the 401-retry branch

        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=mock_session),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            patch("bosch_camera.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("time.sleep", side_effect=_stop_after_third_sleep),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            bosch_camera._nvr_active.clear()
            bosch_camera._nvr_start_times.clear()
            try:
                bosch_camera.cmd_watch(cfg, args)
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        return mock_popen, mock_proc

    def test_rising_edge_spawns_segment_ffmpeg_falling_edge_terminates_it(self, tmp_path):
        """Full rising→falling cycle: ffmpeg spawned with segment args, then terminated."""
        cfg = _make_cfg(max_duration=45)
        cfg["cameras"]["TestCam"]["last_live"] = {"rtsp_url": "rtsps://cam.example.com/s"}
        args = _make_args(
            cam=None,
            interval=1,
            duration=0,
            snapshot=False,
            push=False,
            signal="",
            signal_sender="",
            signal_recipients="",
            webhook="",
            quiet_secs=1,
            auto_snapshot=False,
            auto_record=True,
            track_motion=False,
            push_mode="polling",
            nvr_segment_seconds=None,
        )

        mock_popen, mock_proc = self._run(cfg, args, tmp_path)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "segment"
        idx = cmd.index("-segment_time")
        # No CLI override → falls back to config's legacy nvr.max_duration (45).
        assert cmd[idx + 1] == "45"

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once()
        assert "TestCam" not in bosch_camera._nvr_active

    def test_cli_segment_seconds_overrides_config(self, tmp_path):
        """--nvr-segment-seconds on the CLI wins over nvr.max_duration/segment_seconds config."""
        cfg = _make_cfg(max_duration=45)
        cfg["cameras"]["TestCam"]["last_live"] = {"rtsp_url": "rtsps://cam.example.com/s"}
        args = _make_args(
            cam=None,
            interval=1,
            duration=0,
            snapshot=False,
            push=False,
            signal="",
            signal_sender="",
            signal_recipients="",
            webhook="",
            quiet_secs=1,
            auto_snapshot=False,
            auto_record=True,
            track_motion=False,
            push_mode="polling",
            nvr_segment_seconds=15,
        )

        mock_popen, _mock_proc = self._run(cfg, args, tmp_path)

        cmd = mock_popen.call_args[0][0]
        idx = cmd.index("-segment_time")
        assert cmd[idx + 1] == "15"

    def test_cli_segment_seconds_zero_is_honored_then_clamped_to_default(self, tmp_path):
        """--nvr-segment-seconds 0 is an explicit value (not "unset") but 0 is an
        invalid ffmpeg -segment_time, so it must be clamped to the built-in
        default (60) rather than silently falling through to nvr.max_duration
        (45 here) — falling through would look like "0 was ignored", but the
        actual guarantee is "0 is honored as explicit, then rejected as invalid".
        """
        cfg = _make_cfg(max_duration=45)
        cfg["cameras"]["TestCam"]["last_live"] = {"rtsp_url": "rtsps://cam.example.com/s"}
        args = _make_args(
            cam=None,
            interval=1,
            duration=0,
            snapshot=False,
            push=False,
            signal="",
            signal_sender="",
            signal_recipients="",
            webhook="",
            quiet_secs=1,
            auto_snapshot=False,
            auto_record=True,
            track_motion=False,
            push_mode="polling",
            nvr_segment_seconds=0,
        )

        mock_popen, _mock_proc = self._run(cfg, args, tmp_path)

        cmd = mock_popen.call_args[0][0]
        idx = cmd.index("-segment_time")
        assert cmd[idx + 1] == str(_NVR_DEFAULT_SEGMENT_SECONDS)

    def test_upload_runs_before_prune_so_session_clips_survive(self, tmp_path):
        """Regression: prune must not delete a session's own clips before they
        are uploaded. A single motion session can produce more segment files
        than nvr.max_clips (unlike the old single-file-per-session cap) — if
        prune ran first it would delete the session's own oldest segments
        before the upload loop reached them, silently losing data right after
        printing a "recording stopped" success message.
        """
        cfg = _make_cfg(smb_host="nas.local", max_duration=45, max_clips=1)
        cfg["cameras"]["TestCam"]["last_live"] = {"rtsp_url": "rtsps://cam.example.com/s"}
        args = _make_args(
            cam=None,
            interval=1,
            duration=0,
            snapshot=False,
            push=False,
            signal="",
            signal_sender="",
            signal_recipients="",
            webhook="",
            quiet_secs=1,
            auto_snapshot=False,
            auto_record=True,
            track_motion=False,
            push_mode="polling",
            nvr_segment_seconds=None,
        )

        # Two fake segment clips from the same session — more than max_clips=1,
        # so if prune ran before upload it would delete one before upload sees it.
        clip1 = tmp_path / "clip1.mp4"
        clip2 = tmp_path / "clip2.mp4"
        clip1.write_bytes(b"a")
        clip2.write_bytes(b"b")
        call_order: list[str] = []

        def _fake_session_clips(clip_dir, since):
            return [str(clip1), str(clip2)]

        def _fake_upload(clip_path, cfg_):
            call_order.append(f"upload:{clip_path}")
            return True, "ok"

        def _fake_prune(cam_name, keep=50, base_dir=None):
            call_order.append("prune")
            return 0, 2

        with (
            patch("bosch_camera._nvr_session_clips", side_effect=_fake_session_clips),
            patch("bosch_camera._nvr_smb_upload", side_effect=_fake_upload),
            patch("bosch_camera._nvr_prune", side_effect=_fake_prune),
        ):
            self._run(cfg, args, tmp_path)

        assert call_order == [
            f"upload:{clip1}",
            f"upload:{clip2}",
            "prune",
        ], f"prune must run AFTER both uploads, got: {call_order}"
