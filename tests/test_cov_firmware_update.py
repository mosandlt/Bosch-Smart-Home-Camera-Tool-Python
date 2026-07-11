"""
Tests for cmd_firmware_update (show status + install).

PIN_EVERY_MODE: one test per discrete mode/subcommand + default + error path.
Cross-ported from the HA integration's BoschCameraCoordinator.async_install_firmware
during the 2026-07-11 family-parity work (docs/family-parity-plan.md §2b).

Source: bosch_camera.py cmd_firmware_update.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import cmd_firmware_update

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"


def _make_cfg() -> dict[str, Any]:
    return {
        "account": {"bearer_token": "tok", "refresh_token": "", "username": ""},
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


CAM_ID_2 = "AABBCCDD-6666-7777-8888-999900001111"
CAM_NAME_2 = "Garten"


def _make_cfg_two_cams() -> dict[str, Any]:
    cfg = _make_cfg()
    cfg["cameras"][CAM_NAME_2] = {
        "id": CAM_ID_2,
        "name": CAM_NAME_2,
        "model": "HOME_Eyes_Outdoor",
        "firmware": "9.40.102",
    }
    return cfg


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {"cam": None, "sub": None, "yes": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _ok(payload: Any = None) -> MagicMock:
    return MagicMock(status_code=200, json=lambda: payload or {}, text="")


def _resp(status: int, payload: Any = None, text: str = "") -> MagicMock:
    return MagicMock(status_code=status, json=lambda: payload or {}, text=text)


class TestCmdFirmwareUpdate:
    """Tests for firmware status show + install."""

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_firmware_update(cfg, args)

    def test_show_up_to_date(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default (no sub), upToDate=True → shows installed version, no update line."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"current": "9.40.102", "upToDate": True, "updating": False})
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "9.40.102" in out
        assert "Update available" not in out

    def test_show_update_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """upToDate=False + update field set → shows the target version."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": False}
        )
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "9.40.104" in out
        assert "Update available" in out

    def test_show_installing_in_progress(self, capsys: pytest.CaptureFixture[str]) -> None:
        """updating=True → shown in status output."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": True}
        )
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "True" in out

    def test_show_http_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET non-200 → error with status code, no crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "500" in out

    def test_install_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """install with a pending update target → PUT {"id": target}, success message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": False}
        )
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(sub="install"), sess)
        sess.put.assert_called_once()
        put_call = sess.put.call_args
        assert put_call.kwargs["json"] == {"id": "9.40.104"}
        assert CAM_ID in put_call[0][0]
        out = capsys.readouterr().out
        assert "Install started" in out

    def test_install_via_cam_slot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`firmware-update install` (no cam given) parses 'install' from the cam slot."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": False}
        )
        sess.put.return_value = _resp(200)
        self._run(cfg, _args(cam="install"), sess)
        sess.put.assert_called_once()

    def test_install_already_up_to_date_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """install with no update target (already up to date) → no PUT, friendly message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok({"current": "9.40.104", "upToDate": True, "updating": False})
        self._run(cfg, _args(sub="install"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "up to date" in out.lower()

    def test_install_already_in_progress_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """install while updating=True already → no second PUT, warns instead."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": True}
        )
        self._run(cfg, _args(sub="install"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "already in progress" in out.lower()

    def test_install_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """install PUT returns 444 → offline warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": False}
        )
        sess.put.return_value = _resp(444)
        self._run(cfg, _args(sub="install"), sess)
        out = capsys.readouterr().out
        assert "offline" in out.lower()

    def test_install_put_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """install PUT returns a generic error → HTTP status + body shown."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": False}
        )
        sess.put.return_value = _resp(400, text="bad request")
        self._run(cfg, _args(sub="install"), sess)
        out = capsys.readouterr().out
        assert "400" in out

    def test_install_fetch_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET fails before install can be attempted → error shown, no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(500)
        self._run(cfg, _args(sub="install"), sess)
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "500" in out

    def test_show_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 401 → 'Token expired' message, no crash."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(401)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "expired" in out.lower() or "401" in out

    def test_show_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 444 → offline warning."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(444)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_show_not_supported(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 442 → not-supported message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = _resp(442)
        self._run(cfg, _args(), sess)
        out = capsys.readouterr().out
        assert "442" in out or "not supported" in out.lower()


class TestCmdFirmwareUpdateFleetInstallGuard:
    """Regression tests for the 2026-07-11 bug-hunt finding: 'firmware-update
    install' with no camera name silently installed on EVERY camera with zero
    confirmation. Now requires an explicit y/N (or --yes) when targeting >1 camera.
    """

    def _run(self, cfg: dict[str, Any], args: argparse.Namespace, sess: MagicMock) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_firmware_update(cfg, args)

    def test_no_cam_multi_camera_prompts_and_aborts_on_no(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No camera + 2 cams in config + sub=install + answer 'n' → aborts, no PUT/GET at all."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        self._run(cfg, _args(sub="install"), sess)
        sess.get.assert_not_called()
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_no_cam_multi_camera_prompts_and_proceeds_on_yes(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering 'y' at the prompt proceeds to fetch/install both cameras."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()
        sess.get.return_value = _ok({"current": "9.40.102", "upToDate": True, "updating": False})
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        self._run(cfg, _args(sub="install"), sess)
        assert sess.get.call_count == 2

    def test_no_cam_multi_camera_dash_yes_skips_prompt(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--yes skips the confirmation prompt entirely."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()
        sess.get.return_value = _ok({"current": "9.40.102", "upToDate": True, "updating": False})

        def _fail_if_called(_prompt: str) -> str:
            raise AssertionError("input() must not be called when --yes is set")

        monkeypatch.setattr("builtins.input", _fail_if_called)
        self._run(cfg, _args(sub="install", yes=True), sess)
        assert sess.get.call_count == 2

    def test_explicit_single_camera_no_prompt(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit camera name (even with other cameras in config) → no prompt at all."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()
        sess.get.return_value = _ok(
            {"current": "9.40.102", "upToDate": False, "update": "9.40.104", "updating": False}
        )
        sess.put.return_value = _resp(200)

        def _fail_if_called(_prompt: str) -> str:
            raise AssertionError("input() must not be called for a single explicit camera")

        monkeypatch.setattr("builtins.input", _fail_if_called)
        self._run(cfg, _args(cam=CAM_NAME, sub="install"), sess)
        sess.put.assert_called_once()

    def test_eof_during_prompt_treated_as_abort(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-interactive stdin (EOFError) during the prompt aborts safely, no crash."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()

        def _raise_eof(_prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise_eof)
        self._run(cfg, _args(sub="install"), sess)
        sess.get.assert_not_called()
        out = capsys.readouterr().out
        assert "Aborted" in out
