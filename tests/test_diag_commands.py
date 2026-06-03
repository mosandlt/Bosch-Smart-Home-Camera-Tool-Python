"""
Tests for F1/F4/F6/F13 diagnostic commands:
  cmd_snapshot_mjpeg, cmd_onvif_scopes, cmd_rcp_version, cmd_feature_flags
  + helper: fetch_rcp_lan, _get_local_connection_creds

PIN_EVERY_MODE: each success path, each skip/error path, --json output where applicable.

Source: CLAUDE.md F1/F4/F6/F13 feature spec (2026-05-25).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_feature_flags,
    cmd_onvif_scopes,
    cmd_rcp_version,
    cmd_snapshot_mjpeg,
    fetch_rcp_lan,
    _get_local_connection_creds,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID_GEN2  = "AABBCCDD-0000-1111-2222-333344445555"
CAM_ID_GEN1  = "FFFF0000-CAFE-BABE-DEAD-BEEFDEADBEEF"
CAM_NAME_GEN2 = "Terrasse"
CAM_NAME_GEN1 = "Kamera"


def _jwt() -> str:
    import base64
    import time
    import json as _j
    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = base64.urlsafe_b64encode(
        _j.dumps({"exp": int(time.time()) + 3600}).encode()
    ).rstrip(b"=").decode()
    return f"{hdr}.{pay}.sig"


def _make_cfg_gen2() -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME_GEN2: {
                "id": CAM_ID_GEN2,
                "name": CAM_NAME_GEN2,
                "model": "HOME_Eyes_Outdoor",
                "firmware": "9.40.102",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _make_cfg_gen1() -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME_GEN1: {
                "id": CAM_ID_GEN1,
                "name": CAM_NAME_GEN1,
                "model": "CAMERA_360",
                "firmware": "7.91.56",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "output": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _local_conn_response(host: str = "192.0.2.149") -> MagicMock:
    """Mock a successful PUT /connection LOCAL response."""
    return MagicMock(
        status_code=200,
        json=lambda: {
            "type": "LOCAL",
            "urls": [f"{host}:443"],
            "user": "cbs-user-abc123",
            "password": "pass-xyz456",
            "imageUrlScheme": "https://{url}/snap.jpg",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# fetch_rcp_lan
# ─────────────────────────────────────────────────────────────────────────────


class TestFetchRcpLan:
    """Unit tests for the fetch_rcp_lan helper."""

    def test_returns_bytes_on_success(self) -> None:
        """Valid <str>HEXHEX</str> in response → bytes returned."""
        fake_response = MagicMock(
            status_code=200,
            text="<reply><str>deadbeef</str></reply>",
        )
        with patch.object(bosch_camera.requests, "get", return_value=fake_response):
            result = fetch_rcp_lan("192.168.1.1", "user", "pass", "0x0a98")
        assert result == b"\xde\xad\xbe\xef"

    def test_returns_none_on_http_error(self) -> None:
        """Non-200 status code → None returned."""
        fake_response = MagicMock(status_code=401, text="Unauthorized")
        with patch.object(bosch_camera.requests, "get", return_value=fake_response):
            result = fetch_rcp_lan("192.168.1.1", "user", "pass", "0x0a98")
        assert result is None

    def test_returns_none_on_network_exception(self) -> None:
        """Network exception (ConnectionError) → None returned."""
        with patch.object(
            bosch_camera.requests, "get", side_effect=ConnectionError("timeout")
        ):
            result = fetch_rcp_lan("192.168.1.1", "user", "pass", "0x0a98")
        assert result is None

    def test_returns_none_when_no_str_tag(self) -> None:
        """Response without <str> tag → None returned."""
        fake_response = MagicMock(status_code=200, text="<reply><err>1</err></reply>")
        with patch.object(bosch_camera.requests, "get", return_value=fake_response):
            result = fetch_rcp_lan("192.168.1.1", "user", "pass", "0x0a98")
        assert result is None

    def test_returns_none_on_empty_str_tag(self) -> None:
        """<str></str> (empty payload, len=0) → None."""
        fake_response = MagicMock(status_code=200, text="<reply><str></str></reply>")
        with patch.object(bosch_camera.requests, "get", return_value=fake_response):
            result = fetch_rcp_lan("192.168.1.1", "user", "pass", "0x0a98")
        assert result is None

    def test_passes_opcode_in_request(self) -> None:
        """The opcode is forwarded as the 'command' query parameter."""
        fake_response = MagicMock(
            status_code=200,
            text="<reply><str>aa</str></reply>",
        )
        with patch.object(
            bosch_camera.requests, "get", return_value=fake_response
        ) as mock_get:
            fetch_rcp_lan("10.0.0.1", "u", "p", "0x0a98")
        call_kwargs = mock_get.call_args
        params = call_kwargs[1].get("params") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]["params"]
        assert params["command"] == "0x0a98"
        assert params["direction"] == "READ"

    def test_uses_digest_auth(self) -> None:
        """HTTPDigestAuth is used for the request."""
        from requests.auth import HTTPDigestAuth

        fake_response = MagicMock(
            status_code=200,
            text="<reply><str>01</str></reply>",
        )
        with patch.object(
            bosch_camera.requests, "get", return_value=fake_response
        ) as mock_get:
            fetch_rcp_lan("10.0.0.1", "myuser", "mypass", "0x0a98")
        call_kwargs = mock_get.call_args
        auth = call_kwargs[1].get("auth")
        assert isinstance(auth, HTTPDigestAuth)
        assert auth.username == "myuser"
        assert auth.password == "mypass"


# ─────────────────────────────────────────────────────────────────────────────
# _get_local_connection_creds
# ─────────────────────────────────────────────────────────────────────────────


class TestGetLocalConnectionCreds:
    """Unit tests for the _get_local_connection_creds helper."""

    def test_returns_host_user_pass_on_success(self) -> None:
        """200 response with urls/user/password → (host, user, pass) tuple."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "urls": ["192.0.2.149:443"],
                "user": "cbs-user",
                "password": "secret",
            },
        )
        result = _get_local_connection_creds(sess, CAM_ID_GEN2)
        assert result is not None
        host, user, pw = result
        assert host == "192.0.2.149"
        assert user == "cbs-user"
        assert pw == "secret"

    def test_strips_port_from_host(self) -> None:
        """Host entry '192.0.2.149:443' → host='192.0.2.149'."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "urls": ["192.0.2.150:443"],
                "user": "u",
                "password": "p",
            },
        )
        result = _get_local_connection_creds(sess, CAM_ID_GEN2)
        assert result is not None
        assert result[0] == "192.0.2.150"

    def test_returns_none_on_http_error(self) -> None:
        """Non-200 response → None."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500)
        result = _get_local_connection_creds(sess, CAM_ID_GEN2)
        assert result is None

    def test_returns_none_when_no_urls(self) -> None:
        """Empty urls list → None."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            json=lambda: {"urls": [], "user": "u", "password": "p"},
        )
        result = _get_local_connection_creds(sess, CAM_ID_GEN2)
        assert result is None

    def test_returns_none_when_no_creds(self) -> None:
        """Missing user/password → None."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            json=lambda: {"urls": ["192.168.1.1:443"], "user": "", "password": ""},
        )
        result = _get_local_connection_creds(sess, CAM_ID_GEN2)
        assert result is None

    def test_returns_none_on_network_exception(self) -> None:
        """Network exception → None."""
        sess = MagicMock()
        sess.put.side_effect = ConnectionError("host unreachable")
        result = _get_local_connection_creds(sess, CAM_ID_GEN2)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# cmd_snapshot_mjpeg
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdSnapshotMjpeg:
    """Tests for F1 — cmd_snapshot_mjpeg."""

    def _ffmpeg_success(self) -> MagicMock:
        """Simulate ffmpeg returning a minimal valid JPEG."""
        # Minimal 2-byte JPEG header + enough filler bytes
        fake_jpeg = b"\xff\xd8" + b"\x00" * 100
        return MagicMock(returncode=0, stdout=fake_jpeg, stderr=b"")

    def test_skips_gen1_camera(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen1 model → prints 'skipped', no ffmpeg call."""
        cfg = _make_cfg_gen1()
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(subprocess, "run") as mock_run,
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN1))
        assert not mock_run.called
        out = capsys.readouterr().out
        assert "skipped" in out.lower() or "Gen2" in out or "gen2" in out.lower()

    def test_skips_when_local_conn_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        """LOCAL connection failure → prints 'skipped', no ffmpeg call."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(subprocess, "run") as mock_run,
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2))
        assert not mock_run.called
        out = capsys.readouterr().out
        assert "skipped" in out.lower()

    def test_skips_when_ffmpeg_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FileNotFoundError from subprocess → 'skipped', exit 0 (no exception)."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(subprocess, "run", side_effect=FileNotFoundError("ffmpeg")),
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "ffmpeg" in out.lower()
        assert "skipped" in out.lower() or "not found" in out.lower()

    def test_skips_when_ffmpeg_times_out(self, capsys: pytest.CaptureFixture[str]) -> None:
        """TimeoutExpired from subprocess → 'skipped', no crash."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15),
            ),
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "skipped" in out.lower() or "timed out" in out.lower()

    def test_skips_when_ffmpeg_returns_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """FFmpeg exits non-zero → 'skipped'."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                subprocess, "run",
                return_value=MagicMock(returncode=1, stdout=b"", stderr=b"error msg"),
            ),
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "skipped" in out.lower()

    def test_skips_when_output_is_not_jpeg(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Any
    ) -> None:
        """FFmpeg output doesn't start with JPEG magic bytes → 'skipped'."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        out_file = str(tmp_path / "snap.jpg")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                subprocess, "run",
                return_value=MagicMock(returncode=0, stdout=b"NOTJPEG", stderr=b""),
            ),
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2, output=out_file))
        out = capsys.readouterr().out
        assert "skipped" in out.lower()

    def test_saves_jpeg_to_custom_path(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Any
    ) -> None:
        """Successful capture → JPEG written to -o path, prints path + bytes."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        out_file = str(tmp_path / "snap.jpg")
        fake_jpeg = b"\xff\xd8" + b"\xab" * 200
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                subprocess, "run",
                return_value=MagicMock(returncode=0, stdout=fake_jpeg, stderr=b""),
            ),
            patch.object(bosch_camera, "open_file"),
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2, output=out_file))
        import os
        assert os.path.isfile(out_file)
        with open(out_file, "rb") as fh:
            written = fh.read()
        assert written == fake_jpeg
        out = capsys.readouterr().out
        assert out_file in out or "snap.jpg" in out
        assert "202" in out  # byte count: len(fake_jpeg)=202

    def test_ffmpeg_called_with_rtsp_url(self, tmp_path: Any) -> None:
        """FFmpeg subprocess is invoked with the correct RTSP URL."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "urls": ["192.0.2.149:443"],
                "user": "testuser",
                "password": "testpass",
                "imageUrlScheme": "https://{url}/snap.jpg",
            },
        )
        out_file = str(tmp_path / "snap.jpg")
        fake_jpeg = b"\xff\xd8" + b"\x00" * 50
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                subprocess, "run",
                return_value=MagicMock(returncode=0, stdout=fake_jpeg, stderr=b""),
            ) as mock_run,
            patch.object(bosch_camera, "open_file"),
        ):
            cmd_snapshot_mjpeg(cfg, _args(cam=CAM_NAME_GEN2, output=out_file))
        cmd_used = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd_used[0]
        assert "rtsp://testuser:testpass@192.0.2.149:443/rtsp_tunnel?inst=3" in cmd_used
        assert "-vframes" in cmd_used
        assert "1" in cmd_used


# ─────────────────────────────────────────────────────────────────────────────
# cmd_onvif_scopes
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdOnvifScopes:
    """Tests for F4 — cmd_onvif_scopes."""

    def test_prints_scopes_on_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Valid RCP response with scope strings → printed human-readable."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        # null-terminated ASCII: "onvif://www.onvif.org/type/video_encoder" + null + another scope
        payload = b"onvif://www.onvif.org/type/video_encoder\x00onvif://www.onvif.org/hardware/Camera\x00"
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "fetch_rcp_lan", return_value=payload),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "video_encoder" in out
        assert "hardware" in out

    def test_reports_conn_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        """LOCAL connection failure → error message, no crash."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "local" in out.lower() or "offline" in out.lower() or "connection" in out.lower()

    def test_reports_rcp_no_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fetch_rcp_lan returns None → 'no data' message printed."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "fetch_rcp_lan", return_value=None),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "no data" in out.lower() or "0x0a98" in out

    def test_json_output_shape_with_scopes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json flag → valid JSON list with 'cam' + 'scopes' keys."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        payload = b"scope://example\x00scope://other\x00"
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "fetch_rcp_lan", return_value=payload),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2, json=True))
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert data[0]["cam"] == CAM_NAME_GEN2
        assert "scope://example" in data[0]["scopes"]
        assert "scope://other" in data[0]["scopes"]

    def test_json_error_entry_on_conn_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json + LOCAL failure → JSON list with error key."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["error"] == "local_connection_failed"

    def test_json_error_entry_on_no_rcp_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json + RCP no data → JSON list with error key."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "fetch_rcp_lan", return_value=None),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2, json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["error"] == "rcp_no_data"

    def test_payload_without_null_separator_shows_scope(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Payload with a single null-terminated scope → scope printed."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        # One scope followed by null terminator
        payload = b"onvif://single-scope\x00"
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "fetch_rcp_lan", return_value=payload),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "single-scope" in out

    def test_payload_all_nulls_shows_raw_info(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Payload of all null bytes → no scope strings shown, raw bytes info shown."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.put.return_value = _local_conn_response()
        payload = b"\x00\x00\x00"
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "fetch_rcp_lan", return_value=payload),
        ):
            cmd_onvif_scopes(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        # No scope strings → raw info line shown
        assert "3" in out or "raw" in out.lower() or "bytes" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp_version
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpVersion:
    """Tests for F6 — cmd_rcp_version."""

    def test_prints_version_from_opcodes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """0xff00 returns 4 bytes → formatted version string printed."""
        cfg = _make_cfg_gen2()
        # 4-byte version: 1.2.38.150
        ver_bytes = bytes([1, 2, 38, 150])

        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_rcp_setup", return_value=("https://proxy/rcp.xml", "0xsess")),
            patch.object(bosch_camera, "rcp_read", return_value=ver_bytes),
        ):
            cmd_rcp_version(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "1.2.38.150" in out

    def test_prints_unavailable_when_rcp_read_returns_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rcp_read returns None for both opcodes → 'not available' printed."""
        cfg = _make_cfg_gen2()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_rcp_setup", return_value=("https://proxy/rcp.xml", "0xsess")),
            patch.object(bosch_camera, "rcp_read", return_value=None),
        ):
            cmd_rcp_version(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "not available" in out.lower() or "unavailable" in out.lower()

    def test_rcp_setup_failure_handled(self, capsys: pytest.CaptureFixture[str]) -> None:
        """RuntimeError from _rcp_setup → error line printed, no crash."""
        cfg = _make_cfg_gen2()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                bosch_camera, "_rcp_setup",
                side_effect=RuntimeError("PUT /connection returned HTTP 503"),
            ),
        ):
            cmd_rcp_version(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "503" in out

    def test_version_boundary_all_zeros(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Version bytes 0.0.0.0 → '0.0.0.0' printed."""
        cfg = _make_cfg_gen2()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_rcp_setup", return_value=("https://proxy/rcp.xml", "0xsess")),
            patch.object(bosch_camera, "rcp_read", return_value=bytes([0, 0, 0, 0])),
        ):
            cmd_rcp_version(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "0.0.0.0" in out

    def test_version_boundary_max_bytes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Version bytes 255.255.255.255 → '255.255.255.255' printed."""
        cfg = _make_cfg_gen2()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_rcp_setup", return_value=("https://proxy/rcp.xml", "0xsess")),
            patch.object(bosch_camera, "rcp_read", return_value=bytes([255, 255, 255, 255])),
        ):
            cmd_rcp_version(cfg, _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "255.255.255.255" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_feature_flags
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFeatureFlags:
    """Tests for F13 — cmd_feature_flags."""

    def test_prints_enabled_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Dict response with True values → shown in 'Enabled' section."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"APP_RATING": True, "IOT_THINGS_INTEGRATION": False},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args())
        out = capsys.readouterr().out
        assert "APP_RATING" in out
        assert "IOT_THINGS_INTEGRATION" in out

    def test_json_output_dict_form(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json flag + dict response → valid JSON dict printed."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"FLAG_A": True, "FLAG_B": False},
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["FLAG_A"] is True
        assert data["FLAG_B"] is False

    def test_json_output_list_form(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json flag + list response → normalised dict printed."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"name": "FLAG_X", "value": True},
                {"name": "FLAG_Y", "value": False},
            ],
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["FLAG_X"] is True
        assert data["FLAG_Y"] is False

    def test_token_expired_401(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 401 → 'token expired' printed, no crash."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args())
        out = capsys.readouterr().out
        assert "token" in out.lower() or "expired" in out.lower() or "401" in out

    def test_http_error_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-200/non-401 → error message with status code."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args())
        out = capsys.readouterr().out
        assert "503" in out

    def test_http_error_json_error_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json + HTTP 503 → JSON error object."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=503)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args(json=True))
        data = json.loads(capsys.readouterr().out)
        assert "error" in data

    def test_empty_dict_response(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty dict response → 'empty response' message."""
        cfg = _make_cfg_gen2()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, json=lambda: {})
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args())
        out = capsys.readouterr().out
        assert "empty" in out.lower()

    def test_all_enabled_displayed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """All-True dict → all keys appear in output."""
        cfg = _make_cfg_gen2()
        flags = {"AA": True, "BB": True, "CC": True}
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, json=lambda: flags)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args())
        out = capsys.readouterr().out
        for k in flags:
            assert k in out

    def test_all_disabled_displayed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """All-False dict → all keys appear in output."""
        cfg = _make_cfg_gen2()
        flags = {"DD": False, "EE": False}
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, json=lambda: flags)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_feature_flags(cfg, _args())
        out = capsys.readouterr().out
        for k in flags:
            assert k in out
