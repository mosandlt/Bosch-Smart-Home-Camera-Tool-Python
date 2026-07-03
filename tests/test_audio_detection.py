"""
Tests for cmd_audio_detection — glass-break + smoke/fire-alarm sound detection
(Gen2 Audio-Plus cameras), cross-ported from HA v14.2.0 (2026-06-25).

PIN_EVERY_MODE: GET-only display, GET+PUT set (single flag / both flags),
read-modify-write body (both fields always sent), HTTP 442 (not supported),
HTTP 443 (privacy mode active) on both GET and PUT, non-200 errors,
Gen2 client-side gate, and --json output are all explicitly pinned.

API: GET/PUT /v11/video_inputs/{id}/audioDetectionConfig
     Body: {"detectGlassBreak": bool, "detectFireAlarm": bool}
Fake IDs only — NEVER real device values, IPs, tokens, or secrets.
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import cmd_audio_detection

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Testcam"


def _make_cfg(
    cam_id: str = CAM_ID, cam_name: str = CAM_NAME, model: str = "HOME_Eyes_Outdoor"
) -> dict[str, Any]:
    """Minimal config dict with one Gen2 camera and a dummy token."""
    return {
        "account": {"bearer_token": "tok", "refresh_token": "", "username": ""},
        "cameras": {
            cam_name: {
                "id": cam_id,
                "name": cam_name,
                "model": model,
                "firmware": "9.40.25",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    """Build a minimal Namespace with sensible defaults."""
    defaults: dict[str, Any] = {
        "cam": None,
        "glass_break": None,
        "fire_alarm": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# GET (show current state)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAudioDetectionGet:
    """Show glass-break/fire-alarm state without setting anything."""

    def test_shows_glass_break_and_fire_alarm(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": True, "detectFireAlarm": False},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "Glass-break" in out
        assert "Fire-alarm" in out
        assert "ON" in out
        assert "OFF" in out
        sess.put.assert_not_called()

    def test_http_442_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 442 → graceful 'not supported' message, no crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=442)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "442" in out or "not supported" in out.lower()

    def test_http_443_privacy_mode_on_get(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 443 on GET → privacy-mode-active message, no crash, no PUT attempted."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=443)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, glass_break="on"))
        out = capsys.readouterr().out
        assert "443" in out
        assert "privacy" in out.lower()
        sess.put.assert_not_called()

    def test_http_error_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "500" in out

    def test_json_output_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": True, "detectFireAlarm": True},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["cam"] == CAM_NAME
        assert data[0]["detectGlassBreak"] is True
        assert data[0]["detectFireAlarm"] is True


# ─────────────────────────────────────────────────────────────────────────────
# SET (read-modify-write)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAudioDetectionSet:
    """Setting glass-break / fire-alarm always sends BOTH fields (read-modify-write)."""

    def test_set_glass_break_only_preserves_fire_alarm(self) -> None:
        """--glass-break on with fire-alarm previously ON → PUT body keeps fire-alarm ON."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": False, "detectFireAlarm": True},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, glass_break="on"))
        body = sess.put.call_args[1]["json"]
        assert body["detectGlassBreak"] is True
        assert body["detectFireAlarm"] is True  # preserved from GET, not reset

    def test_set_fire_alarm_only_preserves_glass_break(self) -> None:
        """--fire-alarm off with glass-break previously ON → PUT body keeps glass-break ON."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": True, "detectFireAlarm": True},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, fire_alarm="off"))
        body = sess.put.call_args[1]["json"]
        assert body["detectGlassBreak"] is True  # preserved from GET, not reset
        assert body["detectFireAlarm"] is False

    def test_set_both_fields_together(self) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": False, "detectFireAlarm": False},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, glass_break="on", fire_alarm="on"))
        body = sess.put.call_args[1]["json"]
        assert body == {"detectGlassBreak": True, "detectFireAlarm": True}

    def test_put_body_always_has_both_keys(self) -> None:
        """Regression: PUT body must never omit either field, even when only one changes."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": True, "detectFireAlarm": False},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, fire_alarm="on"))
        body = sess.put.call_args[1]["json"]
        assert set(body.keys()) == {"detectGlassBreak", "detectFireAlarm"}

    def test_put_failure_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": False, "detectFireAlarm": False},
        )
        sess.put.return_value = MagicMock(status_code=500, text="server error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, glass_break="on"))
        out = capsys.readouterr().out
        assert "500" in out

    def test_put_http_443_privacy_mode_active(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT rejected with HTTP 443 while privacy mode is ON."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": False, "detectFireAlarm": False},
        )
        sess.put.return_value = MagicMock(status_code=443)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, glass_break="on"))
        out = capsys.readouterr().out
        assert "443" in out
        assert "privacy" in out.lower()

    def test_json_set_output_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": False, "detectFireAlarm": False},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, glass_break="on", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["detectGlassBreak"] is True
        assert data[0]["detectFireAlarm"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Gen2 gate — client-side, no API call for Gen1 cameras
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAudioDetectionGen2Gate:
    """Only Gen2 (HOME_Eyes_* / *_GEN2 hardwareVersion) cameras call the API."""

    @pytest.mark.parametrize("model", ["INDOOR", "OUTDOOR", "CAMERA_360", "CAMERA_EYES"])
    def test_gen1_model_skipped_no_api_call(
        self, model: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = _make_cfg(model=model)
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "Gen2" in out
        sess.get.assert_not_called()
        sess.put.assert_not_called()

    @pytest.mark.parametrize(
        "model", ["HOME_Eyes_Outdoor", "HOME_Eyes_Indoor", "CAMERA_OUTDOOR_GEN2"]
    )
    def test_gen2_model_calls_api(self, model: str) -> None:
        cfg = _make_cfg(model=model)
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"detectGlassBreak": False, "detectFireAlarm": False},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME))
        sess.get.assert_called_once()

    def test_json_gen1_gate_error_entry(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg(model="OUTDOOR")
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["error"] == "not_gen2"


# ─────────────────────────────────────────────────────────────────────────────
# Token expiry
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAudioDetectionTokenExpired:
    def test_401_prints_token_expired_and_returns(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio_detection(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "expired" in out.lower()
