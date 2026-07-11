"""
Tests for WebRTC / go2rtc support in bosch_camera.py.

PIN_EVERY_MODE:
  - test_cmd_live_webrtc_flag_dispatches_to_go2rtc_path  → --webrtc=True
  - test_cmd_live_no_webrtc_flag_uses_rtsps_path          → --webrtc=False (default)
"""

from __future__ import annotations

import argparse
import errno
import socket
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bosch_camera import (
    Go2rtcError,
    _build_go2rtc_config,
    _start_go2rtc_with_camera,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

REMOTE_RTSPS = (
    "rtsps://proxy-42.live.cbs.boschsecurity.com:443/abc123hash"
    "/rtsp_tunnel?inst=2&enableaudio=1&fmtp=1&maxSessionDuration=3600"
)


def _mock_cam(cam_id: str = "cam-001") -> dict[str, Any]:
    return {"id": cam_id, "hardwareVersion": "OUTDOOR"}


def _make_live_args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "sub": False,
        "quality": None,
        "hq": False,
        "inst": 2,
        "local": False,
        "vlc": False,
        "webrtc": False,
        "webrtc_port": 1984,
        "go2rtc_binary": "go2rtc",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _setup_live_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> tuple[MagicMock, dict]:
    import bosch_camera as bc

    monkeypatch.setattr(bc, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(bc, "CONFIG_FILE", str(tmp_path / "cfg.json"))

    conn_payload: dict[str, Any] = {
        "urls": ["proxy-42.live.cbs.boschsecurity.com:42090/abc123hash"],
        "imageUrlScheme": "https://{url}/snap.jpg",
        "user": "",
        "password": "",
    }
    mock_put_resp = MagicMock()
    mock_put_resp.status_code = 200
    mock_put_resp.json.return_value = conn_payload

    mock_session = MagicMock()
    mock_session.put.return_value = mock_put_resp

    cfg: dict = {
        "account": {"bearer_token": "tok", "refresh_token": ""},
        "cameras": {"TestCam": {"id": "cam-001"}},
        "language": "en",
    }
    return mock_session, cfg


# ─────────────────────────────────────────────────────────────────────────────
# _build_go2rtc_config
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildGo2rtcConfig:
    def test_yaml_has_rtsps_source(self) -> None:
        """Config must include the full RTSPS URL as the stream source."""
        yaml_str = _build_go2rtc_config(REMOTE_RTSPS, stream_name="bosch_cam", port=1984)
        assert REMOTE_RTSPS in yaml_str

    def test_yaml_has_stream_name(self) -> None:
        """Config must include the stream_name key."""
        yaml_str = _build_go2rtc_config(REMOTE_RTSPS, stream_name="my_cam", port=1984)
        assert "my_cam:" in yaml_str

    def test_yaml_has_api_listen(self) -> None:
        """Config must set the API listen address with the given port."""
        yaml_str = _build_go2rtc_config(REMOTE_RTSPS, port=9999)
        assert ":9999" in yaml_str

    def test_yaml_has_webrtc_listen(self) -> None:
        """Config must include webrtc.listen entry."""
        yaml_str = _build_go2rtc_config(REMOTE_RTSPS)
        assert "webrtc:" in yaml_str
        assert "listen:" in yaml_str

    def test_yaml_starts_with_api_section(self) -> None:
        """Config must start with an api: section."""
        yaml_str = _build_go2rtc_config(REMOTE_RTSPS)
        assert yaml_str.startswith("api:")

    def test_custom_stream_name(self) -> None:
        """Custom stream_name must appear verbatim in the YAML."""
        yaml_str = _build_go2rtc_config(REMOTE_RTSPS, stream_name="garten")
        assert "garten:" in yaml_str


# ─────────────────────────────────────────────────────────────────────────────
# _start_go2rtc_with_camera — error paths
# ─────────────────────────────────────────────────────────────────────────────


class TestStartGo2rtcErrors:
    def test_binary_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When go2rtc binary is not in PATH (and not a direct path), raise Go2rtcError."""
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Ensure os.path.isfile also returns False for the bin name
        with patch("os.path.isfile", return_value=False):
            with pytest.raises(Go2rtcError, match="go2rtc"):
                _start_go2rtc_with_camera(REMOTE_RTSPS, go2rtc_bin="go2rtc")

    def test_port_in_use_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the port is already bound, raise Go2rtcError with port_in_use message."""
        # Provide a fake binary so the binary-check passes
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/go2rtc")

        # Mock socket.socket to raise EADDRINUSE on bind

        class _FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, *a, **kw):
                pass

            def bind(self, addr):
                err = OSError(errno.EADDRINUSE, "Address already in use")
                raise err

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(socket, "socket", _FakeSocket)

        with pytest.raises(Go2rtcError, match=str(1984)):
            _start_go2rtc_with_camera(REMOTE_RTSPS, go2rtc_bin="go2rtc", port=1984)

    def test_timeout_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """When go2rtc doesn't open its port within timeout, raise Go2rtcError."""
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/go2rtc")

        # Allow port check to pass
        class _FakeSocketAllowBind:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, *a, **kw):
                pass

            def bind(self, addr):
                pass  # bind succeeds = port is free

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(socket, "socket", _FakeSocketAllowBind)

        # Popen starts but port never becomes available
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process still running

        # create_connection always fails (port never opens)
        def _always_fail(*a, **kw):
            raise OSError("refused")

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("socket.create_connection", side_effect=_always_fail),
            patch("time.sleep"),
            patch(
                "time.time",
                side_effect=[
                    0,  # deadline = 0 + 2
                    0,  # first loop iteration: time.time() < deadline=2 → True
                    0,  # poll returns None (running)
                    0,  # create_connection attempt
                    3,  # second loop iteration: time.time() >= deadline=2 → exit loop
                    3,  # "time.time() < deadline" re-check after loop → port_ready=False
                ],
            ),
        ):
            with pytest.raises(Go2rtcError, match="2"):
                _start_go2rtc_with_camera(
                    REMOTE_RTSPS,
                    go2rtc_bin="go2rtc",
                    start_timeout=2.0,
                )

    def test_happy_path_returns_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: binary found, port free, process starts → returns correct URL."""
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/go2rtc")

        class _FakeSocketAllowBind:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, *a, **kw):
                pass

            def bind(self, addr):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(socket, "socket", _FakeSocketAllowBind)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        # create_connection succeeds immediately
        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("socket.create_connection", return_value=_FakeConn()),
            patch("builtins.open", MagicMock()),
            patch("os.unlink"),
        ):
            proc, url = _start_go2rtc_with_camera(
                REMOTE_RTSPS,
                go2rtc_bin="go2rtc",
                port=1984,
                stream_name="bosch_cam",
            )

        assert url == "http://localhost:1984/stream.html?src=bosch_cam"
        assert proc is mock_proc

    def test_happy_path_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Custom port must appear in the returned browser URL."""
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/go2rtc")

        class _FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def setsockopt(self, *a, **kw):
                pass

            def bind(self, addr):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(socket, "socket", _FakeSocket)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        class _FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("socket.create_connection", return_value=_FakeConn()),
            patch("builtins.open", MagicMock()),
            patch("os.unlink"),
        ):
            _, url = _start_go2rtc_with_camera(
                REMOTE_RTSPS,
                go2rtc_bin="go2rtc",
                port=8080,
            )

        assert "8080" in url


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup: terminate + unlink
# ─────────────────────────────────────────────────────────────────────────────


class TestGo2rtcCleanup:
    def test_cleanup_terminates_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_open_webrtc_stream must call proc.terminate() on KeyboardInterrupt."""
        import bosch_camera as bc

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = KeyboardInterrupt

        with (
            patch.object(
                bc,
                "_start_go2rtc_with_camera",
                return_value=(mock_proc, "http://localhost:1984/stream.html?src=bosch_cam"),
            ),
            patch("webbrowser.open"),
        ):
            bc._open_webrtc_stream(REMOTE_RTSPS, "TestCam", port=1984, go2rtc_bin="go2rtc")

        mock_proc.terminate.assert_called_once()

    def test_cleanup_unlinks_temp_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """_open_webrtc_stream must unlink the temp config file on exit."""
        import bosch_camera as bc

        # Create a real temp file to be unlinked
        cfg_file = tmp_path / "test_go2rtc.yaml"
        cfg_file.write_text("api:\n  listen: ':1984'\n")

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = KeyboardInterrupt
        # Attach cfg path attribute as the real code does
        mock_proc._go2rtc_cfg_path = str(cfg_file)

        with (
            patch.object(
                bc,
                "_start_go2rtc_with_camera",
                return_value=(mock_proc, "http://localhost:1984/stream.html?src=bosch_cam"),
            ),
            patch("webbrowser.open"),
        ):
            bc._open_webrtc_stream(REMOTE_RTSPS, "TestCam", port=1984, go2rtc_bin="go2rtc")

        assert not cfg_file.exists(), "Temp config must be deleted after go2rtc stops"


# ─────────────────────────────────────────────────────────────────────────────
# cmd_live — PIN_EVERY_MODE: webrtc on/off
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdLiveWebrtcDispatch:
    def test_cmd_live_webrtc_flag_dispatches_to_go2rtc_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """With --webrtc, cmd_live must call _open_webrtc_stream (not _open_rtsps_stream)."""
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        args = _make_live_args(webrtc=True, webrtc_port=1984, go2rtc_binary="go2rtc")

        webrtc_calls: list = []

        def _fake_webrtc(rtsps_url, cam_name, *, port, go2rtc_bin):
            webrtc_calls.append({"url": rtsps_url, "port": port, "bin": go2rtc_bin})

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_webrtc_stream", side_effect=_fake_webrtc),
            patch.object(bc, "_open_rtsps_stream") as mock_rtsps,
        ):
            bc.cmd_live(cfg, args)

        assert len(webrtc_calls) == 1, "Expected exactly one _open_webrtc_stream call"
        assert webrtc_calls[0]["port"] == 1984
        mock_rtsps.assert_not_called()

    def test_cmd_live_no_webrtc_flag_uses_rtsps_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """Without --webrtc, cmd_live must call _open_rtsps_stream (not _open_webrtc_stream)."""
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        args = _make_live_args(webrtc=False)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_webrtc_stream") as mock_webrtc,
            patch.object(bc, "_open_rtsps_stream") as mock_rtsps,
        ):
            bc.cmd_live(cfg, args)

        mock_rtsps.assert_called_once()
        mock_webrtc.assert_not_called()

    def test_cmd_live_webrtc_passes_custom_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """--webrtc-port N must be forwarded to _open_webrtc_stream."""
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        args = _make_live_args(webrtc=True, webrtc_port=8080)

        received_port: list[int] = []

        def _fake_webrtc(rtsps_url, cam_name, *, port, go2rtc_bin):
            received_port.append(port)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_webrtc_stream", side_effect=_fake_webrtc),
        ):
            bc.cmd_live(cfg, args)

        assert received_port == [8080]

    def test_cmd_live_webrtc_passes_custom_binary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """--go2rtc-binary PATH must be forwarded to _open_webrtc_stream."""
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        args = _make_live_args(webrtc=True, go2rtc_binary="/opt/go2rtc")

        received_bin: list[str] = []

        def _fake_webrtc(rtsps_url, cam_name, *, port, go2rtc_bin):
            received_bin.append(go2rtc_bin)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_webrtc_stream", side_effect=_fake_webrtc),
        ):
            bc.cmd_live(cfg, args)

        assert received_bin == ["/opt/go2rtc"]
