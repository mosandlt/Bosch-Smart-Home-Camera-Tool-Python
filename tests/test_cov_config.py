"""
Tests for 5 previously-untested CLI handlers:
  cmd_config, cmd_rescan, cmd_autofollow, cmd_rules, cmd_test_local

PIN_EVERY_MODE: one test per discrete sub-command/flag/mode, plus default + error paths.
Fake IDs only (cloud-ID AABBCCDD-…, MAC aa:bb:cc:…, IPs 192.0.2.x TEST-NET).
NEVER real device values, tokens, or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_autofollow,
    cmd_config,
    cmd_rescan,
    cmd_rules,
    cmd_test_local,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants / helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
CAM_MAC = "aa:bb:cc:dd:ee:ff"
CAM_IP = "192.0.2.10"
CAM_ID2 = "BBCCDDEE-1111-2222-3333-444455556666"
CAM_NAME2 = "Kamera"


def _jwt() -> str:
    import base64
    import time
    import json as _j

    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = base64.urlsafe_b64encode(
        _j.dumps({"exp": int(time.time()) + 3600}).encode()
    ).rstrip(b"=").decode()
    return f"{hdr}.{pay}.sig"


def _make_cfg(*, pan_limit: int = 0) -> dict[str, Any]:
    return {
        "account": {
            "bearer_token": _jwt(),
            "refresh_token": "fake-refresh",
            "username": "user@example.com",
        },
        "cameras": {
            CAM_NAME: {
                "id": CAM_ID,
                "name": CAM_NAME,
                "model": "HOME_Eyes_Outdoor",
                "firmware": "9.40.102",
                "mac": CAM_MAC,
                "has_light": False,
                "pan_limit": pan_limit,
                "local_ip": CAM_IP,
                "local_username": "",
                "local_password": "",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _make_cfg_two_cams() -> dict[str, Any]:
    cfg = _make_cfg()
    cfg["cameras"][CAM_NAME2] = {
        "id": CAM_ID2,
        "name": CAM_NAME2,
        "model": "HOME_Eyes_Indoor",
        "firmware": "9.40.102",
        "mac": "bb:cc:dd:ee:ff:00",
        "has_light": False,
        "pan_limit": 0,
        "local_ip": "192.0.2.20",
        "local_username": "",
        "local_password": "",
    }
    return cfg


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "action": None,
        "sub": None,
        "rule_id": None,
        "rule_name": None,
        "start": None,
        "end": None,
        "days": None,
        "active": False,
        "inactive": False,
        "name": None,
        "play": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _conn_response(conn_type: str = "REMOTE") -> MagicMock:
    """Fake PUT /connection response for cmd_test_local."""
    if conn_type == "LOCAL":
        return MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "type": "LOCAL",
                "urls": [f"{CAM_IP}:443"],
                "user": "testuser",
                "password": "testpass",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
    return MagicMock(
        status_code=200,
        text="",
        json=lambda: {
            "type": "REMOTE",
            "urls": ["proxy-01.live.cbs.boschsecurity.com:42090/AABBCCDD"],
            "user": "",
            "password": "",
            "imageUrlScheme": "https://{url}/snap.jpg",
            "videoUrlScheme": "",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# cmd_config
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdConfig:
    """Tests for cmd_config — prints masked config."""

    def test_prints_config_file_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Output must mention the config file path."""
        cfg = _make_cfg()
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        assert "Config" in out or "config" in out

    def test_masks_bearer_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bearer_token value must be masked (not printed verbatim)."""
        raw_token = _jwt()
        cfg = _make_cfg()
        cfg["account"]["bearer_token"] = raw_token
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        # Raw token must NOT appear verbatim; masked form has "chars" in it
        assert raw_token not in out
        assert "chars" in out or "..." in out

    def test_masks_refresh_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        """refresh_token field must be masked in the output."""
        raw_refresh = "super-secret-refresh-xyz"
        cfg = _make_cfg()
        cfg["account"]["refresh_token"] = raw_refresh
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        assert raw_refresh not in out

    def test_non_secret_keys_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-secret fields (camera name, model) are printed in plain text."""
        cfg = _make_cfg()
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        assert CAM_NAME in out
        assert "HOME_Eyes_Outdoor" in out

    def test_token_age_line_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Output contains the 'Token age' line."""
        cfg = _make_cfg()
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        assert "Token age" in out or "token" in out.lower()

    def test_empty_cameras_still_works(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Config with no cameras → does not crash."""
        cfg = _make_cfg()
        cfg["cameras"] = {}
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        assert out  # Something is printed

    def test_output_is_valid_json_body(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The JSON part of the output (between first { and last }) must be valid JSON."""
        cfg = _make_cfg()
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        # Extract the JSON block between first { and last }
        start = out.find("{")
        end = out.rfind("}") + 1
        assert start != -1 and end > start
        parsed = json.loads(out[start:end])
        assert isinstance(parsed, dict)

    def test_nested_secret_field_masked(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A nested 'local_password' inside cameras dict must also be masked."""
        secret_pw = "camera-secret-password-9999"
        cfg = _make_cfg()
        cfg["cameras"][CAM_NAME]["local_password"] = secret_pw
        cmd_config(cfg, _args())
        out = capsys.readouterr().out
        assert secret_pw not in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rescan
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRescan:
    """Tests for cmd_rescan — re-discovers cameras and updates config."""

    def _api_camera_list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": CAM_ID,
                "title": CAM_NAME,
                "hardwareVersion": "HOME_Eyes_Outdoor",
                "firmwareVersion": "9.40.102",
                "macAddress": CAM_MAC,
                "featureSupport": {"light": False, "panLimit": 0},
            }
        ]

    def test_prints_found_count(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """discover_cameras result is printed: camera count, name, ID."""
        cfg = _make_cfg()
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(
                bosch_camera, "discover_cameras", return_value=cfg["cameras"]
            ),
        ):
            cmd_rescan(cfg, _args())
        out = capsys.readouterr().out
        assert "1" in out  # found count
        assert CAM_NAME in out

    def test_calls_discover_cameras_once(self, tmp_config_dir: str) -> None:
        """discover_cameras is called exactly once per rescan."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(
                bosch_camera, "discover_cameras", return_value=cfg["cameras"]
            ) as mock_discover,
        ):
            cmd_rescan(cfg, _args())
        mock_discover.assert_called_once()

    def test_passes_session_to_discover(self, tmp_config_dir: str) -> None:
        """make_session result is passed as second arg to discover_cameras."""
        cfg = _make_cfg()
        fake_sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=fake_sess),
            patch.object(
                bosch_camera, "discover_cameras", return_value={}
            ) as mock_discover,
        ):
            cmd_rescan(cfg, _args())
        args_passed = mock_discover.call_args[0]
        assert args_passed[1] is fake_sess

    def test_zero_cameras_does_not_crash(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """discover_cameras returns empty dict → no crash, '0 camera(s)' printed."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "discover_cameras", return_value={}),
        ):
            cmd_rescan(cfg, _args())
        out = capsys.readouterr().out
        assert "0" in out

    def test_two_cameras_both_listed(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two cameras returned → both names printed."""
        cfg = _make_cfg_two_cams()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(
                bosch_camera, "discover_cameras", return_value=cfg["cameras"]
            ),
        ):
            cmd_rescan(cfg, _args())
        out = capsys.readouterr().out
        assert CAM_NAME in out
        assert CAM_NAME2 in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_autofollow
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAutofollow:
    """Tests for cmd_autofollow — get/set auto-follow on 360° cameras."""

    def _cfg_pan(self) -> dict[str, Any]:
        """Config with a camera that has pan_limit > 0 (supports auto-follow)."""
        return _make_cfg(pan_limit=120)

    def _cfg_no_pan(self) -> dict[str, Any]:
        """Config with a camera that has pan_limit == 0 (no auto-follow)."""
        return _make_cfg(pan_limit=0)

    def _sess(self, current: bool = False) -> MagicMock:
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda c=current: {"result": c},
        )
        sess.put.return_value = MagicMock(status_code=204, text="")
        return sess

    def test_skips_when_no_pan_limit(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Camera with pan_limit=0 → 'does not support' printed, no PUT."""
        cfg = self._cfg_no_pan()
        sess = self._sess()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args())
        out = capsys.readouterr().out
        assert "auto-follow" in out.lower() or "panLimit" in out or "support" in out.lower()
        sess.put.assert_not_called()

    def test_get_shows_current_state_enabled(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No action arg → current state (ENABLED) is shown."""
        cfg = self._cfg_pan()
        sess = self._sess(current=True)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action=None))
        out = capsys.readouterr().out
        assert "ENABLED" in out or "enabled" in out.lower()

    def test_get_shows_current_state_disabled(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No action arg → current state (DISABLED) is shown."""
        cfg = self._cfg_pan()
        sess = self._sess(current=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action=None))
        out = capsys.readouterr().out
        assert "DISABLED" in out or "disabled" in out.lower()

    def test_action_on_sends_put_with_true(self) -> None:
        """action='on' → PUT /autofollow with body {"result": true}."""
        cfg = self._cfg_pan()
        sess = self._sess(current=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action="on"))
        sess.put.assert_called_once()
        call_kwargs = sess.put.call_args
        body = call_kwargs[1].get("json") or call_kwargs[0][1]
        assert body == {"result": True}

    def test_action_off_sends_put_with_false(self) -> None:
        """action='off' → PUT /autofollow with body {"result": false}."""
        cfg = self._cfg_pan()
        sess = self._sess(current=True)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action="off"))
        sess.put.assert_called_once()
        call_kwargs = sess.put.call_args
        body = call_kwargs[1].get("json") or call_kwargs[0][1]
        assert body == {"result": False}

    def test_action_on_already_enabled_no_put(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """action='on' when already ENABLED → no PUT (idempotent)."""
        cfg = self._cfg_pan()
        sess = self._sess(current=True)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action="on"))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Already" in out or "already" in out.lower()

    def test_action_off_already_disabled_no_put(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """action='off' when already DISABLED → no PUT (idempotent)."""
        cfg = self._cfg_pan()
        sess = self._sess(current=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action="off"))
        sess.put.assert_not_called()

    def test_get_http_error_handled(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GET /autofollow returns non-200 → error message, no crash."""
        cfg = self._cfg_pan()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=503, text="Service Unavailable")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args())
        out = capsys.readouterr().out
        assert "503" in out or "Could not" in out or "failed" in out.lower()

    def test_put_failure_reported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT /autofollow non-204 → failure message printed."""
        cfg = self._cfg_pan()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200, json=lambda: {"result": False}
        )
        sess.put.return_value = MagicMock(status_code=500, text="Server Error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(action="on"))
        out = capsys.readouterr().out
        assert "500" in out or "Failed" in out or "failed" in out.lower()

    def test_cam_positional_arg_parsed_as_action(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cam='on' with no action → treated as action='on' for all cameras."""
        cfg = self._cfg_pan()
        sess = self._sess(current=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_autofollow(cfg, _args(cam="on", action=None))
        # Should have attempted a PUT
        sess.put.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rules
# ─────────────────────────────────────────────────────────────────────────────

RULE_ID = "rule-0001"
FAKE_RULE = {
    "id": RULE_ID,
    "name": "Night Mode",
    "isActive": True,
    "startTime": "22:00:00",
    "endTime": "06:00:00",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],
}


class TestCmdRules:
    """Tests for cmd_rules — list/add/edit/delete camera automation rules."""

    def _sess_list(self, rules: list[dict[str, Any]] | None = None) -> MagicMock:
        if rules is None:
            rules = [FAKE_RULE]
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200, json=lambda r=rules: r
        )
        sess.post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"id": "new-rule-42"},
            text="",
        )
        sess.put.return_value = MagicMock(status_code=204, text="")
        sess.delete.return_value = MagicMock(status_code=204, text="")
        return sess

    # ── list (default sub=None) ───────────────────────────────────────────────

    def test_list_prints_rules(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default (no sub) → rules are listed."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args())
        out = capsys.readouterr().out
        assert "Night Mode" in out or RULE_ID in out

    def test_list_no_rules_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty rules list → 'no rules' / hint text printed."""
        cfg = _make_cfg()
        sess = self._sess_list(rules=[])
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args())
        out = capsys.readouterr().out
        assert "no rules" in out.lower() or "rules" in out.lower()

    def test_list_401_prints_token_expired(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GET /rules returns 401 → 'Token expired' line."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args())
        out = capsys.readouterr().out
        assert "expired" in out.lower() or "401" in out or "token" in out.lower()

    def test_list_444_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET /rules returns 444 → offline message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        r444 = MagicMock(status_code=444, text="Camera offline")
        r444.json.side_effect = ValueError("no json")
        sess.get.return_value = r444
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args())
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out or "unavailable" in out.lower()

    def test_list_http_error_handled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET /rules returns 500 → error message, no crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args())
        out = capsys.readouterr().out
        assert "500" in out or "Could not" in out.lower() or "failed" in out.lower()

    # ── add ───────────────────────────────────────────────────────────────────

    def test_add_sends_post_with_correct_body(self) -> None:
        """sub='add' → POST /rules with name/startTime/endTime/weekdays."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(
                cfg,
                _args(
                    sub="add",
                    rule_name="Night Mode",
                    start="22:00",
                    end="06:00",
                    days="0,1,2,3,4,5,6",
                ),
            )
        sess.post.assert_called_once()
        body = sess.post.call_args[1].get("json") or sess.post.call_args[0][1]
        assert body["name"] == "Night Mode"
        assert body["startTime"] == "22:00:00"
        assert body["endTime"] == "06:00:00"
        assert body["weekdays"] == [0, 1, 2, 3, 4, 5, 6]

    def test_add_default_name_when_none(self) -> None:
        """sub='add' with no rule_name → default name 'New Rule' used."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="add", days="0,1,2,3,4,5,6"))
        body = sess.post.call_args[1].get("json") or sess.post.call_args[0][1]
        assert isinstance(body["name"], str)
        assert len(body["name"]) > 0

    def test_add_none_start_end_days_fall_back_to_defaults(self) -> None:
        """Regression (bosch_camera.py:5935-5938): an argparse Namespace where
        start/end/days are present but None must NOT crash and must fall back to
        the documented defaults.

        Before the fix `getattr(args, "days", "0,1,2,3,4,5,6")` returned None
        (the 3rd arg only fires when the attribute is *absent*), so `.split(",")`
        raised AttributeError; start/end silently produced "None:00". The fix is
        the `getattr(args, x, None) or <default>` idiom already used by rule_name.
        """
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            # start=end=days=None (the exact menu/argparse default) — must not crash
            cmd_rules(cfg, _args(sub="add", start=None, end=None, days=None))
        body = sess.post.call_args[1].get("json") or sess.post.call_args[0][1]
        assert body["startTime"] == "00:00:00"
        assert body["endTime"] == "23:59:00"
        assert body["weekdays"] == [0, 1, 2, 3, 4, 5, 6]

    def test_add_prints_rule_id_on_success(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """POST /rules 201 → new rule ID printed."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="add", rule_name="Test Rule", days="0,1,2,3,4,5,6"))
        out = capsys.readouterr().out
        assert "new-rule-42" in out or "created" in out.lower()

    def test_add_444_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """POST /rules 444 → offline warning printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        r444 = MagicMock(status_code=444, text="offline")
        r444.json.side_effect = ValueError("no json")
        sess.post.return_value = r444
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="add", rule_name="Night Mode", days="0,1,2,3,4,5,6"))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out or "unavailable" in out.lower()

    def test_add_http_error_handled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """POST /rules 500 → error message, no crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.post.return_value = MagicMock(status_code=500, text="Internal Error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="add", rule_name="Night Mode", days="0,1,2,3,4,5,6"))
        out = capsys.readouterr().out
        assert "500" in out or "Failed" in out or "failed" in out.lower()

    # ── edit ──────────────────────────────────────────────────────────────────

    def test_edit_requires_rule_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub='edit' without --id → error message, no PUT."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id=None))
        out = capsys.readouterr().out
        assert "--id" in out or "required" in out.lower() or "error" in out.lower()
        sess.put.assert_not_called()

    def test_edit_active_flag_sets_isactive_true(self) -> None:
        """sub='edit' with --active → PUT body has isActive=True."""
        cfg = _make_cfg()
        sess = self._sess_list(
            rules=[
                {
                    "id": RULE_ID,
                    "name": "Night Mode",
                    "isActive": False,
                    "startTime": "22:00:00",
                    "endTime": "06:00:00",
                    "weekdays": [0, 1, 2],
                }
            ]
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id=RULE_ID, active=True))
        sess.put.assert_called_once()
        body = sess.put.call_args[1].get("json") or sess.put.call_args[0][1]
        assert body["isActive"] is True

    def test_edit_inactive_flag_sets_isactive_false(self) -> None:
        """sub='edit' with --inactive → PUT body has isActive=False."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id=RULE_ID, inactive=True))
        body = sess.put.call_args[1].get("json") or sess.put.call_args[0][1]
        assert body["isActive"] is False

    def test_edit_rule_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub='edit' with unknown rule_id → 'not found' printed, no PUT."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id="nonexistent-rule"))
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "nonexistent" in out
        sess.put.assert_not_called()

    def test_edit_get_failure_handled(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sub='edit' → GET /rules non-200 → error message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id=RULE_ID, active=True))
        out = capsys.readouterr().out
        assert "500" in out or "Could not" in out.lower() or "failed" in out.lower()

    def test_edit_put_failure_reported(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sub='edit' → PUT /rules non-204 → failure message."""
        cfg = _make_cfg()
        sess = self._sess_list()
        sess.put.return_value = MagicMock(status_code=500, text="Error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id=RULE_ID, active=True))
        out = capsys.readouterr().out
        assert "500" in out or "Failed" in out or "failed" in out.lower()

    def test_edit_name_updated_in_put_body(self) -> None:
        """sub='edit' with --name → PUT body has updated name."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="edit", rule_id=RULE_ID, name="Renamed Rule"))
        body = sess.put.call_args[1].get("json") or sess.put.call_args[0][1]
        assert body["name"] == "Renamed Rule"

    # ── delete ────────────────────────────────────────────────────────────────

    def test_delete_requires_rule_id(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sub='delete' without --id → error message, no DELETE."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="delete", rule_id=None))
        out = capsys.readouterr().out
        assert "--id" in out or "required" in out.lower()
        sess.delete.assert_not_called()

    def test_delete_sends_correct_url(self) -> None:
        """sub='delete' with valid rule_id → DELETE to correct endpoint."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="delete", rule_id=RULE_ID))
        sess.delete.assert_called_once()
        url_called = sess.delete.call_args[0][0]
        assert CAM_ID in url_called
        assert RULE_ID in url_called

    def test_delete_success_prints_confirmation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sub='delete' → 204 → success message printed."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="delete", rule_id=RULE_ID))
        out = capsys.readouterr().out
        assert "deleted" in out.lower() or "success" in out.lower()

    def test_delete_failure_reported(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sub='delete' → 500 → failure message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.delete.return_value = MagicMock(status_code=500, text="Server Error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="delete", rule_id=RULE_ID))
        out = capsys.readouterr().out
        assert "500" in out or "Failed" in out or "failed" in out.lower()

    def test_delete_444_camera_offline(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sub='delete' → 444 → offline warning printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        r444 = MagicMock(status_code=444, text="offline")
        r444.json.side_effect = ValueError("no json")
        sess.delete.return_value = r444
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(sub="delete", rule_id=RULE_ID))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out or "unavailable" in out.lower()

    # ── sub parsed from positional cam arg ────────────────────────────────────

    def test_sub_add_as_cam_positional(self) -> None:
        """cam='add' with no sub → treated as sub='add'."""
        cfg = _make_cfg()
        sess = self._sess_list()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_rules(cfg, _args(cam="add", sub=None, rule_name="Auto Rule", days="0,1,2"))
        sess.post.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_test_local
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdTestLocal:
    """Tests for cmd_test_local — probes LOCAL/REMOTE connection, snap timing."""

    def _snap_response(
        self, status: int = 200, content: bytes = b"\xff\xd8\x00" * 5
    ) -> MagicMock:
        m = MagicMock()
        m.status_code = status
        m.content = content
        m.headers = {"Content-Type": "image/jpeg"} if status == 200 else {}
        return m

    def test_puts_connection_for_both_types(self) -> None:
        """PUT /connection called for both LOCAL and REMOTE."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": ["192.0.2.10:443"],
                "user": "u",
                "password": "p",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
            patch.object(bosch_camera, "open_file"),
        ):
            mock_requests.get.return_value = self._snap_response()
            cmd_test_local(cfg, _args())
        # Two PUT calls — one LOCAL, one REMOTE
        assert sess.put.call_count == 2
        urls = [c[0][0] for c in sess.put.call_args_list]
        assert all("connection" in u for u in urls)

    def test_puts_local_and_remote_types(self) -> None:
        """PUT body for first call is LOCAL, second is REMOTE."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": ["192.0.2.10:443"],
                "user": "u",
                "password": "p",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
            patch.object(bosch_camera, "open_file"),
        ):
            mock_requests.get.return_value = self._snap_response()
            cmd_test_local(cfg, _args())
        types_used = [
            c[1].get("json", {}).get("type") or c[0][1].get("type")
            for c in sess.put.call_args_list
        ]
        assert "LOCAL" in types_used
        assert "REMOTE" in types_used

    def test_non_200_connection_prints_body_and_continues(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PUT /connection non-200 → response body shown, loop continues (no crash)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=503, text="Service Unavailable"
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
        ):
            mock_requests.get.side_effect = AssertionError("should not be called")
            cmd_test_local(cfg, _args())
        out = capsys.readouterr().out
        assert "503" in out or "Service Unavailable" in out

    def test_no_urls_in_response_prints_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PUT /connection 200 but urls=[] → warning message, no snap attempt."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": [],
                "user": "",
                "password": "",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
        ):
            mock_requests.get.side_effect = AssertionError("should not be called")
            cmd_test_local(cfg, _args())
        out = capsys.readouterr().out
        assert "No URLs" in out or "no url" in out.lower()
        mock_requests.get.assert_not_called()

    def test_snap_success_saved_to_disk(
        self, tmp_path: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Successful snap for LOCAL → file written to BASE_DIR."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.side_effect = [
            # LOCAL response
            MagicMock(
                status_code=200,
                text="",
                json=lambda: {
                    "urls": ["192.0.2.10:443"],
                    "user": "u",
                    "password": "p",
                    "imageUrlScheme": "https://{url}/snap.jpg",
                    "videoUrlScheme": "",
                },
            ),
            # REMOTE response (no URLs to simplify)
            MagicMock(
                status_code=200,
                text="",
                json=lambda: {
                    "urls": [],
                    "user": "",
                    "password": "",
                    "imageUrlScheme": "https://{url}/snap.jpg",
                    "videoUrlScheme": "",
                },
            ),
        ]
        jpeg_bytes = b"\xff\xd8" + b"\xaa" * 50
        fake_snap = MagicMock()
        fake_snap.status_code = 200
        fake_snap.content = jpeg_bytes
        fake_snap.headers = {"Content-Type": "image/jpeg"}
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            patch.object(bosch_camera, "requests") as mock_requests,
            patch.object(bosch_camera, "open_file"),
        ):
            mock_requests.get.return_value = fake_snap
            mock_requests.get.side_effect = None  # reset
            mock_requests.get.return_value = fake_snap
            cmd_test_local(cfg, _args())
        # A jpg file should have been written under tmp_path
        saved_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".jpg")]
        assert len(saved_files) >= 1

    def test_snap_network_exception_does_not_crash(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """requests.get raises ConnectionError → warning printed, no crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": ["192.0.2.10:443"],
                "user": "u",
                "password": "p",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
        ):
            mock_requests.get.side_effect = ConnectionError("host unreachable")
            cmd_test_local(cfg, _args())
        out = capsys.readouterr().out
        assert "error" in out.lower() or "snap" in out.lower()

    def test_remote_url_builds_rtsps(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """REMOTE url with /hash → printed RTSP URL starts with rtsps://."""
        cfg = _make_cfg()
        sess = MagicMock()
        remote_url = "proxy-01.live.cbs.boschsecurity.com:42090/AABBCCDD"
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": [remote_url],
                "user": "",
                "password": "",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
        ):
            mock_requests.get.return_value = self._snap_response(status=404)
            cmd_test_local(cfg, _args())
        out = capsys.readouterr().out
        assert "rtsps://" in out

    def test_local_url_builds_rtsp(self, capsys: pytest.CaptureFixture[str]) -> None:
        """LOCAL url without / → printed RTSP URL starts with rtsp://."""
        cfg = _make_cfg()
        sess = MagicMock()
        # LOCAL: no slash in URL
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": ["192.0.2.10:443"],
                "user": "u",
                "password": "p",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
            patch.object(bosch_camera, "open_file"),
        ):
            mock_requests.get.return_value = self._snap_response()
            cmd_test_local(cfg, _args())
        out = capsys.readouterr().out
        assert "rtsp://" in out

    def test_play_flag_calls_open_rtsps_stream(self) -> None:
        """play=True → _open_rtsps_stream is called."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": ["192.0.2.10:443"],
                "user": "u",
                "password": "p",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
            patch.object(bosch_camera, "open_file"),
            patch.object(bosch_camera, "_open_rtsps_stream") as mock_play,
        ):
            mock_requests.get.return_value = self._snap_response()
            cmd_test_local(cfg, _args(play=True))
        mock_play.assert_called()

    def test_play_false_does_not_call_open_stream(self) -> None:
        """play=False (default) → _open_rtsps_stream is NOT called."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            text="",
            json=lambda: {
                "urls": ["192.0.2.10:443"],
                "user": "u",
                "password": "p",
                "imageUrlScheme": "https://{url}/snap.jpg",
                "videoUrlScheme": "",
            },
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "requests") as mock_requests,
            patch.object(bosch_camera, "open_file"),
            patch.object(bosch_camera, "_open_rtsps_stream") as mock_play,
        ):
            mock_requests.get.return_value = self._snap_response()
            cmd_test_local(cfg, _args(play=False))
        mock_play.assert_not_called()
