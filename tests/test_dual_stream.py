"""
Tests for dual-stream URL support (_build_stream_urls, cmd_info, cmd_live --sub).

PIN_EVERY_MODE:
  - test_cmd_live_without_sub_flag_uses_main_url  → sub=False (default)
  - test_cmd_live_with_sub_flag_uses_sub_url       → sub=True
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bosch_camera import _build_stream_urls


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

REMOTE_URL = "proxy-42.live.cbs.boschsecurity.com:42090/abc123hash"
LOCAL_URL = "192.168.1.100:443"


def _conn_result_remote(inst_path: str = REMOTE_URL) -> dict[str, Any]:
    """Minimal PUT /connection REMOTE response."""
    return {
        "urls": [inst_path],
        "imageUrlScheme": "https://{url}/snap.jpg",
        "user": "",
        "password": "",
    }


def _conn_result_local(ip: str = "192.168.1.100", port: int = 443) -> dict[str, Any]:
    """Minimal PUT /connection LOCAL response."""
    return {
        "urls": [f"{ip}:{port}"],
        "imageUrlScheme": "https://{url}/snap.jpg",
        "user": "admin",
        "password": "secret",
    }


def _mock_cam(cam_id: str = "cam-001") -> dict[str, Any]:
    """Minimal camera dict as stored in cfg['cameras']."""
    return {"id": cam_id, "hardwareVersion": "OUTDOOR"}


# ─────────────────────────────────────────────────────────────────────────────
# _build_stream_urls — unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildStreamUrlsRemote:
    def test_returns_two_distinct_urls(self) -> None:
        """_build_stream_urls must return two distinct URL strings."""
        main_url, sub_url = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=1)
        assert main_url != sub_url
        assert main_url != ""
        assert sub_url != ""

    def test_main_url_uses_inst1(self) -> None:
        """Main URL with inst=1 must contain inst=1 query param."""
        main_url, _ = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=1)
        assert "inst=1" in main_url

    def test_sub_url_always_inst2(self) -> None:
        """Sub URL must always use inst=2 regardless of the inst parameter."""
        _, sub_url = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=1)
        assert "inst=2" in sub_url

    def test_sub_url_uses_inst2_even_when_main_is_inst4(self) -> None:
        """sub_url stays inst=2 even when main is set to inst=4 (low bandwidth)."""
        _, sub_url = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=4)
        assert "inst=2" in sub_url

    def test_main_url_is_rtsps(self) -> None:
        """REMOTE main URL must be rtsps:// scheme."""
        main_url, _ = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=1)
        assert main_url.startswith("rtsps://")

    def test_sub_url_uses_sub_indicator_inst2(self) -> None:
        """Sub URL must contain /rtsp_tunnel?inst=2 path fragment."""
        _, sub_url = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=1)
        assert "/rtsp_tunnel?inst=2" in sub_url

    def test_empty_urls_returns_empty_strings(self) -> None:
        """Empty urls list → both return values are empty strings."""
        conn = {"urls": [], "user": "", "password": ""}
        main_url, sub_url = _build_stream_urls(_mock_cam(), conn, inst=1)
        assert main_url == ""
        assert sub_url == ""

    def test_urls_contain_enableaudio(self) -> None:
        """Both URLs must include enableaudio=1 for audio support."""
        main_url, sub_url = _build_stream_urls(_mock_cam(), _conn_result_remote(), inst=1)
        assert "enableaudio=1" in main_url
        assert "enableaudio=1" in sub_url


class TestBuildStreamUrlsLocal:
    def test_local_mode_returns_rtsp_not_rtsps(self) -> None:
        """LOCAL mode (use_tls_proxy=True) must return rtsp:// not rtsps://."""
        main_url, sub_url = _build_stream_urls(
            _mock_cam(),
            _conn_result_local(),
            inst=1,
            use_tls_proxy=True,
            proxy_port=12345,
        )
        assert main_url.startswith("rtsp://")
        assert sub_url.startswith("rtsp://")

    def test_local_mode_uses_proxy_port(self) -> None:
        """LOCAL URL must point to 127.0.0.1 with the given proxy_port."""
        main_url, sub_url = _build_stream_urls(
            _mock_cam(),
            _conn_result_local(),
            inst=1,
            use_tls_proxy=True,
            proxy_port=54321,
        )
        assert "127.0.0.1:54321" in main_url
        assert "127.0.0.1:54321" in sub_url

    def test_local_mode_embeds_credentials(self) -> None:
        """LOCAL URL must URL-encode credentials into the auth prefix."""
        main_url, sub_url = _build_stream_urls(
            _mock_cam(),
            _conn_result_local(),
            inst=1,
            use_tls_proxy=True,
            proxy_port=9999,
        )
        assert "admin:secret@" in main_url
        assert "admin:secret@" in sub_url

    def test_local_mode_two_distinct_urls(self) -> None:
        """LOCAL mode must also return two distinct URLs (inst=1 vs inst=2)."""
        main_url, sub_url = _build_stream_urls(
            _mock_cam(),
            _conn_result_local(),
            inst=1,
            use_tls_proxy=True,
            proxy_port=7777,
        )
        assert main_url != sub_url


# ─────────────────────────────────────────────────────────────────────────────
# Gen1 camera note
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: Gen1 cameras (INDOOR/OUTDOOR) may not actually serve inst=2 data — the
# Bosch RTSP server may silently return the default stream regardless of the
# inst= parameter. The CLI builds and passes the inst=2 URL; whether the camera
# honours it is firmware-dependent. From the Python-CLI perspective both URLs
# are structurally identical (same proxy host, same hash) — the only difference
# is the inst query parameter. There is therefore no separate Gen1/Gen2 URL
# divergence to test at the URL-construction level; the note is documented here
# for future investigation.
class TestGen1SubStreamBehaviorNote:
    def test_gen1_camera_builds_sub_url_same_structure(self) -> None:
        """Gen1 cam: sub_url is structurally the same as main except inst=2.

        The camera may or may not honour inst=2 at runtime — that's a firmware
        question, not a URL-construction bug.
        """
        cam = {"id": "gen1-cam", "hardwareVersion": "OUTDOOR"}
        main_url, sub_url = _build_stream_urls(cam, _conn_result_remote(), inst=1)
        # Both share the same proxy host and hash path
        assert "proxy-42.live.cbs.boschsecurity.com" in main_url
        assert "proxy-42.live.cbs.boschsecurity.com" in sub_url
        assert "abc123hash" in main_url
        assert "abc123hash" in sub_url
        # Only the inst param differs
        assert "inst=1" in main_url
        assert "inst=2" in sub_url


# ─────────────────────────────────────────────────────────────────────────────
# cmd_info — outputs both URLs
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdInfoOutputsBothUrls:
    def test_cmd_info_outputs_main_and_sub_url(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cmd_info must print both Main Stream URL and Sub Stream URL lines."""
        import bosch_camera as bc

        # Patch BASE_DIR so save_config doesn't write to real location
        monkeypatch.setattr(bc, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "CONFIG_FILE", str(tmp_path / "cfg.json"))

        # Minimal camera payload from GET /v11/video_inputs
        cam_payload = [
            {
                "id": "cam-test",
                "title": "TestCam",
                "connectionStatus": "ONLINE",
                "hardwareVersion": "OUTDOOR",
                "firmwareVersion": "7.91.56",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "privacyMode": False,
                "recordingOn": False,
                "numberOfUnreadEvents": 0,
                "timeZone": "UTC",
                "alarmType": "NONE",
                "notificationsEnabledStatus": "ON",
                "notifications": {},
                "featureSupport": {},
                "featureStatus": {},
                "soundIsOnForRecording": False,
            }
        ]

        conn_payload = {
            "urls": ["proxy-42.live.cbs.boschsecurity.com:42090/abc123hash"],
            "imageUrlScheme": "https://{url}/snap.jpg",
            "user": "",
            "password": "",
        }

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = cam_payload

        mock_put_resp = MagicMock()
        mock_put_resp.status_code = 200
        mock_put_resp.json.return_value = conn_payload

        mock_session = MagicMock()
        # GET /v11/video_inputs → camera list
        mock_session.get.return_value = mock_get_resp
        # PUT /connection → stream URLs
        mock_session.put.return_value = mock_put_resp

        cfg: dict = {
            "account": {"bearer_token": "tok", "refresh_token": ""},
            "cameras": {},
            "language": "en",
        }

        args = argparse.Namespace(full=False)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "check_token_age", return_value="0 min"),
        ):
            bc.cmd_info(cfg, args)

        captured = capsys.readouterr().out
        assert "Main Stream URL" in captured
        assert "Sub Stream URL" in captured
        assert "inst=1" in captured  # main
        assert "inst=2" in captured  # sub


# ─────────────────────────────────────────────────────────────────────────────
# cmd_live — PIN_EVERY_MODE
# ─────────────────────────────────────────────────────────────────────────────


def _make_live_args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "sub": False,
        "quality": None,
        "hq": False,
        "inst": 2,
        "local": False,
        "vlc": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _setup_live_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> tuple[MagicMock, dict]:
    """Wire up the minimum mocks for cmd_live and return (mock_session, cfg)."""
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


class TestCmdLiveSubFlag:
    def test_cmd_live_without_sub_flag_uses_main_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Without --sub, cmd_live must pass the main URL (inst=2 default) to ffplay."""
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        args = _make_live_args(sub=False)

        opened_urls: list[str] = []

        def _fake_open(url: str, cam_name: str, snap_url: str = "", use_vlc: bool = False) -> None:
            opened_urls.append(url)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream", side_effect=_fake_open),
        ):
            bc.cmd_live(cfg, args)

        assert len(opened_urls) == 1
        # Default (no --sub) → inst=2 (the argparse default)
        assert "inst=2" in opened_urls[0]

    def test_cmd_live_with_sub_flag_uses_sub_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """With --sub, cmd_live must pass the sub-stream URL (inst=2) to ffplay.

        Both default and --sub produce inst=2 because inst=2 IS the sub-stream.
        The key test here is that --sub forces inst=2 even if --quality high
        (inst=1) would otherwise win. We verify no high-quality URL slips through.
        """
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        # With --sub AND --hq/quality=high → --sub wins
        args = _make_live_args(sub=True, hq=True, quality="high")

        opened_urls: list[str] = []

        def _fake_open(url: str, cam_name: str, snap_url: str = "", use_vlc: bool = False) -> None:
            opened_urls.append(url)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream", side_effect=_fake_open),
        ):
            bc.cmd_live(cfg, args)

        assert len(opened_urls) == 1
        # --sub overrides --hq → must be inst=2, not inst=1
        assert "inst=2" in opened_urls[0]
        assert "inst=1" not in opened_urls[0]

    def test_cmd_live_sub_prints_info_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With --sub, cmd_live must print the sub-stream info message."""
        import bosch_camera as bc

        mock_session, cfg = _setup_live_mocks(monkeypatch, tmp_path)
        args = _make_live_args(sub=True)

        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=mock_session),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "get_cameras", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "resolve_cam", return_value={"TestCam": {"id": "cam-001"}}),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream"),
        ):
            bc.cmd_live(cfg, args)

        captured = capsys.readouterr().out
        # The i18n key cmd.live.using_sub_stream must be printed
        assert "sub-stream" in captured.lower() or "Sub-stream" in captured
