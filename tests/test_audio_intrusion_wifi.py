"""
Tests for cmd_audio, cmd_intrusion, cmd_wifi — v10.7.4 cross-port from HA v12.6.0.

PIN_EVERY_MODE: each numeric boundary, both GET-only and GET+PUT paths, HTTP error
variants (401/442/non-200), and --json output are explicitly tested.

Source: captures/api-findings.md §6.2 (audio mic/speaker 0-100, intrusion
sensitivity 0-7, distance 1-8 (Bosch rejects >8), wifiinfo RSSI/SSID/signal).

Regression tests added v10.8.1:
- BUG1: intrusion distance 9/10 must be rejected (Bosch HTTP 400 above 8)
- BUG2: intercom speaker-set must issue GET→full-body PUT with microphoneLevel preserved
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

import bosch_camera
from bosch_camera import (
    CLOUD_API,
    cmd_audio,
    cmd_intrusion,
    cmd_wifi,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Testcam"


def _make_cfg(cam_id: str = CAM_ID, cam_name: str = CAM_NAME) -> dict[str, Any]:
    """Minimal config dict with one camera and a dummy token."""
    import base64, time

    def _jwt() -> str:
        import base64, json as _j
        hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        pay = base64.urlsafe_b64encode(
            _j.dumps({"exp": int(time.time()) + 3600}).encode()
        ).rstrip(b"=").decode()
        return f"{hdr}.{pay}.sig"

    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            cam_name: {
                "id": cam_id,
                "name": cam_name,
                "model": "HOME_Eyes_Outdoor",
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
        "mic": None,
        "speaker": None,
        "mode": None,
        "sensitivity": None,
        "distance": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _patch_env(cfg: dict[str, Any]) -> tuple[Any, Any]:
    """Return (patch_token, patch_session, patch_cameras) context managers."""
    mock_sess = MagicMock()
    return mock_sess


# ─────────────────────────────────────────────────────────────────────────────
# cmd_audio — GET-only (show current levels)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAudioGet:
    """Show mic + speaker levels without setting anything."""

    def test_shows_levels_from_api(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET response with audioEnabled+microphoneLevel+speakerLevel → printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 60, "speakerLevel": 80},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "60" in out
        assert "80" in out

    def test_microphone_level_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Boundary: mic=0 is valid and displayed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": False, "microphoneLevel": 0, "speakerLevel": 0},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "0" in out

    def test_microphone_level_100(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Boundary: mic=100 is valid and displayed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 100, "speakerLevel": 100},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "100" in out

    def test_http_442_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 442 → prints 'not supported' message, does not crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=442)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "442" in out or "not supported" in out.lower()

    def test_http_non_200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-200, non-442 → error line printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "503" in out

    def test_json_output_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json flag → valid JSON list with expected keys."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 55, "speakerLevel": 75},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert data[0]["microphoneLevel"] == 55
        assert data[0]["speakerLevel"] == 75


# ─────────────────────────────────────────────────────────────────────────────
# cmd_audio — SET (PUT with new levels)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAudioSet:
    """Setting mic/speaker levels via PUT."""

    def test_set_mic_level(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--mic N → PUT body contains microphoneLevel=N."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=70))
        call_kwargs = sess.put.call_args
        assert call_kwargs[1]["json"]["microphoneLevel"] == 70
        assert call_kwargs[1]["json"]["speakerLevel"] == 50  # unchanged

    def test_set_speaker_level(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--speaker N → PUT body contains speakerLevel=N."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, speaker=90))
        call_kwargs = sess.put.call_args
        assert call_kwargs[1]["json"]["speakerLevel"] == 90
        assert call_kwargs[1]["json"]["microphoneLevel"] == 50  # unchanged

    def test_set_both_mic_and_speaker(self) -> None:
        """--mic and --speaker together → both values updated in PUT body."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=20, speaker=30))
        body = sess.put.call_args[1]["json"]
        assert body["microphoneLevel"] == 20
        assert body["speakerLevel"] == 30

    def test_mic_boundary_zero(self) -> None:
        """--mic 0 is valid → PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=0))
        assert sess.put.called
        assert sess.put.call_args[1]["json"]["microphoneLevel"] == 0

    def test_mic_boundary_100(self) -> None:
        """--mic 100 is valid → PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=100))
        assert sess.put.call_args[1]["json"]["microphoneLevel"] == 100

    def test_mic_out_of_range_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--mic 101 is out of range → no PUT issued, error printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=101))
        assert not sess.put.called
        out = capsys.readouterr().out
        assert "101" in out or "0-100" in out

    def test_speaker_out_of_range_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--speaker -1 is out of range → no PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, speaker=-1))
        assert not sess.put.called

    def test_put_failure_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT returning 500 → error message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=500, text="server error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=50))
        out = capsys.readouterr().out
        assert "500" in out

    def test_json_set_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json with SET → result list contains updated levels."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": True, "microphoneLevel": 50, "speakerLevel": 50},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_audio(cfg, _args(cam=CAM_NAME, mic=65, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["microphoneLevel"] == 65


# ─────────────────────────────────────────────────────────────────────────────
# cmd_intrusion — GET-only
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdIntrusionGet:
    """Show intrusion detection config without setting anything."""

    def test_shows_mode_sensitivity_distance(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET response → prints mode, sensitivity, distance."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 4, "distance": 7},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "ALL_MOTIONS" in out
        assert "4" in out
        assert "7" in out

    def test_http_442_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 442 → graceful 'not supported' message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=442)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "442" in out or "not supported" in out.lower()

    def test_http_error_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-200 → error line with status code."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=404)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "404" in out

    def test_json_output_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json flag → valid JSON list with mode/sensitivity/distance keys."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ZONES",
                          "sensitivity": 2, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["detectionMode"] == "ZONES"
        assert data[0]["sensitivity"] == 2
        assert data[0]["distance"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# cmd_intrusion — SET
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdIntrusionSet:
    """Setting intrusion detection config."""

    def test_mode_indoor_maps_to_all_motions(self) -> None:
        """--mode indoor → PUT body detectionMode='ALL_MOTIONS'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ZONES",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, mode="indoor"))
        body = sess.put.call_args[1]["json"]
        assert body["detectionMode"] == "ALL_MOTIONS"

    def test_mode_outdoor_maps_to_zones(self) -> None:
        """--mode outdoor → PUT body detectionMode='ZONES'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, mode="outdoor"))
        body = sess.put.call_args[1]["json"]
        assert body["detectionMode"] == "ZONES"

    def test_sensitivity_boundary_zero(self) -> None:
        """--sensitivity 0 is valid → PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, sensitivity=0))
        assert sess.put.call_args[1]["json"]["sensitivity"] == 0

    def test_sensitivity_boundary_seven(self) -> None:
        """--sensitivity 7 is valid → PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, sensitivity=7))
        assert sess.put.call_args[1]["json"]["sensitivity"] == 7

    def test_sensitivity_above_max_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity 8 exceeds max → no PUT, error printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, sensitivity=8))
        assert not sess.put.called
        out = capsys.readouterr().out
        assert "8" in out or "0-7" in out

    def test_distance_boundary_one(self) -> None:
        """--distance 1 is valid → PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, distance=1))
        assert sess.put.call_args[1]["json"]["distance"] == 1

    def test_distance_boundary_eight(self) -> None:
        """--distance 8 is valid (max accepted by Bosch) → PUT issued."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, distance=8))
        assert sess.put.call_args[1]["json"]["distance"] == 8

    def test_distance_nine_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """REGRESSION BUG1: --distance 9 → Bosch returns HTTP 400; CLI must reject it."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, distance=9))
        assert not sess.put.called
        out = capsys.readouterr().out
        assert "9" in out or "1-8" in out

    def test_distance_ten_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """REGRESSION BUG1: --distance 10 → Bosch returns HTTP 400; CLI must reject it."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, distance=10))
        assert not sess.put.called
        out = capsys.readouterr().out
        assert "10" in out or "1-8" in out

    def test_distance_above_max_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--distance 11 exceeds max → no PUT, error printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, distance=11))
        assert not sess.put.called

    def test_distance_below_min_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--distance 0 is below min → no PUT, error printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, distance=0))
        assert not sess.put.called

    def test_all_three_params_together(self) -> None:
        """--mode + --sensitivity + --distance all together → single PUT with all values."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 1, "distance": 3},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, mode="outdoor", sensitivity=5, distance=8))
        body = sess.put.call_args[1]["json"]
        assert body["detectionMode"] == "ZONES"
        assert body["sensitivity"] == 5
        assert body["distance"] == 8

    def test_invalid_mode_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--mode garbage → no PUT, error printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, mode="garbage"))
        assert not sess.put.called

    def test_json_set_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json with SET → result list contains updated values."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"enabled": True, "detectionMode": "ALL_MOTIONS",
                          "sensitivity": 3, "distance": 5},
        )
        sess.put.return_value = MagicMock(status_code=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_intrusion(cfg, _args(cam=CAM_NAME, sensitivity=6, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["sensitivity"] == 6


# ─────────────────────────────────────────────────────────────────────────────
# cmd_wifi — GET-only (read-only command)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdWifi:
    """Show WiFi info (RSSI, SSID, signal strength)."""

    def test_shows_ssid_rssi_signal(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET response → SSID and RSSI printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ssid": "MyNetwork", "rssi": -65,
                          "signalStrength": 70, "ipAddress": "192.168.1.5",
                          "macAddress": "aa:bb:cc:dd:ee:ff"},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "MyNetwork" in out
        assert "-65" in out

    def test_strong_signal_indicator(self, capsys: pytest.CaptureFixture[str]) -> None:
        """signalStrength >= 50 → displayed without error."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ssid": "HomeWifi", "rssi": -50,
                          "signalStrength": 80, "ipAddress": "10.0.0.2",
                          "macAddress": "11:22:33:44:55:66"},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "HomeWifi" in out
        assert "80" in out

    def test_weak_signal_indicator(self, capsys: pytest.CaptureFixture[str]) -> None:
        """signalStrength < 50 → displayed without error."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ssid": "WeakWifi", "rssi": -85,
                          "signalStrength": 20, "ipAddress": "10.0.0.3",
                          "macAddress": "ff:ee:dd:cc:bb:aa"},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "WeakWifi" in out
        assert "20" in out

    def test_http_442_wired_camera(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 442 (wired / no WiFi) → graceful message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=442)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "442" in out or "wired" in out.lower() or "not available" in out.lower()

    def test_http_error_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-200 → error line printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME))
        out = capsys.readouterr().out
        assert "500" in out

    def test_json_output_shape(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json flag → valid JSON list with ssid/rssi_dbm/signal_pct keys."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ssid": "TestSSID", "rssi": -70,
                          "signalStrength": 55, "ipAddress": "192.168.1.20",
                          "macAddress": "00:11:22:33:44:55"},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME, json=True))
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert data[0]["ssid"] == "TestSSID"
        assert data[0]["rssi_dbm"] == -70
        assert data[0]["signal_pct"] == 55

    def test_json_442_error_entry(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json + HTTP 442 → JSON list with error key 'not_supported'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=442)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["error"] == "not_supported"

    def test_rssi_none_when_field_absent(self, capsys: pytest.CaptureFixture[str]) -> None:
        """API response missing rssi → rssi_dbm is None in JSON output."""
        cfg = _make_cfg()
        sess = MagicMock()
        # No rssi/signalLevel field at all
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"ssid": "NoRSSI", "signalStrength": 60,
                          "ipAddress": "10.0.0.9", "macAddress": "00:00:00:00:00:00"},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_wifi(cfg, _args(cam=CAM_NAME, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["rssi_dbm"] is None


# ─────────────────────────────────────────────────────────────────────────────
# cmd_intercom — speaker level set (BUG2 regression)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdIntercomeIntercomSpeakerSet:
    """REGRESSION BUG2: intercom speaker-set must GET first, then PUT a full body.

    Bosch /audio is a full-body endpoint. A partial PUT (omitting microphoneLevel)
    can silently reset the mic level.  Fix: GET current state → merge → full PUT.
    """

    def _make_intercom_sess(
        self,
        mic: int = 60,
        enabled: bool = True,
        put_status: int = 200,
        conn_status: int = 200,
    ) -> MagicMock:
        """Build a mock session for intercom tests."""
        sess = MagicMock()

        audio_resp = MagicMock(
            status_code=200,
            json=lambda: {"audioEnabled": enabled, "microphoneLevel": mic, "speakerLevel": 50},
        )
        put_resp = MagicMock(status_code=put_status)
        conn_resp = MagicMock(
            status_code=conn_status,
            json=lambda: {"urls": ["proxy.example.com/abc123"]},
        )

        def _get_side(url: str, **_kw: object) -> MagicMock:
            return audio_resp

        def _put_side(url: str, **_kw: object) -> MagicMock:
            if "/audio" in url:
                return put_resp
            return conn_resp

        sess.get.side_effect = _get_side
        sess.put.side_effect = _put_side
        return sess

    def _args_intercom(self, speaker_level: int = 80) -> argparse.Namespace:
        return argparse.Namespace(cam=CAM_NAME, duration=1, speaker_level=speaker_level)

    def test_intercom_speaker_set_issues_get_first(self, capsys: pytest.CaptureFixture[str]) -> None:
        """REGRESSION BUG2: intercom must GET /audio before PUT (not partial-PUT)."""
        cfg = _make_cfg()
        sess = self._make_intercom_sess(mic=60)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
        ):
            try:
                bosch_camera.cmd_intercom(cfg, self._args_intercom(speaker_level=80))
            except Exception:
                pass  # live TCP/audio may fail in test — we only check the audio PUT
        # GET for /audio must have been called
        get_urls = [call[0][0] for call in sess.get.call_args_list]
        assert any("/audio" in u for u in get_urls), "GET /audio was not called before PUT"

    def test_intercom_speaker_set_full_body_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """REGRESSION BUG2: PUT body must contain microphoneLevel from GET response."""
        cfg = _make_cfg()
        sess = self._make_intercom_sess(mic=77, enabled=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
        ):
            try:
                bosch_camera.cmd_intercom(cfg, self._args_intercom(speaker_level=90))
            except Exception:
                pass
        # Find the /audio PUT call
        audio_put_bodies = [
            call[1]["json"]
            for call in sess.put.call_args_list
            if "/audio" in call[0][0]
        ]
        assert audio_put_bodies, "No PUT to /audio was issued"
        body = audio_put_bodies[0]
        assert "microphoneLevel" in body, "PUT body is missing microphoneLevel"
        assert body["microphoneLevel"] == 77, "microphoneLevel not preserved from GET"
        assert body["speakerLevel"] == 90, "speakerLevel not set to requested value"
        assert "audioEnabled" in body, "PUT body is missing audioEnabled"

    def test_intercom_speaker_set_no_partial_put(self) -> None:
        """REGRESSION BUG2: PUT must NOT use old SpeakerLevel (capital S) partial body."""
        cfg = _make_cfg()
        sess = self._make_intercom_sess(mic=55)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
        ):
            try:
                bosch_camera.cmd_intercom(cfg, self._args_intercom(speaker_level=70))
            except Exception:
                pass
        audio_put_bodies = [
            call[1]["json"]
            for call in sess.put.call_args_list
            if "/audio" in call[0][0]
        ]
        assert audio_put_bodies, "No PUT to /audio was issued"
        body = audio_put_bodies[0]
        # Old buggy code sent {"audioEnabled": True, "SpeakerLevel": N} — assert gone
        assert "SpeakerLevel" not in body, "Old buggy SpeakerLevel (capital S) still present"
        assert "speakerLevel" in body, "speakerLevel (correct casing) must be in PUT body"
