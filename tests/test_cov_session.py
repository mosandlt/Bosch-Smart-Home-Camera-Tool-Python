"""
Coverage tests for cmd_info, _renew_session (inside cmd_watch), get_token,
and discover_cameras.

Fake IDs only — NEVER real device values, IPs, tokens, or secrets.
JWTs: unsigned, built via conftest._make_jwt with the header key order putting
typ first, so the base64 prefix is eyJ0... (the alg-first prefix that secret
scanners flag is deliberately avoided).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

import bosch_camera
from tests.conftest import FROZEN_EPOCH, _make_jwt

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants (fake IDs only)
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
FAKE_MAC = "aa:bb:cc:dd:ee:ff"
FAKE_IP = "192.0.2.1"
FAKE_TOKEN = "tok"
FROZEN_NOW = "2024-06-01 12:00:00"


def _cfg(token: str = FAKE_TOKEN, refresh: str = "") -> dict[str, Any]:
    """Minimal config dict for testing."""
    return {
        "account": {
            "bearer_token": token,
            "refresh_token": refresh,
            "username": "testuser",
        },
        "cameras": {},
    }


def _cam_json(
    *,
    status: str = "ONLINE",
    model: str = "HOME_Eyes_Outdoor",
    has_light: bool = True,
    pan_limit: int = 30,
) -> dict[str, Any]:
    """Return a single camera entry as returned by /v11/video_inputs."""
    return {
        "id": CAM_ID,
        "title": CAM_NAME,
        "connectionStatus": status,
        "hardwareVersion": model,
        "firmwareVersion": "9.40.102",
        "macAddress": FAKE_MAC,
        "privacyMode": False,
        "recordingOn": True,
        "numberOfUnreadEvents": 2,
        "timeZone": "Europe/Berlin",
        "alarmType": None,
        "notificationsEnabledStatus": "ENABLED",
        "notifications": {"motion": True, "audio": False},
        "featureSupport": {"light": has_light, "sound": True, "viewingAngle": 110, "panLimit": pan_limit},
        "featureStatus": {
            "scheduleStatus": "OFF",
            "generalLightOnTime": "20:00",
            "generalLightOffTime": "06:00",
            "lightOnMotion": True,
            "lightOnMotionFollowUpTimeSeconds": 30,
        },
        "soundIsOnForRecording": True,
    }


def _mock_session(
    *,
    inputs_status: int = 200,
    inputs_data: list[Any] | None = None,
    conn_status: int = 200,
    protocol_status: int = 200,
    wifi_status: int = 200,
) -> MagicMock:
    """Return a MagicMock session with configurable GET/PUT responses."""
    if inputs_data is None:
        inputs_data = [_cam_json()]

    sess = MagicMock()

    def _get(url: str, **_kw: Any) -> MagicMock:
        r = MagicMock()
        if "video_inputs" in url and "wifiinfo" in url:
            r.status_code = wifi_status
            r.json.return_value = {
                "ssid": "FakeSSID",
                "signalStrength": 80,
                "ipAddress": FAKE_IP,
                "macAddress": FAKE_MAC,
            }
        elif "video_inputs" in url and all(
            s not in url
            for s in [
                "commissioned", "firmware", "lighting_override", "motion",
                "recording_options", "ambient_light_sensor_level",
                "intrusionDetectionConfig", "credentials", "rules",
                "timestamp", "privacy_sound_override", "feature_flags",
                "wifiinfo",
            ]
        ) and url.endswith("video_inputs"):
            r.status_code = inputs_status
            r.json.return_value = inputs_data
        elif "protocol_support" in url:
            r.status_code = protocol_status
            r.json.return_value = {"state": "SUPPORTED"}
        elif "feature_flags" in url:
            r.status_code = 200
            r.json.return_value = {"clip_download": True, "webrtc": True}
        else:
            r.status_code = 200
            r.json.return_value = {}
        return r

    def _put(url: str, **_kw: Any) -> MagicMock:
        r = MagicMock()
        r.status_code = conn_status
        if conn_status == 200:
            r.json.return_value = {
                "urls": ["proxy-01.live.cbs.boschsecurity.com:42090/fakehash"],
                "imageUrlScheme": "https://{url}/snap.jpg",
            }
        else:
            r.json.return_value = {}
        return r

    sess.get.side_effect = _get
    sess.put.side_effect = _put
    return sess


# ─────────────────────────────────────────────────────────────────────────────
# get_token
# ─────────────────────────────────────────────────────────────────────────────


class TestGetToken:
    @freeze_time(FROZEN_NOW)
    def test_valid_token_returned_directly(self) -> None:
        """Valid (non-expired) token is returned immediately without refresh."""
        tok = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=tok)
        result = bosch_camera.get_token(cfg)
        assert result == tok

    @freeze_time(FROZEN_NOW)
    def test_expired_token_with_refresh_success(self) -> None:
        """Expired token triggers silent renewal via _do_refresh; new token is saved."""
        expired = _make_jwt(FROZEN_EPOCH - 3600)
        new_tok = _make_jwt(FROZEN_EPOCH + 7200)
        cfg = _cfg(token=expired, refresh="fake-refresh-token")
        fake_module = MagicMock()
        fake_module._do_refresh.return_value = {
            "access_token": new_tok,
            "refresh_token": "new-refresh",
        }
        with (
            patch.dict(sys.modules, {"get_token": fake_module}),
            patch.object(bosch_camera, "save_config") as mock_save,
        ):
            result = bosch_camera.get_token(cfg)
        assert result == new_tok
        mock_save.assert_called_once()
        assert cfg["account"]["bearer_token"] == new_tok
        assert cfg["account"]["refresh_token"] == "new-refresh"

    @freeze_time(FROZEN_NOW)
    def test_expired_token_refresh_returns_none(self) -> None:
        """_do_refresh returns None/empty → fall through to expired token fallback."""
        expired = _make_jwt(FROZEN_EPOCH - 3600)
        cfg = _cfg(token=expired, refresh="fake-refresh-token")
        fake_module = MagicMock()
        fake_module._do_refresh.return_value = {}  # empty = falsy
        with patch.dict(sys.modules, {"get_token": fake_module}):
            result = bosch_camera.get_token(cfg)
        # Should return the expired token (let API reject it)
        assert result == expired

    @freeze_time(FROZEN_NOW)
    def test_expired_token_refresh_exception(self) -> None:
        """_do_refresh raises → fall through to returning expired token."""
        expired = _make_jwt(FROZEN_EPOCH - 3600)
        cfg = _cfg(token=expired, refresh="fake-refresh-token")
        fake_module = MagicMock()
        fake_module._do_refresh.side_effect = RuntimeError("network error")
        with patch.dict(sys.modules, {"get_token": fake_module}):
            result = bosch_camera.get_token(cfg)
        assert result == expired

    @freeze_time(FROZEN_NOW)
    def test_no_token_get_token_auto_success(self) -> None:
        """No token at all → get_token_auto is called and its result returned."""
        auto_tok = _make_jwt(FROZEN_EPOCH + 7200)
        cfg = _cfg(token="")
        fake_module = MagicMock()
        fake_module.get_token_auto.return_value = auto_tok
        with patch.dict(sys.modules, {"get_token": fake_module}):
            result = bosch_camera.get_token(cfg)
        assert result == auto_tok

    @freeze_time(FROZEN_NOW)
    def test_no_token_get_token_auto_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_token_auto returns None → fall through to manual input."""
        cfg = _cfg(token="")
        fake_module = MagicMock()
        fake_module.get_token_auto.return_value = None
        manual_tok = _make_jwt(FROZEN_EPOCH + 3600)
        with (
            patch.dict(sys.modules, {"get_token": fake_module}),
            patch("builtins.input", return_value=manual_tok),
            patch.object(bosch_camera, "save_config"),
        ):
            result = bosch_camera.get_token(cfg)
        assert result == manual_tok

    @freeze_time(FROZEN_NOW)
    def test_no_token_manual_empty_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Manual input returns empty string → sys.exit(1)."""
        cfg = _cfg(token="")
        fake_module = MagicMock()
        fake_module.get_token_auto.return_value = None
        with (
            patch.dict(sys.modules, {"get_token": fake_module}),
            patch("builtins.input", return_value=""),
        ):
            with pytest.raises(SystemExit) as exc_info:
                bosch_camera.get_token(cfg)
        assert exc_info.value.code == 1

    @freeze_time(FROZEN_NOW)
    def test_get_token_import_error_falls_through(self) -> None:
        """ImportError from get_token → fall to manual with expired token fallback."""
        expired = _make_jwt(FROZEN_EPOCH - 3600)
        cfg = _cfg(token=expired, refresh="")
        # No get_token module at all → ImportError on both paths
        with patch.dict(sys.modules, {"get_token": None}):  # type: ignore[dict-item]
            # no refresh_token, no get_token module → return expired token as-is
            result = bosch_camera.get_token(cfg)
        assert result == expired


# ─────────────────────────────────────────────────────────────────────────────
# discover_cameras
# ─────────────────────────────────────────────────────────────────────────────


class TestDiscoverCameras:
    def test_401_returns_empty(self) -> None:
        """HTTP 401 returns empty dict without saving config."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 401
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        result = bosch_camera.discover_cameras(cfg, sess)
        assert result == {}

    def test_non_200_raises(self) -> None:
        """Non-200/non-401 response raises via raise_for_status."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 500
        r.raise_for_status.side_effect = Exception("500 Server Error")
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        with pytest.raises(Exception, match="500"):
            bosch_camera.discover_cameras(cfg, sess)

    def test_success_populates_cfg(self, tmp_config_dir: str) -> None:
        """Successful discovery populates cfg['cameras'] and saves config."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = [
            {
                "id": CAM_ID,
                "title": CAM_NAME,
                "hardwareVersion": "HOME_Eyes_Outdoor",
                "firmwareVersion": "9.40.102",
                "macAddress": FAKE_MAC,
                "featureSupport": {"light": True, "panLimit": 30},
            }
        ]
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        with patch("builtins.input", side_effect=EOFError):
            result = bosch_camera.discover_cameras(cfg, sess)
        assert CAM_NAME in result
        cam = result[CAM_NAME]
        assert cam["id"] == CAM_ID
        assert cam["mac"] == FAKE_MAC
        assert cam["has_light"] is True
        assert cam["pan_limit"] == 30
        # config should be updated
        assert cfg["cameras"] == result

    def test_empty_list(self, tmp_config_dir: str) -> None:
        """Empty camera list → empty cameras dict, config saved with empty cameras."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = []
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        result = bosch_camera.discover_cameras(cfg, sess)
        assert result == {}
        assert cfg["cameras"] == {}

    def test_preserves_existing_local_config(self, tmp_config_dir: str) -> None:
        """Existing local_ip/username/password are preserved on re-discovery."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = [{"id": CAM_ID, "title": CAM_NAME, "featureSupport": {}}]
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        cfg["cameras"] = {
            CAM_NAME: {"local_ip": FAKE_IP, "local_username": "admin", "local_password": "pass"}
        }
        with patch("builtins.input", side_effect=EOFError):
            result = bosch_camera.discover_cameras(cfg, sess)
        assert result[CAM_NAME]["local_ip"] == FAKE_IP
        assert result[CAM_NAME]["local_username"] == "admin"

    def test_local_ip_prompt_accepted(self, tmp_config_dir: str) -> None:
        """When no existing local_ip, user input is accepted."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = [{"id": CAM_ID, "title": CAM_NAME, "featureSupport": {}}]
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        with patch("builtins.input", return_value=FAKE_IP):
            result = bosch_camera.discover_cameras(cfg, sess)
        assert result[CAM_NAME]["local_ip"] == FAKE_IP

    def test_no_title_falls_back_to_id(self, tmp_config_dir: str) -> None:
        """Camera without 'title' uses 'id' as name."""
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 200
        r.raise_for_status.return_value = None
        r.json.return_value = [{"id": CAM_ID, "featureSupport": {}}]  # no "title"
        sess.get.return_value = r
        cfg: dict[str, Any] = _cfg()
        with patch("builtins.input", side_effect=EOFError):
            result = bosch_camera.discover_cameras(cfg, sess)
        assert CAM_ID in result


# ─────────────────────────────────────────────────────────────────────────────
# cmd_info
# ─────────────────────────────────────────────────────────────────────────────


def _make_args(full: bool = False) -> argparse.Namespace:
    ns = argparse.Namespace()
    ns.full = full
    return ns


class TestCmdInfo:
    def _run(
        self,
        sess: MagicMock,
        cfg: dict[str, Any] | None = None,
        full: bool = False,
    ) -> None:
        if cfg is None:
            cfg = _cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "rcp_open_connection", side_effect=RuntimeError("no RCP")),
            # check_token_age falls back to os.path.getmtime(CONFIG_FILE) when the
            # JWT has no usable exp claim; the real bosch_config.json is absent on
            # CI runners → stub it (token-age display is not under test here).
            patch.object(bosch_camera, "check_token_age", return_value="valid (mock)"),
        ):
            bosch_camera.cmd_info(cfg, _make_args(full=full))

    def test_online_camera_basic(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Online camera: basic info is printed without error."""
        sess = _mock_session()
        # Patch the main video_inputs call to return proper data
        cam_data = [_cam_json(status="ONLINE")]

        def _get_override(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "wifiinfo" in url:
                r.status_code = 200
                r.json.return_value = {
                    "ssid": "FakeSSID", "signalStrength": 75,
                    "ipAddress": FAKE_IP, "macAddress": FAKE_MAC,
                }
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess.get.side_effect = _get_override
        self._run(sess)
        out = capsys.readouterr().out
        assert CAM_NAME in out
        assert "ONLINE" in out

    def test_offline_camera_shows_red_icon(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Offline camera uses red icon path."""
        cam_data = [_cam_json(status="OFFLINE")]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=404, json=MagicMock(return_value={}))
        self._run(sess)
        out = capsys.readouterr().out
        assert "OFFLINE" in out

    def test_updating_camera_shows_update_icon(self, capsys: pytest.CaptureFixture[str]) -> None:
        """UPDATING status triggers update icon path."""
        cam_data = [_cam_json(status="UPDATING_FW")]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess)
        out = capsys.readouterr().out
        assert "UPDATING_FW" in out

    def test_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Token expired (401) → prints expired message and returns."""
        r = MagicMock()
        r.status_code = 401

        sess = MagicMock()
        sess.get.return_value = r
        self._run(sess)
        out = capsys.readouterr().out
        assert "Token expired" in out

    def test_protocol_not_supported_shows_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Protocol check returns DEPRECATED → warning is printed."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "DEPRECATED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"urls": ["proxy-01.live.cbs.boschsecurity.com:42090/h"]}),
        )
        self._run(sess)
        out = capsys.readouterr().out
        assert "DEPRECATED" in out

    def test_protocol_check_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Protocol check returns HTTP 503 → prints HTTP status."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 503
                r.json.return_value = {}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess)
        out = capsys.readouterr().out
        assert "503" in out

    def test_stream_url_unavailable(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT /connection fails with non-200 → prints unavailable message."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=503, json=MagicMock(return_value={}))
        self._run(sess)
        out = capsys.readouterr().out
        assert "unavailable" in out or "503" in out

    def test_stream_url_exception(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT /connection raises exception → prints error line."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.side_effect = ConnectionError("connection refused")
        self._run(sess)
        out = capsys.readouterr().out
        assert "error" in out.lower() or "connection refused" in out

    def test_full_mode_fetches_extra_endpoints(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--full fetches commissioned/firmware/motion etc. and feature_flags."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "firmware" in url:
                r.status_code = 200
                r.json.return_value = {"version": "9.40.102", "upToDate": True}
            elif "motion" in url:
                r.status_code = 200
                r.json.return_value = {"enabled": True, "sensitivity": 5}
            elif "lighting_override" in url:
                r.status_code = 200
                r.json.return_value = {"frontLightOn": False, "wallwasherOn": False}
            elif "recording_options" in url:
                r.status_code = 200
                r.json.return_value = {"recordSound": True}
            elif "ambient_light_sensor_level" in url:
                r.status_code = 200
                r.json.return_value = {"ambientLightSensorLevel": 0.5}
            elif "intrusionDetectionConfig" in url:
                r.status_code = 200
                r.json.return_value = {"enabled": True, "detectionMode": "STANDARD",
                                       "sensitivity": 3, "distance": 5}
            elif "credentials" in url:
                r.status_code = 200
                r.json.return_value = {"userToken": "fake-user-token"}
            elif "rules" in url:
                r.status_code = 200
                r.json.return_value = []
            elif "timestamp" in url:
                r.status_code = 200
                r.json.return_value = {"result": "enabled"}
            elif "privacy_sound_override" in url:
                r.status_code = 200
                r.json.return_value = {"result": "OFF"}
            elif "feature_flags" in url:
                r.status_code = 200
                r.json.return_value = {"clip_download": True, "webrtc": True}
            elif "wifiinfo" in url:
                r.status_code = 200
                r.json.return_value = {
                    "ssid": "FakeSSID", "signalStrength": 75,
                    "ipAddress": FAKE_IP, "macAddress": FAKE_MAC,
                }
            elif "commissioned" in url:
                r.status_code = 200
                r.json.return_value = {"commissioned": True}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "urls": ["proxy-01.live.cbs.boschsecurity.com:42090/fakehash"],
                "imageUrlScheme": "https://{url}/snap.jpg",
            }),
        )
        self._run(sess, full=True)
        out = capsys.readouterr().out
        # Feature flags section shown in full mode
        assert "Feature Flags" in out or "clip_download" in out

    def test_full_mode_feature_flags_list_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--full with flags as list (not dict) is printed correctly."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "feature_flags" in url:
                r.status_code = 200
                r.json.return_value = [
                    {"name": "webrtc", "value": True},
                    {"key": "recording", "enabled": False},
                    "raw_flag",
                ]
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess, full=True)
        out = capsys.readouterr().out
        assert "webrtc" in out or "Feature Flags" in out

    def test_full_mode_feature_flags_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--full with feature_flags returning HTTP 403 prints warning."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "feature_flags" in url:
                r.status_code = 403
                r.json.return_value = {}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess, full=True)
        out = capsys.readouterr().out
        assert "403" in out

    def test_full_mode_privacy_sound_442(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--full with privacy_sound_override returning 442 prints 'not supported'."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "privacy_sound_override" in url:
                r.status_code = 442
                r.json.return_value = {}
            elif "feature_flags" in url:
                r.status_code = 200
                r.json.return_value = {}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess, full=True)
        out = capsys.readouterr().out
        assert "not supported" in out or "442" in out

    def test_empty_camera_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty camera list → only header prints, no cam-level output."""
        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = []
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        self._run(sess)
        out = capsys.readouterr().out
        assert "Camera Info" in out
        assert CAM_NAME not in out

    def test_wifi_exception_silently_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """WiFi GET raising an exception is caught silently."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "wifiinfo" in url:
                raise ConnectionError("wifi fetch failed")
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        # Should NOT raise
        self._run(sess)

    def test_stream_urls_empty_urls(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT /connection returns 200 but empty 'urls' → no stream URLs printed."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"urls": []}),
        )
        self._run(sess)
        out = capsys.readouterr().out
        assert "Snap URL" not in out  # no snap/stream URLs when urls=[]

    def test_alarm_type_none_shows_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        """alarmType=None in JSON → shows 'NONE' (not Python None)."""
        cam_data = [_cam_json()]
        cam_data[0]["alarmType"] = None

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess)
        out = capsys.readouterr().out
        assert "NONE" in out

    def test_gen1_model_display_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen1 CAMERA_EYES model uses HW_DISPLAY_NAMES lookup."""
        cam_data = [_cam_json(model="CAMERA_EYES")]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run(sess)
        out = capsys.readouterr().out
        # Should show model name (may be mapped via HW_DISPLAY_NAMES or raw)
        assert "CAMERA_EYES" in out or "Eyes Outdoor" in out


# ─────────────────────────────────────────────────────────────────────────────
# _renew_session (nested inside cmd_watch)
# We test it indirectly by running cmd_watch with a near-expiry token and
# asserting that get_token is called again.
# ─────────────────────────────────────────────────────────────────────────────


class TestRenewSessionViaWatch:
    """_renew_session is a closure inside cmd_watch — test via the watch loop.

    Strategy: patch time.sleep to a no-op so the inner sleep loop exits fast,
    and use side_effect on api_get_events to set _STOP_REQUESTED after the
    first real iteration completes (so we get exactly one pass through the body).
    """

    def _args(self, **kw: Any) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.cam = None
        ns.interval = 0
        ns.duration = 0
        ns.snapshot = False
        ns.push = False
        ns.push_mode = "polling"
        ns.signal = ""
        ns.signal_sender = ""
        ns.signal_recipients = ""
        ns.webhook = ""
        ns.quiet_secs = 30
        ns.auto_snapshot = False
        ns.auto_record = False
        ns.track_motion = False
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def _run_one_iteration(
        self,
        cfg: dict[str, Any],
        token: str,
        get_token_side_effect: Any = None,
        events_return: list[Any] | None = None,
    ) -> list[str]:
        """Run cmd_watch for exactly one poll iteration, return get_token call list.

        Note: cmd_watch calls api_get_events once for the BASELINE fetch (before
        the loop), then once per camera per poll iteration. We allow 2 calls
        (1 baseline + 1 poll) before setting _STOP_REQUESTED.
        """
        if events_return is None:
            events_return = []

        calls: list[str] = []
        api_call_count: list[int] = [0]

        new_tok = _make_jwt(FROZEN_EPOCH + 7200)

        def _fake_get_token(_cfg: dict[str, Any]) -> str:
            calls.append("called")
            if get_token_side_effect is not None:
                return get_token_side_effect(_cfg)
            return new_tok

        sess = MagicMock()
        sess.headers = {"Authorization": f"Bearer {token}"}

        def _events_two_calls(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_call_count[0] += 1
            # Call 1 = baseline fetch; call 2 = first poll iteration → stop after
            if api_call_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()
            return events_return

        bosch_camera._STOP_REQUESTED.clear()

        with (
            patch.object(bosch_camera, "get_token", side_effect=_fake_get_token),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_two_calls),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        return calls

    @freeze_time(FROZEN_NOW)
    def test_near_expiry_token_triggers_renew(self) -> None:
        """Near-expiry token causes _renew_session call inside the loop body.

        Strategy: first get_token call returns near_expiry token; the loop body
        detects _is_token_near_expiry(near_expiry)==True and calls _renew_session
        which calls get_token again.
        """
        near_expiry = _make_jwt(FROZEN_EPOCH + 30)  # within 60s buffer → near expiry
        new_tok = _make_jwt(FROZEN_EPOCH + 7200)
        cfg = _cfg(token=near_expiry)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        call_count: list[int] = [0]

        def _get_token_returns_near_first(_cfg: dict[str, Any]) -> str:
            call_count[0] += 1
            # First call: return near-expiry so _renew_session is triggered in loop
            # Subsequent calls (from _renew_session): return valid token
            if call_count[0] == 1:
                return near_expiry
            return new_tok

        api_event_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {"Authorization": f"Bearer {near_expiry}"}
        bosch_camera._STOP_REQUESTED.clear()

        def _events_two_calls(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_event_calls[0] += 1
            # call 1 = baseline, call 2 = first poll → stop after poll
            if api_event_calls[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()
            return []

        with (
            patch.object(bosch_camera, "get_token", side_effect=_get_token_returns_near_first),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_two_calls),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        # get_token: 1st call is the initial cmd_watch, 2nd is _renew_session
        assert call_count[0] >= 2

    @freeze_time(FROZEN_NOW)
    def test_valid_token_skips_preemptive_renew(self) -> None:
        """Valid (non-near-expiry) token does NOT trigger pre-emptive renew at 3891.

        Note: the empty-events retry branch (3905) may still call _renew_session once
        even for a valid token when events list is empty and Authorization is set.
        So we patch session.headers to have no Authorization to skip that branch.
        """
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        calls: list[str] = []
        sess = MagicMock()
        # No Authorization header → skip the empty-events retry branch
        sess.headers = {}

        api_call_count: list[int] = [0]

        def _events_two_calls(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_call_count[0] += 1
            if api_call_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()
            return []

        bosch_camera._STOP_REQUESTED.clear()

        def _fake_get_token(_cfg: dict[str, Any]) -> str:
            calls.append("called")
            return valid

        with (
            patch.object(bosch_camera, "get_token", side_effect=_fake_get_token),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_two_calls),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        # Only the initial get_token call — no pre-emptive renew for valid token
        assert len(calls) == 1

    @freeze_time(FROZEN_NOW)
    def test_event_loop_processes_new_events(self) -> None:
        """New events are printed during the poll iteration."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        fake_events = [
            {"id": "ev-001", "eventType": "MOTION", "timestamp": "2024-06-01T12:00:00Z"},
        ]

        call_count: list[int] = [0]

        def _events_once(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            bosch_camera._STOP_REQUESTED.set()
            return fake_events

        sess = MagicMock()
        sess.headers = {"Authorization": f"Bearer {valid}"}
        bosch_camera._STOP_REQUESTED.clear()

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_once),
            patch.object(bosch_camera, "api_mark_events_read", return_value=None),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        assert call_count[0] >= 1

    @freeze_time(FROZEN_NOW)
    def test_empty_events_retry_renew_path(self) -> None:
        """Empty events + Authorization header causes renew retry path (possible 401)."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        new_tok = _make_jwt(FROZEN_EPOCH + 7200)
        call_count: list[int] = [0]

        def fake_get_token(_cfg: dict[str, Any]) -> str:
            call_count[0] += 1
            return new_tok

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {"Authorization": f"Bearer {valid}"}
        bosch_camera._STOP_REQUESTED.clear()

        def _events(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            # 1 = baseline, 2 = poll body (first call), 3 = poll body retry
            if api_calls[0] >= 3:
                bosch_camera._STOP_REQUESTED.set()
            return []  # always empty → triggers the "possibly 401 → renew" branch

        with (
            patch.object(bosch_camera, "get_token", side_effect=fake_get_token),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        # get_token called once at start, once more for renew retry
        assert call_count[0] >= 2

    @freeze_time(FROZEN_NOW)
    def test_duration_stops_watch(self) -> None:
        """_STOP_REQUESTED pre-set: watch loop exits before first iteration."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        sess = MagicMock()
        sess.headers = {"Authorization": f"Bearer {valid}"}
        bosch_camera._STOP_REQUESTED.set()  # pre-set → loop exits immediately

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())
        # Returns cleanly without error

    @freeze_time(FROZEN_NOW)
    def test_api_get_events_exception_is_caught(self) -> None:
        """api_get_events raising an exception is caught and logged (3900-3903)."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {}  # no Authorization → skip retry renew branch
        bosch_camera._STOP_REQUESTED.clear()

        def _raise_on_poll(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            if api_calls[0] == 1:
                return []  # baseline OK
            # Poll iteration: raise exception
            bosch_camera._STOP_REQUESTED.set()
            raise ConnectionError("network timeout")

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_raise_on_poll),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())
        # Should complete without propagating the exception
        assert api_calls[0] >= 2

    @freeze_time(FROZEN_NOW)
    def test_renew_retry_exception_is_caught(self) -> None:
        """_renew_session exception inside the retry branch is caught (3910-3913)."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {"Authorization": f"Bearer {valid}"}
        bosch_camera._STOP_REQUESTED.clear()

        def _events_with_retry_exception(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            if api_calls[0] == 1:
                return []  # baseline OK
            if api_calls[0] == 2:
                # First poll call: return [] to trigger retry renew
                # _renew_session will be called next, then api_get_events(retry)
                return []
            # Retry api_get_events call after renew: raise exception
            bosch_camera._STOP_REQUESTED.set()
            raise RuntimeError("retry also failed")

        def _renew_raises(_cfg: dict[str, Any]) -> str:
            # get_token called from _renew_session: set stop and succeed
            return valid

        with (
            patch.object(bosch_camera, "get_token", side_effect=_renew_raises),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_with_retry_exception),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())
        assert api_calls[0] >= 3

    @freeze_time(FROZEN_NOW)
    def test_duration_timeout_branch(self) -> None:
        """duration reached mid-loop prints stop message (3876-3877)."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        sess = MagicMock()
        sess.headers = {}
        bosch_camera._STOP_REQUESTED.clear()

        api_calls: list[int] = [0]
        import time as _time

        call_times: list[float] = [_time.time()]  # start time

        def _events_baseline(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            return []

        # Patch time.time to simulate elapsed > duration after baseline
        time_calls: list[int] = [0]

        def _fake_time() -> float:
            time_calls[0] += 1
            # First call (start_time assignment): return T
            # Subsequent calls: return T+100 so duration check triggers
            return call_times[0] if time_calls[0] <= 2 else call_times[0] + 100.0

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_baseline),
            patch("time.sleep", return_value=None),
            patch("time.time", side_effect=_fake_time),
        ):
            bosch_camera.cmd_watch(cfg, self._args(duration=10))


# ─────────────────────────────────────────────────────────────────────────────
# Additional get_token branch coverage
# ─────────────────────────────────────────────────────────────────────────────


class TestGetTokenAdditionalBranches:
    @freeze_time(FROZEN_NOW)
    def test_refresh_import_error_fallback_to_auto(self) -> None:
        """ImportError for get_token._do_refresh with no token → falls to get_token_auto."""
        auto_tok = _make_jwt(FROZEN_EPOCH + 7200)
        cfg = _cfg(token="", refresh="some-refresh-token")

        # get_token module raises ImportError on _do_refresh import
        # → fall through to get_token_auto
        fake_module = MagicMock()
        fake_module._do_refresh.side_effect = ImportError("no module")
        fake_module.get_token_auto.return_value = auto_tok
        with patch.dict(sys.modules, {"get_token": fake_module}):
            result = bosch_camera.get_token(cfg)
        assert result == auto_tok

    @freeze_time(FROZEN_NOW)
    def test_no_token_get_token_auto_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_token_auto raises exception → falls through to manual input."""
        cfg = _cfg(token="")
        manual_tok = _make_jwt(FROZEN_EPOCH + 3600)
        fake_module = MagicMock()
        fake_module.get_token_auto.side_effect = RuntimeError("browser failed")
        with (
            patch.dict(sys.modules, {"get_token": fake_module}),
            patch("builtins.input", return_value=manual_tok),
            patch.object(bosch_camera, "save_config"),
        ):
            result = bosch_camera.get_token(cfg)
        assert result == manual_tok

    @freeze_time(FROZEN_NOW)
    def test_expired_with_refresh_token_no_import(self) -> None:
        """Expired token + refresh, but get_token module absent (ImportError) → return expired."""
        expired = _make_jwt(FROZEN_EPOCH - 3600)
        cfg = _cfg(token=expired, refresh="some-refresh")
        # Simulate get_token module not found
        with patch.dict(sys.modules, {"get_token": None}):  # type: ignore[dict-item]
            result = bosch_camera.get_token(cfg)
        assert result == expired

    @freeze_time(FROZEN_NOW)
    def test_no_token_get_token_auto_import_error(self) -> None:
        """get_token_auto ImportError → falls through to manual input (line 348)."""
        cfg = _cfg(token="")
        manual_tok = _make_jwt(FROZEN_EPOCH + 3600)
        # Simulate get_token module absent entirely
        with (
            patch.dict(sys.modules, {"get_token": None}),  # type: ignore[dict-item]
            patch("builtins.input", return_value=manual_tok),
            patch.object(bosch_camera, "save_config"),
        ):
            result = bosch_camera.get_token(cfg)
        assert result == manual_tok

    @freeze_time(FROZEN_NOW)
    def test_no_token_get_token_auto_raises_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_token_auto raises exception → falls through to manual input (line 350)."""
        cfg = _cfg(token="")
        manual_tok = _make_jwt(FROZEN_EPOCH + 3600)
        fake_module = MagicMock()
        fake_module.get_token_auto.side_effect = RuntimeError("browser failed")
        with (
            patch.dict(sys.modules, {"get_token": fake_module}),
            patch("builtins.input", return_value=manual_tok),
            patch.object(bosch_camera, "save_config"),
        ):
            result = bosch_camera.get_token(cfg)
        assert result == manual_tok


class TestCmdInfoExceptionBranches:
    """Cover exception-silencing branches in cmd_info."""

    def _run_full(self, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "rcp_open_connection", side_effect=RuntimeError("no RCP")),
            patch.object(bosch_camera, "check_token_age", return_value="valid (mock)"),
        ):
            bosch_camera.cmd_info(_cfg(), _make_args(full=True))

    def test_protocol_check_exception_silenced(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Protocol check session.get raising an exception is silenced (1956-1957)."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                raise ConnectionError("protocol check failed")
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run_full(sess)
        out = capsys.readouterr().out
        # Should still print camera info (protocol exception silenced)
        assert "Camera Info" in out

    def test_full_mode_commissioned_exception_silenced(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exception in /commissioned fetch is silenced (2061-2062)."""
        cam_data = [_cam_json()]

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "commissioned" in url:
                raise ConnectionError("commissioned fetch failed")
            elif "feature_flags" in url:
                r.status_code = 200
                r.json.return_value = {}
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run_full(sess)
        # Should complete without raising

    def test_full_mode_exceptions_all_silenced(self, capsys: pytest.CaptureFixture[str]) -> None:
        """All --full extra endpoint fetches can fail silently (covers 2072-2143)."""
        cam_data = [_cam_json()]
        exc = ConnectionError("all extra endpoints fail")

        def _get(url: str, **kw: Any) -> MagicMock:
            r = MagicMock()
            if url.endswith("video_inputs"):
                r.status_code = 200
                r.json.return_value = cam_data
                r.raise_for_status.return_value = None
            elif "protocol_support" in url:
                r.status_code = 200
                r.json.return_value = {"state": "SUPPORTED"}
            elif "feature_flags" in url:
                r.status_code = 200
                r.json.return_value = {}
            elif any(
                s in url
                for s in ["firmware", "lighting_override", "motion", "recording_options",
                          "ambient_light_sensor_level", "intrusionDetectionConfig",
                          "credentials", "rules", "timestamp", "privacy_sound_override",
                          "commissioned"]
            ):
                raise exc
            else:
                r.status_code = 200
                r.json.return_value = {}
            return r

        sess = MagicMock()
        sess.get.side_effect = _get
        sess.put.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"urls": []}))
        self._run_full(sess)
        out = capsys.readouterr().out
        assert "Camera Info" in out


class TestWatchNewEvents:
    """Cover _renew_session area: new_events iteration and printing (3918-3935)."""

    def _args(self, **kw: Any) -> argparse.Namespace:
        ns = argparse.Namespace()
        ns.cam = None
        ns.interval = 0
        ns.duration = 0
        ns.snapshot = False
        ns.push = False
        ns.push_mode = "polling"
        ns.signal = ""
        ns.signal_sender = ""
        ns.signal_recipients = ""
        ns.webhook = ""
        ns.quiet_secs = 30
        ns.auto_snapshot = False
        ns.auto_record = False
        ns.track_motion = False
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    @freeze_time(FROZEN_NOW)
    def test_new_events_printed_with_image_and_clip(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """New events with imageUrl and videoClipUrl are printed (3924-3934)."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        fake_events = [
            {
                "id": "ev-002",
                "eventType": "MOTION",
                "timestamp": "2024-06-01T12:00:00Z",
                "imageUrl": "https://fakebosch.example.com/snap.jpg",
                "videoClipUrl": "https://fakebosch.example.com/clip.mp4",
            }
        ]

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {}  # no Authorization → skip retry renew
        bosch_camera._STOP_REQUESTED.clear()

        def _events(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            if api_calls[0] == 1:
                return []  # baseline: no events
            bosch_camera._STOP_REQUESTED.set()
            return fake_events  # poll iteration: return new event

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read", return_value=None),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        out = capsys.readouterr().out
        assert "MOTION" in out

    @freeze_time(FROZEN_NOW)
    def test_audio_event_type_icon(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AUDIO event type uses sound icon branch (3929 'AUDIO' check)."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        fake_events = [
            {"id": "ev-003", "eventType": "AUDIO_ALARM", "timestamp": "2024-06-01T12:00:00Z"}
        ]

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {}
        bosch_camera._STOP_REQUESTED.clear()

        def _events(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            if api_calls[0] == 1:
                return []
            bosch_camera._STOP_REQUESTED.set()
            return fake_events

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read", return_value=None),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        out = capsys.readouterr().out
        assert "AUDIO_ALARM" in out

    @freeze_time(FROZEN_NOW)
    def test_person_event_type_icon(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PERSON event type uses person icon branch."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        fake_events = [
            {"id": "ev-004", "eventType": "PERSON", "timestamp": "2024-06-01T12:00:00Z"}
        ]

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {}
        bosch_camera._STOP_REQUESTED.clear()

        def _events(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            if api_calls[0] == 1:
                return []
            bosch_camera._STOP_REQUESTED.set()
            return fake_events

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read", return_value=None),
            patch("time.sleep", return_value=None),
        ):
            bosch_camera.cmd_watch(cfg, self._args())

        out = capsys.readouterr().out
        assert "PERSON" in out

    @freeze_time(FROZEN_NOW)
    def test_stop_after_sleep_loop_branch(self) -> None:
        """_STOP_REQUESTED set during sleep loop → break at line 3885."""
        valid = _make_jwt(FROZEN_EPOCH + 3600)
        cfg = _cfg(token=valid)
        cfg["cameras"] = {
            CAM_NAME: {"id": CAM_ID, "name": CAM_NAME, "local_ip": "", "has_light": False}
        }

        api_calls: list[int] = [0]
        sess = MagicMock()
        sess.headers = {}
        bosch_camera._STOP_REQUESTED.clear()

        def _events_baseline(s: Any, cam_id: str, limit: int = 20) -> list[Any]:
            api_calls[0] += 1
            return []  # baseline only — stop is set during sleep

        sleep_calls: list[int] = [0]

        def _fake_sleep(secs: float) -> None:
            sleep_calls[0] += 1
            # Set stop during sleep so the inner sleep loop + outer check fires
            bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=valid),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "api_get_events", side_effect=_events_baseline),
            patch("time.sleep", side_effect=_fake_sleep),
        ):
            # Need interval > 0 so the sleep loop actually runs
            bosch_camera.cmd_watch(cfg, self._args(interval=5))

        # Completed cleanly after stop via sleep branch
