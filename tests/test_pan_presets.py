"""
Tests for pan preset feature (v10.7.5).

PIN_EVERY_MODE: one test per named preset + boundary check + invalid + JSON output.
Covers PAN_PRESET_MAP constant values and cmd_pan --preset dispatch.

Source: HA integration v12.6.1 PTZ preset port.
"""

from __future__ import annotations

import types
import unittest.mock as mock

import pytest

import bosch_camera


# ── PAN_PRESET_MAP constant tests ─────────────────────────────────────────────

class TestPanPresetMap:
    """Verify canonical preset→angle mapping values."""

    def test_home_is_zero(self) -> None:
        assert bosch_camera.PAN_PRESET_MAP["home"] == 0

    def test_left_is_minus_60(self) -> None:
        assert bosch_camera.PAN_PRESET_MAP["left"] == -60

    def test_right_is_plus_60(self) -> None:
        assert bosch_camera.PAN_PRESET_MAP["right"] == 60

    def test_back_left_is_minus_120(self) -> None:
        assert bosch_camera.PAN_PRESET_MAP["back-left"] == -120

    def test_back_right_is_plus_120(self) -> None:
        assert bosch_camera.PAN_PRESET_MAP["back-right"] == 120

    def test_all_five_presets_defined(self) -> None:
        assert set(bosch_camera.PAN_PRESET_MAP.keys()) == {
            "home", "left", "right", "back-left", "back-right"
        }


# ── cmd_pan dispatch tests ────────────────────────────────────────────────────

def _make_args(preset: str | None = None, action: str | None = None,
               cam: str | None = None) -> types.SimpleNamespace:
    """Build a minimal argparse Namespace for cmd_pan."""
    return types.SimpleNamespace(cam=cam, action=action, preset=preset)


def _make_cfg(cam_id: str = "AAA-BBB") -> dict:
    return {
        "cameras": {
            "Kamera": {
                "id": cam_id,
                "name": "Kamera",
                "model": "CAMERA_360",
                "pan_limit": 120,
            }
        },
        "access_token": "tok",
        "token_expires_at": 9999999999,
    }


def _mock_session(current: int = 0, limit: int = 120) -> mock.MagicMock:
    """Return a mock requests.Session with stubbed GET and PUT."""
    session = mock.MagicMock()
    get_resp = mock.MagicMock()
    get_resp.status_code = 200
    # First GET: /v11/video_inputs (camera list with featureSupport)
    video_inputs_resp = mock.MagicMock()
    video_inputs_resp.status_code = 200
    video_inputs_resp.json.return_value = [
        {
            "id": "AAA-BBB",
            "title": "Kamera",
            "hardwareVersion": "CAMERA_360",
            "featureSupport": {"panLimit": limit},
        }
    ]
    # Second GET: /v11/video_inputs/{id}/pan
    pan_state_resp = mock.MagicMock()
    pan_state_resp.status_code = 200
    pan_state_resp.json.return_value = {
        "currentAbsolutePosition": current,
        "panLimit": limit,
    }
    put_resp = mock.MagicMock()
    put_resp.status_code = 200
    put_resp.json.return_value = {
        "currentAbsolutePosition": 0,
        "estimatedTimeToCompletion": 500,
        "cameraStoppedAtLimit": False,
    }

    session.get.side_effect = [video_inputs_resp, pan_state_resp]
    session.put.return_value = put_resp
    return session


class TestCmdPanPresetDispatch:
    """Verify that --preset flag resolves to the correct absolutePosition."""

    def _run(self, preset: str, current: int = 99) -> mock.MagicMock:
        """Run cmd_pan with given preset and return the session mock."""
        cfg = _make_cfg()
        session = _mock_session(current=current)
        args = _make_args(preset=preset)
        with mock.patch("bosch_camera.get_token", return_value="tok"), \
             mock.patch("bosch_camera.make_session", return_value=session):
            bosch_camera.cmd_pan(cfg, args)
        return session

    def test_preset_home_sends_0(self) -> None:
        session = self._run("home", current=60)
        session.put.assert_called_once()
        body = session.put.call_args[1]["json"]
        assert body["absolutePosition"] == 0

    def test_preset_left_sends_minus_60(self) -> None:
        session = self._run("left", current=0)
        body = session.put.call_args[1]["json"]
        assert body["absolutePosition"] == -60

    def test_preset_right_sends_plus_60(self) -> None:
        session = self._run("right", current=0)
        body = session.put.call_args[1]["json"]
        assert body["absolutePosition"] == 60

    def test_preset_back_left_sends_minus_120(self) -> None:
        session = self._run("back-left", current=0)
        body = session.put.call_args[1]["json"]
        assert body["absolutePosition"] == -120

    def test_preset_back_right_sends_plus_120(self) -> None:
        session = self._run("back-right", current=0)
        body = session.put.call_args[1]["json"]
        assert body["absolutePosition"] == 120

    def test_no_put_when_already_at_target(self) -> None:
        """If camera is already at the preset angle, no PUT is issued."""
        session = self._run("home", current=0)
        session.put.assert_not_called()

    def test_invalid_action_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Unknown action string → error message, no PUT."""
        cfg = _make_cfg()
        session = _mock_session(current=0)
        args = _make_args(preset=None, action="diagonal")
        with mock.patch("bosch_camera.get_token", return_value="tok"), \
             mock.patch("bosch_camera.make_session", return_value=session):
            bosch_camera.cmd_pan(cfg, args)
        out = capsys.readouterr().out
        assert "Unknown action" in out or "❌" in out
        session.put.assert_not_called()

    def test_numeric_action_still_works(self) -> None:
        """Raw numeric action (legacy) still sends the correct absolutePosition."""
        cfg = _make_cfg()
        session = _mock_session(current=0)
        args = _make_args(preset=None, action="45")
        with mock.patch("bosch_camera.get_token", return_value="tok"), \
             mock.patch("bosch_camera.make_session", return_value=session):
            bosch_camera.cmd_pan(cfg, args)
        body = session.put.call_args[1]["json"]
        assert body["absolutePosition"] == 45
