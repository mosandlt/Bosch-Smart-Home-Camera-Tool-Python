"""
Tests for 4 CLI handlers not previously covered:
  cmd_privacy_sound, cmd_privacy_masks, cmd_zones, cmd_lighting_schedule

PIN_EVERY_MODE: one test per discrete mode/subcommand/flag + default + error path.

Source: bosch_camera.py lines 5822-5901 (privacy_sound),
        6394-6492 (zones), 6495-6588 (privacy_masks), 6591-6674 (lighting_schedule).
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_lighting_schedule,
    cmd_privacy_masks,
    cmd_privacy_sound,
    cmd_zones,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / shared helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
CLOUD = "https://residential.cbs.boschsecurity.com"


def _jwt() -> str:
    import base64
    import time
    import json as _j

    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = (
        base64.urlsafe_b64encode(_j.dumps({"exp": int(time.time()) + 3600}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{hdr}.{pay}.sig"


def _make_cfg() -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME: {
                "id": CAM_ID,
                "name": CAM_NAME,
                "model": "HOME_Eyes_Outdoor",
                "firmware": "9.40.102",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "sub": None,
        "action": None,
        "json": None,
        "on": None,
        "off": None,
        "motion": None,
        "threshold": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _ok(payload: Any = None) -> MagicMock:
    """HTTP 200 response with optional JSON payload."""
    return MagicMock(status_code=200, json=lambda: payload or {}, text="")


def _resp(status: int, payload: Any = None, text: str = "") -> MagicMock:
    return MagicMock(status_code=status, json=lambda: payload or {}, text=text)


# ─────────────────────────────────────────────────────────────────────────────
# cmd_privacy_sound
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdPrivacySound:
    """Tests for privacy sound GET + SET."""

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_privacy_sound(cfg, args)

    def test_get_enabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns result=true → shows ENABLED."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": True})
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "ENABLED" in out

    def test_get_disabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns result=false → shows DISABLED."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": False})
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "DISABLED" in out

    def test_set_on_sends_put_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=on and current=False → PUT with result=True."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": False})
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(action="on"), sess)
        sess.put.assert_called_once()
        put_kwargs = sess.put.call_args
        body = put_kwargs[1]["json"]
        assert body == {"result": True}
        assert str(CAM_ID) in put_kwargs[0][0]

    def test_set_off_sends_put_false(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=off and current=True → PUT with result=False."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": True})
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(action="off"), sess)
        body = sess.put.call_args[1]["json"]
        assert body == {"result": False}

    def test_set_already_same_state_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action=on and current=True → no PUT, already-enabled message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": True})
        self._run(cfg, _args(action="on"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Already" in out or "no change" in out.lower()

    def test_cam_arg_on_shorthand(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='on' with no action → treated as action=on (shorthand)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": False})
        sess.put.return_value = _resp(200)
        # cam set to 'on' with action=None triggers the shorthand branch
        self._run(cfg, _args(cam="on", action=None), sess)
        body = sess.put.call_args[1]["json"]
        assert body == {"result": True}

    def test_http_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 401 → early return, PUT never called."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(action="on"), sess)
        sess.put.assert_not_called()

    def test_http_442_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 442 → not-supported warning, no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(442)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "442" in out
        sess.put.assert_not_called()

    def test_http_444_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 444 → offline warning, continues."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(444, text="")
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "444" in out or "offline" in out.lower()

    def test_http_put_444_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET ok, PUT returns 444 → offline warning printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": False})
        sess.put.return_value = _resp(444, text="camera offline")
        self._run(cfg, _args(action="on"), sess)
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_http_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 500 → error message, no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500, text="server error")
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "500" in out
        sess.put.assert_not_called()

    def test_url_contains_cam_id(self) -> None:
        """GET is called on the correct endpoint with cam_id."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"result": False})
        self._run(cfg, _args(), sess)
        called_url = sess.get.call_args[0][0]
        assert CAM_ID in called_url
        assert "privacy_sound_override" in called_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_zones
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdZones:
    """Tests for motion-detection zones: list / set / clear."""

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_zones(cfg, args)

    _sample_zone = {"x": 0.0, "y": 0.3, "w": 0.67, "h": 0.7}

    def test_list_zones_shows_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns one zone → count and coords printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok([self._sample_zone])
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "zone" in out.lower()

    def test_list_empty_zones(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns [] → 'no motion zones' message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok([])
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "no motion zones" in out.lower()

    def test_set_valid_json_sends_post(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with valid --json → POST with parsed array."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(200)
        zones_json = json.dumps([self._sample_zone])
        self._run(cfg, _args(sub="set", json=zones_json), sess)
        sess.post.assert_called_once()
        post_body = sess.post.call_args[1]["json"]
        assert isinstance(post_body, list)
        assert post_body[0]["x"] == 0.0

    def test_set_missing_json_no_post(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with no --json → error message, no POST."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(sub="set", json=None), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "--json" in out

    def test_set_invalid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with malformed JSON → error printed, no POST."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(sub="set", json="not-valid-json{"), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "Invalid JSON" in out or "invalid" in out.lower()

    def test_set_json_not_array(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with JSON object (not array) → error, no POST."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(sub="set", json='{"x": 0.0}'), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "array" in out.lower() or "must be" in out.lower()

    def test_clear_sends_post_empty_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=clear → POST with empty list."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(204)
        self._run(cfg, _args(sub="clear"), sess)
        sess.post.assert_called_once()
        assert sess.post.call_args[1]["json"] == []

    def test_clear_success_204(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=clear, POST 204 → cleared message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(204)
        self._run(cfg, _args(sub="clear"), sess)
        out = capsys.readouterr().out
        assert "cleared" in out.lower() or "clear" in out.lower()

    def test_set_http_443_privacy_active(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set, POST returns 443 → privacy-mode warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(443)
        zones_json = json.dumps([self._sample_zone])
        self._run(cfg, _args(sub="set", json=zones_json), sess)
        out = capsys.readouterr().out
        assert "443" in out or "privacy" in out.lower()

    def test_clear_http_443_privacy_active(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=clear, POST returns 443 → privacy-mode warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(443)
        self._run(cfg, _args(sub="clear"), sess)
        out = capsys.readouterr().out
        assert "443" in out or "privacy" in out.lower()

    def test_list_http_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 401 → early return."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "401" in out or "Token" in out

    def test_list_http_443_privacy_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 443 → privacy-mode warning, continues."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(443)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "443" in out or "privacy" in out.lower()

    def test_list_http_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 500 → error message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "500" in out

    def test_sub_shorthand_clear_from_cam_arg(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='clear' with sub=None → shorthand: sub=clear, cam=None."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(200)
        self._run(cfg, _args(cam="clear", sub=None), sess)
        sess.post.assert_called_once()
        assert sess.post.call_args[1]["json"] == []

    def test_url_contains_cam_id(self) -> None:
        """GET is called on the correct motion_sensitive_areas endpoint."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok([])
        self._run(cfg, _args(), sess)
        called_url = sess.get.call_args[0][0]
        assert CAM_ID in called_url
        assert "motion_sensitive_areas" in called_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_privacy_masks
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdPrivacyMasks:
    """Tests for privacy mask zones: list / set / clear."""

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_privacy_masks(cfg, args)

    _sample_mask = {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}

    def test_list_masks_shows_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns one mask → count and coords printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok([self._sample_mask])
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "mask" in out.lower()

    def test_list_empty_masks(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns [] → 'no privacy masks' message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok([])
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "no privacy masks" in out.lower()

    def test_set_valid_json_sends_post(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with valid --json → POST with parsed array."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(200)
        masks_json = json.dumps([self._sample_mask])
        self._run(cfg, _args(sub="set", json=masks_json), sess)
        sess.post.assert_called_once()
        post_body = sess.post.call_args[1]["json"]
        assert isinstance(post_body, list)
        assert post_body[0]["w"] == 0.3

    def test_set_missing_json_no_post(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with no --json → error message, no POST."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(sub="set", json=None), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "--json" in out

    def test_set_invalid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with malformed JSON → error printed, no POST."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(sub="set", json="{bad-json"), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "Invalid JSON" in out or "invalid" in out.lower()

    def test_set_json_not_array(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with JSON object (not array) → error, no POST."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(sub="set", json='{"x": 0.1}'), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "array" in out.lower() or "must be" in out.lower()

    def test_clear_sends_post_empty_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=clear → POST with empty list."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(204)
        self._run(cfg, _args(sub="clear"), sess)
        sess.post.assert_called_once()
        assert sess.post.call_args[1]["json"] == []

    def test_clear_success_204(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=clear, POST 204 → cleared message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(204)
        self._run(cfg, _args(sub="clear"), sess)
        out = capsys.readouterr().out
        assert "cleared" in out.lower() or "clear" in out.lower()

    def test_set_http_443_privacy_active(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set, POST returns 443 → privacy-mode warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(443)
        masks_json = json.dumps([self._sample_mask])
        self._run(cfg, _args(sub="set", json=masks_json), sess)
        out = capsys.readouterr().out
        assert "443" in out or "privacy" in out.lower()

    def test_clear_http_443_privacy_active(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=clear, POST returns 443 → privacy-mode warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = _resp(443)
        self._run(cfg, _args(sub="clear"), sess)
        out = capsys.readouterr().out
        assert "443" in out or "privacy" in out.lower()

    def test_list_http_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 401 → early return."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "401" in out or "Token" in out

    def test_list_http_443_privacy_mode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 443 → privacy-mode warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(443)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "443" in out or "privacy" in out.lower()

    def test_list_http_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 500 → error message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "500" in out

    def test_sub_shorthand_set_from_cam_arg(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='set' with sub=None and no json → shorthand: sub=set, cam=None → missing --json error."""
        cfg = _make_cfg()
        sess = MagicMock()
        self._run(cfg, _args(cam="set", sub=None, json=None), sess)
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "--json" in out

    def test_url_contains_cam_id(self) -> None:
        """GET is called on the correct privacy_masks endpoint."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok([])
        self._run(cfg, _args(), sess)
        called_url = sess.get.call_args[0][0]
        assert CAM_ID in called_url
        assert "privacy_masks" in called_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_lighting_schedule
# ─────────────────────────────────────────────────────────────────────────────

_SCHEDULE_PAYLOAD: dict[str, Any] = {
    "scheduleStatus": "FOLLOW_SCHEDULE",
    "generalLightOnTime": "20:00:00",
    "generalLightOffTime": "06:00:00",
    "darknessThreshold": 0.5,
    "lightOnMotion": True,
    "lightOnMotionFollowUpTimeSeconds": 60,
    "frontIlluminatorInGeneralLightOn": True,
    "frontIlluminatorGeneralLightIntensity": 80,
    "wallwasherInGeneralLightOn": False,
}


class TestCmdLightingSchedule:
    """Tests for lighting schedule: show + set."""

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_lighting_schedule(cfg, args)

    def test_show_schedule_prints_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default (no sub) GET 200 → schedule fields printed."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(copy.deepcopy(_SCHEDULE_PAYLOAD))
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "20:00:00" in out
        assert "06:00:00" in out

    def test_show_http_401_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 401 → early return."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "401" in out or "expired" in out.lower()

    def test_show_http_444_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 444 → offline warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(444)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_show_http_442_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 442 → not-supported message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(442)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "442" in out or "not supported" in out.lower()

    def test_show_http_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 500 → error with status code."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "500" in out

    def test_set_on_off_sends_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with --on and --off → GET then PUT with updated times."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(sub="set", on="21:00", off="07:00"), sess)
        sess.put.assert_called_once()
        put_body = sess.put.call_args[1]["json"]
        assert put_body["generalLightOnTime"] == "21:00:00"
        assert put_body["generalLightOffTime"] == "07:00:00"
        assert put_body["scheduleStatus"] == "FOLLOW_SCHEDULE"

    def test_set_on_already_has_seconds(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--on HH:MM:SS → not duplicated, stored as-is."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(sub="set", on="22:30:00", off=None), sess)
        put_body = sess.put.call_args[1]["json"]
        assert put_body["generalLightOnTime"] == "22:30:00"

    def test_set_motion_flag_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with motion=True → lightOnMotion=True in PUT body."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        payload["lightOnMotion"] = False
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(sub="set", motion=True), sess)
        put_body = sess.put.call_args[1]["json"]
        assert put_body["lightOnMotion"] is True

    def test_set_motion_flag_false(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with motion=False → lightOnMotion=False in PUT body."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        payload["lightOnMotion"] = True
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(sub="set", motion=False), sess)
        put_body = sess.put.call_args[1]["json"]
        assert put_body["lightOnMotion"] is False

    def test_set_threshold(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set with threshold=0.8 → darknessThreshold=0.8 in PUT body."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(sub="set", threshold=0.8), sess)
        put_body = sess.put.call_args[1]["json"]
        assert abs(put_body["darknessThreshold"] - 0.8) < 1e-9

    def test_set_get_fails_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set, GET returns 500 → PUT never called."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500)
        self._run(cfg, _args(sub="set", on="20:00"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "500" in out

    def test_set_put_444_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set, GET ok, PUT returns 444 → offline warning."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(444)
        self._run(cfg, _args(sub="set", on="20:00"), sess)
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_set_put_non200_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set, GET ok, PUT returns 503 → error message."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(503, text="service unavailable")
        self._run(cfg, _args(sub="set", on="20:00"), sess)
        out = capsys.readouterr().out
        assert "503" in out

    def test_set_shorthand_from_cam_arg(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='set' with sub=None → shorthand: sub=set, cam=None → GET+PUT."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(cam="set", sub=None, on="19:00"), sess)
        sess.put.assert_called_once()

    def test_url_contains_cam_id_get(self) -> None:
        """GET is called on the correct lighting_options endpoint."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(copy.deepcopy(_SCHEDULE_PAYLOAD))
        self._run(cfg, _args(), sess)
        called_url = sess.get.call_args[0][0]
        assert CAM_ID in called_url
        assert "lighting_options" in called_url

    def test_set_204_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=set, PUT returns 204 → success message."""
        import copy

        cfg = _make_cfg()
        sess = MagicMock()
        payload = copy.deepcopy(_SCHEDULE_PAYLOAD)
        sess.get.return_value = _ok(payload)
        sess.put.return_value = _resp(204)
        self._run(cfg, _args(sub="set", on="20:00", off="06:00"), sess)
        out = capsys.readouterr().out
        assert "updated" in out.lower() or "schedule" in out.lower()
