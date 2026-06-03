"""
Tests for 5 previously-untested CLI handlers:
  cmd_recording, cmd_siren, cmd_notifications, cmd_notification_types, cmd_unread

PIN_EVERY_MODE: one test per discrete mode/level/type + default + error/garbage.
All fixtures use FAKE IDs only; no real credentials, IPs, or device info.

Source: bosch_camera.py handlers + test_diag_commands.py / test_audio_intrusion_wifi.py style.
"""

from __future__ import annotations

import argparse
import base64
import json as _json_mod
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_notifications,
    cmd_notification_types,
    cmd_recording,
    cmd_siren,
    cmd_unread,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
CAM_ID_INDOOR = "BBBBCCCC-1111-2222-3333-444455556666"
CAM_NAME_INDOOR = "Kamera"


def _jwt() -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = base64.urlsafe_b64encode(
        _json_mod.dumps({"exp": int(time.time()) + 3600}).encode()
    ).rstrip(b"=").decode()
    return f"{hdr}.{pay}.sig"


def _make_cfg(
    cam_id: str = CAM_ID,
    cam_name: str = CAM_NAME,
    model: str = "HOME_Eyes_Outdoor",
) -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            cam_name: {
                "id": cam_id,
                "name": cam_name,
                "model": model,
                "firmware": "9.40.102",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _make_cfg_indoor() -> dict[str, Any]:
    return _make_cfg(cam_id=CAM_ID_INDOOR, cam_name=CAM_NAME_INDOOR, model="HOME_Eyes_Indoor")


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "action": None,
        "sound_on": False,
        "sound_off": False,
        "stop": False,
        "set_duration": None,
        "set": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _ok(data: Any = None, status: int = 200) -> MagicMock:
    """Return a mock response with status_code and .json()."""
    m = MagicMock()
    m.status_code = status
    m.json = lambda: data if data is not None else {}
    m.text = ""
    return m


def _err(status: int) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.text = f"HTTP {status}"
    return m


# ─────────────────────────────────────────────────────────────────────────────
# cmd_recording
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRecording:
    """Tests for cmd_recording — GET + sound-on/off paths."""

    def _patch(
        self, cfg: dict[str, Any], get_ret: MagicMock, put_ret: MagicMock | None = None
    ) -> tuple[MagicMock, Any, Any]:
        sess = MagicMock()
        sess.get.return_value = get_ret
        if put_ret is not None:
            sess.put.return_value = put_ret
        tok = patch.object(bosch_camera, "get_token", return_value="tok")
        ses = patch.object(bosch_camera, "make_session", return_value=sess)
        cam = patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"])
        return sess, tok, ses, cam  # type: ignore[return-value]

    def test_get_shows_sound_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET with recordSound=True → prints 'ON'."""
        cfg = _make_cfg()
        sess, tok, ses, cam = self._patch(cfg, _ok({"recordSound": True}))
        with tok, ses, cam:
            cmd_recording(cfg, _args())
        assert "ON" in capsys.readouterr().out

    def test_get_shows_sound_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET with recordSound=False → prints 'OFF'."""
        cfg = _make_cfg()
        sess, tok, ses, cam = self._patch(cfg, _ok({"recordSound": False}))
        with tok, ses, cam:
            cmd_recording(cfg, _args())
        assert "OFF" in capsys.readouterr().out

    def test_get_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET-only (no --sound-on/off) → hint line printed."""
        cfg = _make_cfg()
        sess, tok, ses, cam = self._patch(cfg, _ok({"recordSound": False}))
        with tok, ses, cam:
            cmd_recording(cfg, _args())
        out = capsys.readouterr().out
        assert "--sound-on" in out or "--sound-off" in out

    def test_sound_on_sends_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sound-on → PUT body has recordSound=True."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"recordSound": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_on=True))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["recordSound"] is True

    def test_sound_off_sends_false(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sound-off → PUT body has recordSound=False."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"recordSound": True})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_off=True))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["recordSound"] is False

    def test_sound_on_prints_on_after_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 204 after --sound-on → confirmation line with 'ON'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"recordSound": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_on=True))
        out = capsys.readouterr().out
        assert "ON" in out

    def test_get_401_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 401 → token-expired message, returns early, no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_on=True))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "token" in out.lower() or "expired" in out.lower() or "401" in out

    def test_get_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 503 → error message, no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args())
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "503" in out

    def test_put_failure_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 500 → failure message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"recordSound": False})
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_on=True))
        out = capsys.readouterr().out
        assert "500" in out or "failed" in out.lower()

    def test_sound_on_and_off_both_set_prefers_off(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Both --sound-on and --sound-off set → recordSound=False (off wins: applied last)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"recordSound": True})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_on=True, sound_off=True))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["recordSound"] is False

    def test_url_contains_cam_id(self) -> None:
        """GET + PUT URLs contain the correct camera ID."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"recordSound": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_recording(cfg, _args(sound_on=True))
        get_url = sess.get.call_args[0][0]
        put_url = sess.put.call_args[0][0]
        assert CAM_ID in get_url
        assert CAM_ID in put_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_siren
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdSiren:
    """Tests for cmd_siren — trigger/stop/duration paths."""

    def test_trigger_indoor_sends_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HOME_Eyes_Indoor + trigger → PUT body {"status": "ON"}."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["status"] == "ON"

    def test_stop_sends_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--stop → PUT body {"status": "OFF"}."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, stop=True))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["status"] == "OFF"

    def test_unsupported_model_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-Indoor model → prints unsupported, no PUT."""
        cfg = _make_cfg()  # HOME_Eyes_Outdoor
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "not supported" in out.lower() or "HOME_Eyes_Indoor" in out

    def test_no_cam_specified_multi_cam_aborts(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Two cameras without --cam specified → error, no PUT."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
            "cameras": {
                "CamA": {"id": CAM_ID, "name": "CamA", "model": "HOME_Eyes_Indoor", "firmware": "9.0"},
                "CamB": {
                    "id": CAM_ID_INDOOR, "name": "CamB",
                    "model": "HOME_Eyes_Indoor", "firmware": "9.0",
                },
            },
            "settings": {},
            "lan_ips": {},
        }
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args())
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "exactly one" in out.lower() or "siren" in out.lower()

    def test_trigger_success_prints_activated(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 200 → prints 'activated'."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR))
        out = capsys.readouterr().out
        assert "activated" in out.lower()

    def test_stop_success_prints_stopped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 200 + --stop → prints 'stopped'."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, stop=True))
        out = capsys.readouterr().out
        assert "stopped" in out.lower()

    def test_privacy_mode_443(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 443 → privacy-mode message."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _err(443)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR))
        out = capsys.readouterr().out
        assert "privacy" in out.lower()

    def test_http_442_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 442 → not-supported message."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _err(442)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR))
        out = capsys.readouterr().out
        assert "442" in out or "not supported" in out.lower()

    def test_http_500_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 500 → failure message with status code."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR))
        out = capsys.readouterr().out
        assert "500" in out or "failed" in out.lower()

    def test_set_duration_valid_sends_settings(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set-duration 60 → GET alarm_settings then PUT with alarmDelayInSeconds=60."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        # GET alarm_settings, then PUT alarm_settings, then PUT panic_alarm
        sess.get.return_value = _ok({"alarmDelayInSeconds": 30, "someOtherKey": "x"})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, set_duration=60))
        # First PUT call should be alarm_settings with the new duration
        first_put_args = sess.put.call_args_list[0]
        assert "alarm_settings" in first_put_args[0][0]
        assert first_put_args[1]["json"]["alarmDelayInSeconds"] == 60

    def test_set_duration_too_low_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set-duration 9 → rejected (must be 10-300), no PUT."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, set_duration=9))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "10" in out and "300" in out

    def test_set_duration_too_high_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set-duration 301 → rejected, no PUT."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, set_duration=301))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "10" in out and "300" in out

    def test_set_duration_boundary_min(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set-duration 10 → boundary min, accepted."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.get.return_value = _ok({"alarmDelayInSeconds": 30})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, set_duration=10))
        assert sess.put.call_count >= 1

    def test_set_duration_boundary_max(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set-duration 300 → boundary max, accepted."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.get.return_value = _ok({"alarmDelayInSeconds": 60})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, set_duration=300))
        assert sess.put.call_count >= 1

    def test_set_duration_privacy_443_returns_early(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PUT alarm_settings returns 443 → returns early, panic_alarm not called."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.get.return_value = _ok({"alarmDelayInSeconds": 30})
        sess.put.return_value = _err(443)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR, set_duration=60))
        # Only 1 PUT call (alarm_settings) — panic_alarm not reached
        assert sess.put.call_count == 1
        out = capsys.readouterr().out
        assert "privacy" in out.lower()

    def test_panic_alarm_url_contains_cam_id(self) -> None:
        """PUT URL for panic_alarm contains the correct camera ID."""
        cfg = _make_cfg_indoor()
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_siren(cfg, _args(cam=CAM_NAME_INDOOR))
        put_url = sess.put.call_args[0][0]
        assert CAM_ID_INDOOR in put_url
        assert "panic_alarm" in put_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_notifications
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdNotifications:
    """Tests for cmd_notifications — get / on / off / already-set / error paths."""

    def _cam_list_response(
        self, cam_id: str = CAM_ID, status: str = "ALWAYS_OFF"
    ) -> MagicMock:
        return _ok([{"id": cam_id, "notificationsEnabledStatus": status}])

    def test_get_shows_current_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET-only (no action) → current state printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args())
        out = capsys.readouterr().out
        assert "ALWAYS_OFF" in out

    def test_get_shows_follow_schedule(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FOLLOW_CAMERA_SCHEDULE state → printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="FOLLOW_CAMERA_SCHEDULE")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args())
        out = capsys.readouterr().out
        assert "FOLLOW_CAMERA_SCHEDULE" in out

    def test_get_shows_on_camera_schedule(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ON_CAMERA_SCHEDULE state → printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ON_CAMERA_SCHEDULE")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args())
        out = capsys.readouterr().out
        assert "ON_CAMERA_SCHEDULE" in out

    def test_on_sends_follow_camera_schedule(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=on → PUT body {"enabledNotificationsStatus": "FOLLOW_CAMERA_SCHEDULE"}."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["enabledNotificationsStatus"] == "FOLLOW_CAMERA_SCHEDULE"

    def test_off_sends_always_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=off → PUT body {"enabledNotificationsStatus": "ALWAYS_OFF"}."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="FOLLOW_CAMERA_SCHEDULE")
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="off"))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["enabledNotificationsStatus"] == "ALWAYS_OFF"

    def test_on_when_already_follow_schedule_no_put(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """action=on + current=FOLLOW_CAMERA_SCHEDULE → no PUT (already on)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="FOLLOW_CAMERA_SCHEDULE")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "already" in out.lower()

    def test_on_when_already_on_camera_schedule_no_put(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """action=on + current=ON_CAMERA_SCHEDULE → no PUT (already on)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ON_CAMERA_SCHEDULE")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "already" in out.lower()

    def test_off_when_already_off_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=off + current=ALWAYS_OFF → no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="off"))
        sess.put.assert_not_called()

    def test_get_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 401 → token expired message, no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "token" in out.lower() or "expired" in out.lower() or "401" in out

    def test_put_failure_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 500 → failure message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        out = capsys.readouterr().out
        assert "500" in out or "failed" in out.lower()

    def test_cam_arg_as_on_without_explicit_action(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cam='on' with no explicit action → treated as action=on (cam_arg swap)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(cam="on", action=None))
        sess.put.assert_called_once()

    def test_on_success_prints_confirmation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 204 + action=on → confirmation line printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        out = capsys.readouterr().out
        assert "FOLLOW_CAMERA_SCHEDULE" in out

    def test_enable_notifications_url_contains_cam_id(self) -> None:
        """PUT URL contains the correct camera ID and endpoint."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._cam_list_response(status="ALWAYS_OFF")
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_notifications(cfg, _args(action="on"))
        put_url = sess.put.call_args[0][0]
        assert CAM_ID in put_url
        assert "enable_notifications" in put_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_notification_types
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdNotificationTypes:
    """Tests for cmd_notification_types — get / set / error paths."""

    def test_get_prints_types(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 200 → all type keys printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": True, "person": False, "audio": True})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args())
        out = capsys.readouterr().out
        assert "movement" in out
        assert "person" in out
        assert "audio" in out

    def test_get_shows_on_off_labels(self, capsys: pytest.CaptureFixture[str]) -> None:
        """True → 'ON', False → 'OFF' labels in output."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": True, "person": False})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args())
        out = capsys.readouterr().out
        assert "ON" in out
        assert "OFF" in out

    def test_get_prints_toggle_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET-only → toggle hint printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": True})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args())
        out = capsys.readouterr().out
        assert "--set" in out or "toggle" in out.lower()

    def test_set_on_sends_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set movement=on → PUT body has movement=True."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False, "person": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=on"]))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["movement"] is True

    def test_set_off_sends_false(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set person=off → PUT body has person=False."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": True, "person": True})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["person=off"]))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["person"] is False

    def test_set_true_variant(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set audio=true → equivalent to 'on'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"audio": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["audio=true"]))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["audio"] is True

    def test_set_false_variant(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set audio=false → equivalent to 'off'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"audio": True})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["audio=false"]))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["audio"] is False

    def test_set_1_variant(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set movement=1 → True."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=1"]))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["movement"] is True

    def test_set_0_variant(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set movement=0 → False."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": True})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=0"]))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["movement"] is False

    def test_set_invalid_value_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set movement=maybe → warning printed, key skipped."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=maybe"]))
        out = capsys.readouterr().out
        assert "invalid" in out.lower() or "maybe" in out

    def test_set_multiple_pairs(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--set movement=on person=off → both applied in one PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False, "person": True})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=on", "person=off"]))
        _, kwargs = sess.put.call_args
        assert kwargs["json"]["movement"] is True
        assert kwargs["json"]["person"] is False

    def test_get_401_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 401 → token-expired message, returns early."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args())
        out = capsys.readouterr().out
        assert "token" in out.lower() or "expired" in out.lower() or "401" in out

    def test_get_444_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 444 → camera offline message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(444)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args())
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "unavailable" in out.lower()

    def test_get_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 503 → error message with status code."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args())
        out = capsys.readouterr().out
        assert "503" in out

    def test_put_failure_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 500 → failure message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False})
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=on"]))
        out = capsys.readouterr().out
        assert "500" in out or "failed" in out.lower()

    def test_put_success_prints_updated_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 204 → updated state confirmed in output."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=on"]))
        out = capsys.readouterr().out
        assert "updated" in out.lower() or "movement" in out

    def test_notifications_url_contains_cam_id(self) -> None:
        """GET + PUT URLs contain correct camera ID."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"movement": False})
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_notification_types(cfg, _args(set=["movement=on"]))
        get_url = sess.get.call_args[0][0]
        put_url = sess.put.call_args[0][0]
        assert CAM_ID in get_url
        assert CAM_ID in put_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_unread
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdUnread:
    """Tests for cmd_unread — count display / error paths."""

    def test_prints_count_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """numberOfUnreadEvents=0 → '0 unread event(s)' printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"numberOfUnreadEvents": 0})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        out = capsys.readouterr().out
        assert "0" in out

    def test_prints_count_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """numberOfUnreadEvents=5 → '5' in output."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"numberOfUnreadEvents": 5})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        assert "5" in capsys.readouterr().out

    def test_prints_count_large(self, capsys: pytest.CaptureFixture[str]) -> None:
        """numberOfUnreadEvents=999 → '999' in output."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"numberOfUnreadEvents": 999})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        assert "999" in capsys.readouterr().out

    def test_missing_field_defaults_to_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Response without numberOfUnreadEvents field → defaults to 0."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"someOtherField": "value"})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        out = capsys.readouterr().out
        assert "0" in out

    def test_401_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 401 → token expired message, returns early."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        out = capsys.readouterr().out
        assert "token" in out.lower() or "expired" in out.lower() or "401" in out

    def test_non200_error_prints_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 503 → status code printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _err(503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        out = capsys.readouterr().out
        assert "503" in out

    def test_url_contains_cam_id(self) -> None:
        """GET URL uses /v11/video_inputs/{cam_id}."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"numberOfUnreadEvents": 0})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        get_url = sess.get.call_args[0][0]
        assert CAM_ID in get_url

    def test_multi_cam_prints_all(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Two cameras → both counts printed."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
            "cameras": {
                "CamA": {"id": CAM_ID, "name": "CamA", "model": "HOME_Eyes_Outdoor", "firmware": "9.0"},
                "CamB": {
                    "id": CAM_ID_INDOOR,
                    "name": "CamB",
                    "model": "HOME_Eyes_Indoor",
                    "firmware": "9.0",
                },
            },
            "settings": {},
            "lan_ips": {},
        }
        sess = MagicMock()
        sess.get.side_effect = [
            _ok({"numberOfUnreadEvents": 3}),
            _ok({"numberOfUnreadEvents": 7}),
        ]
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        out = capsys.readouterr().out
        assert "3" in out
        assert "7" in out

    def test_401_stops_after_first_cam(self, capsys: pytest.CaptureFixture[str]) -> None:
        """First camera GET 401 → returns early, second camera not fetched."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
            "cameras": {
                "CamA": {
                    "id": CAM_ID, "name": "CamA",
                    "model": "HOME_Eyes_Outdoor", "firmware": "9.0",
                },
                "CamB": {
                    "id": CAM_ID_INDOOR, "name": "CamB",
                    "model": "HOME_Eyes_Indoor", "firmware": "9.0",
                },
            },
            "settings": {},
            "lan_ips": {},
        }
        sess = MagicMock()
        sess.get.side_effect = [_err(401), _ok({"numberOfUnreadEvents": 7})]
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_unread(cfg, _args())
        # Only one GET call — returns early after 401
        assert sess.get.call_count == 1
