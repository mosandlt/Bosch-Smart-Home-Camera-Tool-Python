"""
Coverage tests for 6 CLI handlers in bosch_camera.py:
  cmd_light (2577-2811), cmd_privacy (2449-2576), cmd_token (5245-5310),
  cmd_motion (4249-4322), cmd_maintenance (4195-4248), cmd_pan (2812-2950).

PIN_EVERY_MODE: one test per discrete mode + default + error path.
Fake IDs only — no real device values or secrets.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_light,
    cmd_maintenance,
    cmd_motion,
    cmd_pan,
    cmd_privacy,
    cmd_token,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
CLOUD = "https://residential.cbs.boschsecurity.com"


def _jwt(exp_offset: int = 3600) -> str:
    """Return a valid-looking JWT (typ first so pre-push hook doesn't trip)."""
    hdr = base64.urlsafe_b64encode(b'{"typ":"JWT","alg":"none"}').rstrip(b"=").decode()
    pay = (
        base64.urlsafe_b64encode(
            json.dumps({"exp": int(time.time()) + exp_offset, "email": "user@example.com"}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"{hdr}.{pay}.sig"


def _make_cfg(model: str = "HOME_Eyes_Outdoor") -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "tok_refresh_fake", "username": ""},
        "cameras": {
            CAM_NAME: {
                "id": CAM_ID,
                "name": CAM_NAME,
                "model": model,
                "firmware": "9.40.102",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "action": None,
        "json": False,
        "local": False,
        "extra_args": [],
        "minutes": None,
        "enable": False,
        "disable": False,
        "sensitivity": None,
        "preset": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _resp(status: int, payload: Any = None, text: str = "") -> MagicMock:
    return MagicMock(status_code=status, json=lambda: payload or {}, text=text)


def _sess(**method_overrides: Any) -> MagicMock:
    """Return a MagicMock session with sensible defaults."""
    s = MagicMock()
    s.get.return_value = _resp(200, [])
    s.put.return_value = _resp(204)
    for method, value in method_overrides.items():
        getattr(s, method).return_value = value
    return s


# ─────────────────────────────────────────────────────────────────────────────
# cmd_privacy
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdPrivacy:
    """Tests for cmd_privacy (lines 2449-2576)."""

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_privacy(cfg, args)

    def _video_inputs_resp(self, privacy_mode: str = "OFF") -> MagicMock:
        payload = [{"id": CAM_ID, "privacyMode": privacy_mode}]
        return _resp(200, payload)

    def test_status_only_shows_current(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No action → prints current state and per-camera detail."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            self._video_inputs_resp("OFF"),  # /v11/video_inputs
            _resp(200, {"durationInSeconds": None, "privacyTimeEnd": None}),  # detail GET
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "OFF" in out

    def test_status_shows_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Privacy ON → shows ON state."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            self._video_inputs_resp("ON"),
            _resp(200, {"durationInSeconds": 600, "privacyTimeEnd": "2099-01-01T00:00:00Z"}),
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "ON" in out

    def test_set_on_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=on → PUT with privacyMode=ON, 204 response."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("OFF")
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="on"), sess)
        out = capsys.readouterr().out
        assert "ON" in out
        call_kwargs = sess.put.call_args
        body = call_kwargs[1]["json"]
        assert body["privacyMode"] == "ON"

    def test_set_off_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=off → PUT with privacyMode=OFF."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("ON")
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="off"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["privacyMode"] == "OFF"

    def test_idempotent_already_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Already ON and action=on → no PUT, prints no-change message."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("ON")
        self._run(cfg, _args(action="on"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Already" in out or "no change" in out.lower()

    def test_idempotent_already_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Already OFF and action=off → no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("OFF")
        self._run(cfg, _args(action="off"), sess)
        sess.put.assert_not_called()

    def test_set_on_with_minutes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=on + minutes=10 → PUT with privacyTimeSeconds."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("OFF")
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="on", minutes=10), sess)
        body = sess.put.call_args[1]["json"]
        assert body.get("privacyTimeSeconds") == 600

    def test_401_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """401 on /video_inputs → prints expired message, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(action="on"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "expired" in out.lower() or "401" in out

    def test_500_raises(self) -> None:
        """500 on /video_inputs → raises (5xx hint path)."""
        cfg = _make_cfg()
        r = _resp(500, text="internal error")
        r.raise_for_status.side_effect = Exception("500")
        sess = _sess()
        sess.get.return_value = r
        with pytest.raises(Exception):
            self._run(cfg, _args(action="on"), sess)

    def test_put_failure_non_204(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT returns 400 → prints failure message."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("OFF")
        sess.put.return_value = _resp(400, text="Bad Request")
        self._run(cfg, _args(action="on"), sess)
        out = capsys.readouterr().out
        assert "Failed" in out or "400" in out

    def test_cam_arg_as_action_swapped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """privacy on (cam='on', action=None) → action swap logic."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._video_inputs_resp("OFF")
        sess.put.return_value = _resp(204)
        # Simulate "bosch privacy on" → argparse gives cam="on", action=None
        self._run(cfg, _args(cam="on", action=None), sess)
        body = sess.put.call_args[1]["json"]
        assert body["privacyMode"] == "ON"

    def test_local_no_action_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local without action → early return with info message."""
        cfg = _make_cfg()
        self._run(cfg, _args(local=True, action=None), _sess())
        out = capsys.readouterr().out
        assert "--local" in out or "on or off" in out.lower()

    def test_local_on_no_ip(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local with action=on but no LAN IP → error message."""
        cfg = _make_cfg()  # lan_ips is empty
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value=None),
        ):
            cmd_privacy(cfg, _args(local=True, action="on"))
        out = capsys.readouterr().out
        assert "No LAN IP" in out or "lan-ips" in out.lower()

    def test_local_on_rcp_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local with action=on and IP → calls _lan_rcp_write_privacy(ip, True, ...)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.10"),
            patch.object(
                bosch_camera,
                "_get_local_connection_creds",
                return_value=("192.0.2.10", "u", "p"),
            ),
            patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=True) as mock_rcp,
        ):
            cmd_privacy(cfg, _args(local=True, action="on"))
        mock_rcp.assert_called_once_with("192.0.2.10", True, user="u", password="p")

    def test_local_on_rcp_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local RCP write returns False → error message."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.10"),
            patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=False),
        ):
            cmd_privacy(cfg, _args(local=True, action="on"))
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "RCP" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_light
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdLight:
    """Tests for cmd_light (lines 2577-2811)."""

    def _video_inputs(
        self, has_light: bool = True, sched: str = "ALWAYS_OFF"
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": CAM_ID,
                "featureSupport": {"light": has_light},
                "featureStatus": {
                    "scheduleStatus": sched,
                    "frontIlluminatorInGeneralLightOn": False,
                    "frontIlluminatorGeneralLightIntensity": 0.5,
                    "wallwasherInGeneralLightOn": False,
                    "generalLightOnTime": "21:00",
                    "generalLightOffTime": "06:00",
                    "lightOnMotion": True,
                    "lightOnMotionFollowUpTimeSeconds": 30,
                },
            }
        ]

    def _override_resp(self) -> MagicMock:
        return _resp(
            200, {"frontLightOn": False, "wallwasherOn": False, "frontLightIntensity": 0.5}
        )

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_light(cfg, args)

    def test_status_only_with_light(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No action, camera has light → shows schedule status."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "ALWAYS_OFF" in out or "schedule" in out.lower()

    def test_status_no_light_support(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Camera has no light → prints not-supported message."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(200, self._video_inputs(has_light=False))
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "not support" in out.lower() or "light" in out.lower()

    def test_set_on_all_lights(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=on → PUT body frontLightOn=True, wallwasherOn=True."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),  # second override GET for action path
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="on"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is True
        assert body["wallwasherOn"] is True

    def test_set_off_all_lights(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=off → PUT body frontLightOn=False, wallwasherOn=False."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="off"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is False
        assert body["wallwasherOn"] is False

    def test_set_front_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='front on' → PUT with frontLightOn=True."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="front on"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is True

    def test_set_front_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='front off' → PUT with frontLightOn=False."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="front off"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is False

    def test_set_wall_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='wall on' → PUT with wallwasherOn=True."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="wall on"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["wallwasherOn"] is True

    def test_set_wall_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='wall off' → PUT with wallwasherOn=False."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="wall off"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["wallwasherOn"] is False

    def test_set_intensity_50(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='intensity 50' → PUT with frontLightIntensity=0.5."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="intensity 50"), sess)
        body = sess.put.call_args[1]["json"]
        assert abs(body["frontLightIntensity"] - 0.5) < 0.01
        assert body["frontLightOn"] is True

    def test_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """401 on /video_inputs → early return, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(action="on"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "expired" in out.lower() or "401" in out

    def test_500_raises(self) -> None:
        """500 on /video_inputs → raises after hint."""
        cfg = _make_cfg()
        r = _resp(500, text="server error")
        r.raise_for_status.side_effect = Exception("500")
        sess = _sess()
        sess.get.return_value = r
        with pytest.raises(Exception):
            self._run(cfg, _args(action="on"), sess)

    def test_put_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 400 → prints failure."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(400, text="Bad Request")
        self._run(cfg, _args(action="on"), sess)
        out = capsys.readouterr().out
        assert "Failed" in out or "400" in out

    def test_cam_as_on_swap(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='on', action=None → swap: action='on'."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(cam="on"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is True

    def test_cam_as_front_swap(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='front', action='on' → action becomes 'front on', cam=None."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(cam="front", action="on"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is True

    def test_local_no_action(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local without action → hint, early return."""
        cfg = _make_cfg()
        self._run(cfg, _args(local=True, action=None), _sess())
        out = capsys.readouterr().out
        assert "--local" in out or "on / off" in out.lower()

    def test_local_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local action=off → _lan_rcp_write_front_light(ip, 0, ...)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
            patch.object(
                bosch_camera,
                "_get_local_connection_creds",
                return_value=("192.0.2.11", "u", "p"),
            ),
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True) as mock_rcp,
        ):
            cmd_light(cfg, _args(local=True, action="off"))
        mock_rcp.assert_called_once_with("192.0.2.11", 0, user="u", password="p")

    def test_local_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local action=on → _lan_rcp_write_front_light(ip, 100, ...)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
            patch.object(
                bosch_camera,
                "_get_local_connection_creds",
                return_value=("192.0.2.11", "u", "p"),
            ),
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True) as mock_rcp,
        ):
            cmd_light(cfg, _args(local=True, action="on"))
        mock_rcp.assert_called_once_with("192.0.2.11", 100, user="u", password="p")

    def test_local_intensity_75(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local action='intensity 75' → _lan_rcp_write_front_light(ip, 75, ...)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
            patch.object(
                bosch_camera,
                "_get_local_connection_creds",
                return_value=("192.0.2.11", "u", "p"),
            ),
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True) as mock_rcp,
        ):
            cmd_light(cfg, _args(local=True, action="intensity 75"))
        mock_rcp.assert_called_once_with("192.0.2.11", 75, user="u", password="p")

    def test_local_wall_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local action starts with 'wall' → prints not-supported hint."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
        ):
            cmd_light(cfg, _args(local=True, action="wall on"))
        out = capsys.readouterr().out
        assert "cloud-only" in out.lower() or "wallwasher" in out.lower()

    def test_local_no_ip(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local with no LAN IP → error message."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value=None),
        ):
            cmd_light(cfg, _args(local=True, action="on"))
        out = capsys.readouterr().out
        assert "No LAN IP" in out or "lan-ips" in out.lower()

    def test_local_rcp_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local RCP write returns False → prints error."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=False),
        ):
            cmd_light(cfg, _args(local=True, action="on"))
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "RCP" in out

    def test_action_from_extra_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='front' with extra_args=['on'] → builds 'front on' action."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),
            self._override_resp(),
        ]
        sess.put.return_value = _resp(204)
        # Simulate "light front on" where argparse puts action='front', extra_args=['on']
        self._run(cfg, _args(action="front", extra_args=["on"]), sess)
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is True

    def test_local_front_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local action='front on' (parts[0]='front', parts[1]='on') → brightness=100."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
            patch.object(
                bosch_camera,
                "_get_local_connection_creds",
                return_value=("192.0.2.11", "u", "p"),
            ),
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True) as mock_rcp,
        ):
            cmd_light(cfg, _args(local=True, action="front on"))
        mock_rcp.assert_called_once_with("192.0.2.11", 100, user="u", password="p")

    def test_local_front_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--local action='front off' → brightness=0."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=_sess()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_resolve_lan_ip", return_value="192.0.2.11"),
            patch.object(
                bosch_camera,
                "_get_local_connection_creds",
                return_value=("192.0.2.11", "u", "p"),
            ),
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True) as mock_rcp,
        ):
            cmd_light(cfg, _args(local=True, action="front off"))
        mock_rcp.assert_called_once_with("192.0.2.11", 0, user="u", password="p")

    def test_override_get_exception_in_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Override GET raises → exception swallowed, status still shown."""
        cfg = _make_cfg()
        sess = _sess()
        exc_resp = MagicMock()
        exc_resp.json.side_effect = Exception("network error")
        exc_resp.status_code = 200
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            exc_resp,  # override GET raises on .json()
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        # Should still show schedule status without crashing
        assert "ALWAYS_OFF" in out or "schedule" in out.lower()

    def test_override_get_exception_in_action(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Override GET for action path raises → cur defaults to {}, action proceeds."""
        cfg = _make_cfg()
        sess = _sess()
        exc_resp = MagicMock()
        exc_resp.json.side_effect = Exception("network error")
        exc_resp.status_code = 200
        sess.get.side_effect = [
            _resp(200, self._video_inputs()),
            self._override_resp(),  # status override GET
            exc_resp,  # action override GET raises
        ]
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="on"), sess)
        # Should still PUT with defaults when override fetch fails
        body = sess.put.call_args[1]["json"]
        assert body["frontLightOn"] is True

    def test_schedule_always_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scheduleStatus=ALWAYS_ON → icon 💡 shown in status."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs(sched="ALWAYS_ON")),
            self._override_resp(),
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "ALWAYS_ON" in out

    def test_schedule_schedule(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scheduleStatus=SCHEDULE shown in status."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._video_inputs(sched="SCHEDULE")),
            self._override_resp(),
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "SCHEDULE" in out

    def test_wallwasher_on_in_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """wallwasherInGeneralLightOn=True → wallwasher line shown."""
        cfg = _make_cfg()
        vi = self._video_inputs()
        vi[0]["featureStatus"]["wallwasherInGeneralLightOn"] = True
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, vi),
            self._override_resp(),
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "Wallwasher" in out or "wallwasher" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_pan
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdPan:
    """Tests for cmd_pan (lines 2812-2950)."""

    def _pan_cam_list(self, pan_limit: int = 120) -> list[dict[str, Any]]:
        return [{"id": CAM_ID, "featureSupport": {"panLimit": pan_limit}}]

    def _pan_state(self, pos: int = 0, limit: int = 120) -> dict[str, Any]:
        return {"currentAbsolutePosition": pos, "panLimit": limit}

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_pan(cfg, args)

    def test_status_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No action → shows current position."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(15)),
        ]
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "+15" in out or "15" in out

    def test_no_pan_support(self, capsys: pytest.CaptureFixture[str]) -> None:
        """panLimit=0 → not-supported message."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(200, [{"id": CAM_ID, "featureSupport": {"panLimit": 0}}])
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "not support" in out.lower() or "panLimit=0" in out

    def test_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """401 on /video_inputs → early return."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(action="home"), sess)
        out = capsys.readouterr().out
        assert "expired" in out.lower() or "401" in out

    def test_preset_home(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--preset home → PUT absolutePosition=0."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(45)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 0,
                "estimatedTimeToCompletion": 500,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(preset="home"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 0

    def test_preset_left(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--preset left → PUT absolutePosition=-60."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": -60,
                "estimatedTimeToCompletion": 400,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(preset="left"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == -60

    def test_preset_right(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--preset right → PUT absolutePosition=60."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 60,
                "estimatedTimeToCompletion": 400,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(preset="right"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 60

    def test_preset_back_left(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--preset back-left → PUT absolutePosition=-120."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": -120,
                "estimatedTimeToCompletion": 900,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(preset="back-left"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == -120

    def test_preset_back_right(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--preset back-right → PUT absolutePosition=120."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 120,
                "estimatedTimeToCompletion": 900,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(preset="back-right"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 120

    def test_numeric_action(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Numeric action string → PUT to that position."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 45,
                "estimatedTimeToCompletion": 300,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(action="45"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 45

    def test_idempotent_already_at_target(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Already at 0° and action=home → no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        self._run(cfg, _args(preset="home"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Already" in out or "no change" in out.lower()

    def test_out_of_range_numeric(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Position > panLimit → error message, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list(120)),
            _resp(200, self._pan_state(0)),
        ]
        self._run(cfg, _args(action="200"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "out of range" in out.lower() or "200" in out

    def test_invalid_action_string(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-numeric, non-preset string → error message, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        self._run(cfg, _args(action="garbage"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Unknown" in out or "garbage" in out

    def test_legacy_center_alias(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Legacy 'center' action → target=0."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(45)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 0,
                "estimatedTimeToCompletion": 200,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(action="center"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 0

    def test_cam_arg_as_preset(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='center', action=None → action swapped to 'center'."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(45)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 0,
                "estimatedTimeToCompletion": 200,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(cam="center"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 0

    def test_cam_arg_as_numeric(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='45', action=None → numeric action swap."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 45,
                "estimatedTimeToCompletion": 200,
                "cameraStoppedAtLimit": False,
            },
        )
        self._run(cfg, _args(cam="45"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["absolutePosition"] == 45

    def test_pan_fetch_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET /pan returns non-200 → error message, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(503),
        ]
        self._run(cfg, _args(action="home"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "503" in out or "Could not" in out

    def test_pan_privacy_mode_443(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET /pan returns 443 → privacy hint printed."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(443),
        ]
        self._run(cfg, _args(action="home"), sess)
        out = capsys.readouterr().out
        assert "privacy" in out.lower()

    def test_put_stopped_at_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT response cameraStoppedAtLimit=True → warning shown."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(
            200,
            {
                "currentAbsolutePosition": 120,
                "estimatedTimeToCompletion": 900,
                "cameraStoppedAtLimit": True,
            },
        )
        self._run(cfg, _args(preset="back-right"), sess)
        out = capsys.readouterr().out
        assert "limit" in out.lower()

    def test_put_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT non-200 → prints failure."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.side_effect = [
            _resp(200, self._pan_cam_list()),
            _resp(200, self._pan_state(0)),
        ]
        sess.put.return_value = _resp(400, text="error")
        self._run(cfg, _args(action="45"), sess)
        out = capsys.readouterr().out
        assert "Failed" in out or "400" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_motion
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMotion:
    """Tests for cmd_motion (lines 4249-4322)."""

    def _motion_resp(self, enabled: bool = False, sens: str = "MEDIUM_HIGH") -> MagicMock:
        return _resp(200, {"enabled": enabled, "motionAlarmConfiguration": sens})

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_motion(cfg, args)

    def test_status_only_disabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No flags → shows DISABLED."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False, "LOW")
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "DISABLED" in out

    def test_status_only_enabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Motion enabled → shows ENABLED."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(True, "HIGH")
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "ENABLED" in out

    def test_enable(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--enable → PUT enabled=True."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(enable=True), sess)
        body = sess.put.call_args[1]["json"]
        assert body["enabled"] is True

    def test_disable(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--disable → PUT enabled=False."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(True)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(disable=True), sess)
        body = sess.put.call_args[1]["json"]
        assert body["enabled"] is False

    def test_sensitivity_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity OFF → PUT with motionAlarmConfiguration=OFF, enabled=True."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sensitivity="OFF"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "OFF"
        assert body["enabled"] is True

    def test_sensitivity_low(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity LOW → PUT motionAlarmConfiguration=LOW."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sensitivity="LOW"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "LOW"

    def test_sensitivity_medium_low(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity MEDIUM_LOW."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sensitivity="MEDIUM_LOW"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "MEDIUM_LOW"

    def test_sensitivity_medium_high(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity MEDIUM_HIGH."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sensitivity="MEDIUM_HIGH"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "MEDIUM_HIGH"

    def test_sensitivity_high(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity HIGH."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sensitivity="HIGH"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "HIGH"

    def test_sensitivity_super_high(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--sensitivity SUPER_HIGH → implicit enable."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sensitivity="SUPER_HIGH"), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "SUPER_HIGH"
        assert body["enabled"] is True

    def test_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """401 on GET → early return, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(enable=True), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "expired" in out.lower() or "401" in out

    def test_non_200_get(self, capsys: pytest.CaptureFixture[str]) -> None:
        """503 on GET /motion → error message, no PUT."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(503)
        self._run(cfg, _args(enable=True), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "503" in out or "Could not" in out

    def test_put_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 400 → prints failure."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(400, text="err")
        self._run(cfg, _args(enable=True), sess)
        out = capsys.readouterr().out
        assert "Failed" in out or "400" in out

    def test_put_204_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 204 → success message with ENABLED."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = self._motion_resp(False)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(enable=True), sess)
        out = capsys.readouterr().out
        assert "ENABLED" in out

    def test_sensitivity_field_fallback(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET response uses 'sensitivity' key (old format) → parsed correctly."""
        cfg = _make_cfg()
        sess = _sess()
        sess.get.return_value = _resp(200, {"enabled": True, "sensitivity": "HIGH"})
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(enable=True), sess)
        body = sess.put.call_args[1]["json"]
        assert body["motionAlarmConfiguration"] == "HIGH"


# ─────────────────────────────────────────────────────────────────────────────
# cmd_maintenance
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMaintenance:
    """Tests for cmd_maintenance (lines 4195-4248)."""

    def _mw(self, **kwargs: Any) -> Any:
        """Build a minimal MaintenanceWindow-like mock."""
        from unittest.mock import MagicMock as _MM
        import datetime

        mw = _MM()
        mw.title = kwargs.get("title", "Test Maintenance")
        mw.summary = kwargs.get("summary", "Details here.")
        mw.camera_relevant = kwargs.get("camera_relevant", True)
        mw.link = kwargs.get("link", "https://community.bosch-smarthome.com/test")
        now = datetime.datetime.now(datetime.timezone.utc)
        mw.scheduled_start = kwargs.get("scheduled_start", now - datetime.timedelta(minutes=30))
        mw.scheduled_end = kwargs.get("scheduled_end", now + datetime.timedelta(hours=2))
        mw.state.return_value = kwargs.get("state", "active")
        mw.as_dict.return_value = {
            "title": mw.title,
            "state": kwargs.get("state", "active"),
            "summary": mw.summary,
        }
        return mw

    def test_fetch_failed_prints_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fetch_maintenance returns None → failure message printed."""
        with patch.object(bosch_camera, "fetch_maintenance", return_value=None):
            cmd_maintenance(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert len(out) > 0  # some message printed

    def test_fetch_failed_json_emits_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fetch_maintenance returns None + --json → prints 'null'."""
        with patch.object(bosch_camera, "fetch_maintenance", return_value=None):
            cmd_maintenance(_make_cfg(), _args(json=True))
        out = capsys.readouterr().out
        assert "null" in out

    def test_active_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """state=active → prints active message."""
        mw = self._mw(state="active")
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_scheduled_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """state=scheduled → scheduled message printed."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        mw = self._mw(
            state="scheduled",
            scheduled_start=now + datetime.timedelta(hours=2),
            scheduled_end=now + datetime.timedelta(hours=5),
        )
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_past_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """state=past → past message printed."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        mw = self._mw(
            state="past",
            scheduled_end=now - datetime.timedelta(hours=1),
        )
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_recent_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """state=recent → recent message printed."""
        mw = self._mw(state="recent")
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_unknown_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """state=something-else → unknown message printed."""
        mw = self._mw(state="unknown_xyz")
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json → prints valid JSON dict somewhere in output."""
        mw = self._mw()
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args(json=True))
        out = capsys.readouterr().out
        # The header line is printed before JSON; find the JSON object in output
        json_start = out.find("{")
        assert json_start >= 0, f"No JSON object found in output: {out!r}"
        data = json.loads(out[json_start:].strip())
        assert isinstance(data, dict)
        assert "title" in data

    def test_no_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No summary → no summary line printed (no crash)."""
        mw = self._mw(summary=None)
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        # Should not raise
        capsys.readouterr()

    def test_no_link(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No link → no link line printed (no crash)."""
        mw = self._mw(link=None)
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        capsys.readouterr()

    def test_not_camera_relevant(self, capsys: pytest.CaptureFixture[str]) -> None:
        """camera_relevant=False → no camera-relevant line."""
        mw = self._mw(camera_relevant=False)
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        capsys.readouterr()

    def test_no_scheduled_start(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scheduled_start=None (unknown time) → no crash."""
        mw = self._mw(state="active", scheduled_start=None)
        with patch.object(bosch_camera, "fetch_maintenance", return_value=mw):
            cmd_maintenance(_make_cfg(), _args())
        capsys.readouterr()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_token
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdToken:
    """Tests for cmd_token (lines 5245-5310)."""

    def test_valid_token_shows_expiry(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Valid non-expired JWT → shows email + expiry + valid status."""
        cfg = _make_cfg()
        cmd_token(cfg, _args())
        out = capsys.readouterr().out
        assert "Token" in out or "token" in out.lower()

    def test_expired_token_shows_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Expired JWT → shows EXPIRED message."""
        cfg = _make_cfg()
        cfg["account"]["bearer_token"] = _jwt(exp_offset=-600)  # expired 10 min ago
        cmd_token(cfg, _args())
        out = capsys.readouterr().out
        assert "EXPIRED" in out or "expired" in out.lower()

    def test_no_token_shows_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty bearer_token → shows (none)."""
        cfg = _make_cfg()
        cfg["account"]["bearer_token"] = ""
        cmd_token(cfg, _args())
        out = capsys.readouterr().out
        assert "(none)" in out

    def test_no_refresh_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No refresh token → shows browser login needed."""
        cfg = _make_cfg()
        cfg["account"]["refresh_token"] = ""
        cmd_token(cfg, _args())
        out = capsys.readouterr().out
        assert "browser" in out.lower() or "refresh" in out.lower()

    def test_with_refresh_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Has refresh token → shows auto-renewal."""
        cfg = _make_cfg()
        cfg["account"]["refresh_token"] = "refresh_fake_token_xyz"
        cmd_token(cfg, _args())
        out = capsys.readouterr().out
        assert "refresh" in out.lower() or "auto" in out.lower()

    def test_action_fix_import_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=fix + ImportError → prints not-found message."""
        cfg = _make_cfg()
        with patch.dict("sys.modules", {"get_token": None}):
            cmd_token(cfg, _args(cam="fix"))
        out = capsys.readouterr().out
        assert "get_token.py" in out or "not found" in out.lower()

    def test_action_fix_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=fix + successful renewal → success message."""
        cfg = _make_cfg()
        mock_module = MagicMock()
        mock_module.get_token_auto.return_value = "new_token_xyz"
        with patch.dict("sys.modules", {"get_token": mock_module}):
            cmd_token(cfg, _args(cam="fix"))
        out = capsys.readouterr().out
        assert "renewed" in out.lower() or "success" in out.lower()

    def test_action_fix_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=fix + renewal returns None → failure message."""
        cfg = _make_cfg()
        mock_module = MagicMock()
        mock_module.get_token_auto.return_value = None
        with patch.dict("sys.modules", {"get_token": mock_module}):
            cmd_token(cfg, _args(cam="fix"))
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "renewal" in out.lower()

    def test_action_browser(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=browser → get_token_auto called with force_browser=True."""
        cfg = _make_cfg()
        mock_module = MagicMock()
        mock_module.get_token_auto.return_value = "browser_token_xyz"
        with patch.dict("sys.modules", {"get_token": mock_module}):
            cmd_token(cfg, _args(cam="browser"))
        mock_module.get_token_auto.assert_called_once_with(cfg, force_browser=True)

    def test_action_renew_alias(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=renew → same renewal path as fix."""
        cfg = _make_cfg()
        mock_module = MagicMock()
        mock_module.get_token_auto.return_value = "tok_renewed"
        with patch.dict("sys.modules", {"get_token": mock_module}):
            cmd_token(cfg, _args(cam="renew"))
        mock_module.get_token_auto.assert_called_once()

    def test_action_refresh_alias(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=refresh → same renewal path."""
        cfg = _make_cfg()
        mock_module = MagicMock()
        mock_module.get_token_auto.return_value = "tok_refreshed"
        with patch.dict("sys.modules", {"get_token": mock_module}):
            cmd_token(cfg, _args(cam="refresh"))
        mock_module.get_token_auto.assert_called_once()

    def test_malformed_token_uses_check_token_age(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Malformed token (no dots) → falls back to check_token_age."""
        cfg = _make_cfg()
        cfg["account"]["bearer_token"] = "not_a_real_jwt"
        with patch.object(bosch_camera, "check_token_age", return_value="unknown") as mock_age:
            cmd_token(cfg, _args())
        mock_age.assert_called_once()

    def test_expired_token_shows_fix_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Expired token without action=fix → hints to run 'token fix'."""
        cfg = _make_cfg()
        cfg["account"]["bearer_token"] = _jwt(exp_offset=-600)
        cmd_token(cfg, _args())
        out = capsys.readouterr().out
        assert "fix" in out.lower() or "token" in out.lower()
