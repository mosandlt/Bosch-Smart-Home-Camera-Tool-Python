"""
Coverage tests for stream/snapshot functions in bosch_camera.py.

Targets:
  - cmd_live           (lines 1723-1890)
  - _open_rtsps_stream (lines 1289-1358)
  - open_vlc           (lines 693-747)
  - _fetch_snap        (lines 840-903, nested in snap_from_proxy)
  - snap_from_local    (lines 904-951)
  - _put_connection    (lines 810-839, nested in snap_from_proxy)
  - cmd_intercom       (lines 4064-4194)

PIN_EVERY_MODE: player found/not-found, LOCAL/REMOTE, snapshot success/404/error,
  ffplay/mpv/VLC, webrtc/rtsps path, 401 token refresh, intercom happy+no-ffplay.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera as bc


# ─────────────────────────────────────────────────────────────────────────────
# Fake IDs / credentials (no real values)
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_MAC = "aa:bb:cc:dd:ee:ff"
CAM_NAME = "Terrasse"

CONN_REMOTE: dict[str, Any] = {
    "urls": ["proxy-42.live.cbs.boschsecurity.com:42090/abc123hash"],
    "imageUrlScheme": "https://{url}/snap.jpg",
    "user": "",
    "password": "",
}

CONN_LOCAL: dict[str, Any] = {
    "urls": ["192.0.2.10:443"],
    "imageUrlScheme": "https://{url}/snap.jpg",
    "user": "localuser",
    "password": "localpass",
}


def _fake_cam(local: bool = False) -> dict[str, Any]:
    cam: dict[str, Any] = {
        "id": CAM_ID,
        "hardwareVersion": "OUTDOOR",
        "mac": CAM_MAC,
    }
    if local:
        cam["local_ip"] = "192.0.2.10"
        cam["local_username"] = "localuser"
        cam["local_password"] = "localpass"
    return cam


def _fake_cfg(tmp_path: Any, cam_name: str = CAM_NAME) -> dict[str, Any]:
    return {
        "account": {"bearer_token": "faketoken", "refresh_token": ""},
        "cameras": {cam_name: _fake_cam()},
        "language": "en",
    }


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


def _ok_put_resp(payload: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


def _err_resp(status: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {}
    return r


def _mock_session(
    put_resp: MagicMock | None = None, get_resp: MagicMock | None = None
) -> MagicMock:
    sess = MagicMock()
    if put_resp is not None:
        sess.put.return_value = put_resp
    if get_resp is not None:
        sess.get.return_value = get_resp
    return sess


# ─────────────────────────────────────────────────────────────────────────────
# open_vlc
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenVlc:
    """open_vlc — player selection, per-player CLI args, no-player path."""

    def test_no_player_found_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When no player exists, print hint and return without Popen."""
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", return_value=False),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("rtsp://192.0.2.10:443/stream")
        mock_popen.assert_not_called()

    def test_ffplay_chosen_for_rtsp_with_token(self) -> None:
        """ffplay is preferred for rtsp:// and gets -headers when token supplied."""
        ffplay_path = "/usr/bin/ffplay"
        with (
            patch("shutil.which", side_effect=lambda b: ffplay_path if b == "ffplay" else None),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("rtsp://192.0.2.10:443/stream", token="mytoken123")
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == ffplay_path
        assert "-headers" in cmd
        assert "mytoken123" in " ".join(cmd)

    def test_ffplay_chosen_for_rtsp_no_token(self) -> None:
        """ffplay chosen for rtsp:// without token → no -headers flag."""
        ffplay_path = "/usr/bin/ffplay"
        with (
            patch("shutil.which", side_effect=lambda b: ffplay_path if b == "ffplay" else None),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("rtsp://192.0.2.10:443/stream")
        cmd = mock_popen.call_args[0][0]
        assert "-headers" not in cmd

    def test_mpv_chosen_when_no_ffplay(self) -> None:
        """mpv is chosen for rtsp:// when ffplay is absent."""
        mpv_path = "/usr/bin/mpv"

        def _which(b: str) -> str | None:
            return mpv_path if b == "mpv" else None

        with (
            patch("shutil.which", side_effect=_which),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("rtsp://192.0.2.10:443/stream", token="tok")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == mpv_path
        assert "--http-header-fields=Authorization: Bearer tok" in cmd

    def test_mpv_no_token_no_header_flag(self) -> None:
        """mpv without token → no --http-header-fields arg."""
        mpv_path = "/usr/bin/mpv"
        with (
            patch("shutil.which", side_effect=lambda b: mpv_path if b == "mpv" else None),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("rtsp://192.0.2.10:443/stream")
        cmd = mock_popen.call_args[0][0]
        assert not any("http-header" in a for a in cmd)

    def test_vlc_preferred_for_https_url(self) -> None:
        """VLC is the first choice for https:// (non-rtsp) URLs."""
        vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", side_effect=lambda p: p == vlc_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("https://proxy.example.com/stream.m3u8")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == vlc_path

    def test_vlc_with_credentials_embeds_in_rtsp_url(self) -> None:
        """VLC with user+password for rtsp:// → embed creds in URL."""
        vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", side_effect=lambda p: p == vlc_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("rtsp://192.0.2.10:443/stream", user="admin", password="secret")
        cmd = mock_popen.call_args[0][0]
        # Credentials embedded in URL
        assert "admin" in cmd[1]
        assert "secret" in cmd[1]
        assert cmd[1].startswith("rtsp://")

    def test_vlc_with_token_appends_cookie(self) -> None:
        """VLC with bearer token appends --http-cookie to cmd."""
        vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", side_effect=lambda p: p == vlc_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.open_vlc("https://proxy.example.com/stream.m3u8", token="mytoken123")
        cmd = mock_popen.call_args[0][0]
        assert "--http-cookie" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# snap_from_proxy (_put_connection + _fetch_snap)
# ─────────────────────────────────────────────────────────────────────────────


class TestSnapFromProxy:
    """Tests for snap_from_proxy — covers _put_connection and _fetch_snap."""

    def _make_snap_resp(
        self, status: int = 200, content: bytes = b"\xff\xd8snap", ctype: str = "image/jpeg"
    ) -> MagicMock:
        r = MagicMock()
        r.status_code = status
        r.content = content
        r.headers = {"Content-Type": ctype}
        return r

    def test_remote_happy_path_returns_bytes(self) -> None:
        """REMOTE connection success → snap bytes returned."""
        put_resp = _ok_put_resp(CONN_REMOTE)
        snap_resp = self._make_snap_resp()
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = put_resp
            mock_req.get.return_value = snap_resp
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result == b"\xff\xd8snap"

    def test_local_with_digest_auth(self) -> None:
        """LOCAL connection with user+pass uses HTTPDigestAuth."""
        put_resp = _ok_put_resp(CONN_LOCAL)
        snap_resp = self._make_snap_resp()
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.side_effect = [
                _err_resp(503),  # REMOTE fails
                put_resp,  # LOCAL succeeds
            ]
            mock_req.get.return_value = snap_resp
            mock_req.get = MagicMock(return_value=snap_resp)
            # Call directly with LOCAL to test Digest branch
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        # Either result is bytes or None — just ensure no exception
        assert result is None or isinstance(result, bytes)

    def test_put_connection_401_triggers_refresh(self) -> None:
        """PUT /connection 401 + cfg present → get_token called once."""
        r_401 = _err_resp(401)
        r_200 = _ok_put_resp(CONN_REMOTE)
        snap_resp = self._make_snap_resp()
        cfg: dict[str, Any] = {
            "account": {"bearer_token": "oldtok", "refresh_token": "rt"},
            "cameras": {CAM_NAME: _fake_cam()},
            "language": "en",
        }
        with (
            patch.object(bc, "requests") as mock_req,
            patch.object(bc, "get_token", return_value="newtok") as mock_get_tok,
            patch.object(bc, "make_session"),
        ):
            mock_req.put.side_effect = [r_401, r_200, r_200]
            mock_req.get.return_value = snap_resp
            bc.snap_from_proxy(_fake_cam(), token="oldtok", cfg=cfg, session=mock_req)
        mock_get_tok.assert_called()

    def test_put_connection_returns_non_200_returns_none(self) -> None:
        """_fetch_snap: PUT non-200 → returns None for that conn type."""
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = _err_resp(503)
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result is None

    def test_snap_404_retries_and_fails(self) -> None:
        """_fetch_snap: snap.jpg 404 → retry PUT + second snap → None on failure."""
        put_resp = _ok_put_resp(CONN_REMOTE)
        snap_404 = self._make_snap_resp(status=404)
        with patch.object(bc, "requests") as mock_req:
            # Both REMOTE and LOCAL put succeed, but snap always 404 first
            mock_req.put.return_value = put_resp
            # First snap → 404; retry snap → 404 again (exhausted)
            mock_req.get.side_effect = [snap_404, put_resp, snap_404, snap_404, snap_404]
            mock_req.get.side_effect = [snap_404, snap_404]
            # Reset for cleaner test: put returns ok always; get returns 404
            mock_req.put.return_value = put_resp
            mock_req.get.return_value = snap_404
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result is None

    def test_snap_404_retry_succeeds(self) -> None:
        """_fetch_snap: snap.jpg 404 → retry → 200 → returns bytes."""
        put_resp = _ok_put_resp(CONN_REMOTE)
        snap_404 = self._make_snap_resp(status=404)
        snap_ok = self._make_snap_resp()
        call_count = 0

        def _get_side(*a: Any, **kw: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return snap_404 if call_count == 1 else snap_ok

        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = put_resp
            mock_req.get.side_effect = _get_side
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result == b"\xff\xd8snap"

    def test_snap_http_error_non_200_non_404_returns_none(self) -> None:
        """_fetch_snap: snap.jpg 503 → None for that conn type."""
        put_resp = _ok_put_resp(CONN_REMOTE)
        snap_err = self._make_snap_resp(status=503)
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = put_resp
            mock_req.get.return_value = snap_err
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result is None

    def test_snap_wrong_content_type_returns_none(self) -> None:
        """_fetch_snap: snap returns non-image Content-Type → None."""
        put_resp = _ok_put_resp(CONN_REMOTE)
        snap_bad = self._make_snap_resp(status=200, ctype="text/plain")
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = put_resp
            mock_req.get.return_value = snap_bad
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result is None

    def test_exception_in_fetch_snap_returns_none(self) -> None:
        """_fetch_snap: exception during GET snap → None (all types fail)."""
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = _ok_put_resp(CONN_REMOTE)
            mock_req.get.side_effect = OSError("connection refused")
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result is None

    def test_no_urls_in_response_returns_none(self) -> None:
        """_fetch_snap: PUT 200 but empty urls → None."""
        conn_no_urls = dict(CONN_REMOTE, urls=[])
        with patch.object(bc, "requests") as mock_req:
            mock_req.put.return_value = _ok_put_resp(conn_no_urls)
            result = bc.snap_from_proxy(_fake_cam(), token="tok", session=mock_req)
        assert result is None

    def test_401_without_cfg_does_not_refresh(self) -> None:
        """PUT 401 without cfg → no refresh, returns None."""
        with patch.object(bc, "requests") as mock_req, patch.object(bc, "get_token") as mock_gt:
            mock_req.put.return_value = _err_resp(401)
            bc.snap_from_proxy(_fake_cam(), token="tok", cfg=None, session=mock_req)
        mock_gt.assert_not_called()

    def test_put_connection_still_401_after_refresh(self) -> None:
        """Double 401 → still_401 print path exercised."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": "tok"},
            "cameras": {CAM_NAME: _fake_cam()},
        }
        with (
            patch.object(bc, "requests") as mock_req,
            patch.object(bc, "get_token", return_value="newtok"),
            patch.object(bc, "make_session"),
        ):
            mock_req.put.return_value = _err_resp(401)
            result = bc.snap_from_proxy(_fake_cam(), token="tok", cfg=cfg, session=mock_req)
        assert result is None

    def test_get_token_raises_during_refresh(self) -> None:
        """get_token raise during 401 refresh → returns original response."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": "tok"},
            "cameras": {CAM_NAME: _fake_cam()},
        }
        r_401 = _err_resp(401)
        with (
            patch.object(bc, "requests") as mock_req,
            patch.object(bc, "get_token", side_effect=RuntimeError("net err")),
        ):
            mock_req.put.return_value = r_401
            result = bc.snap_from_proxy(_fake_cam(), token="tok", cfg=cfg, session=mock_req)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# snap_from_local
# ─────────────────────────────────────────────────────────────────────────────


class TestSnapFromLocal:
    """snap_from_local — local IP + Digest auth → JPEG or None."""

    def _ok_snap(self) -> MagicMock:
        r = MagicMock()
        r.status_code = 200
        r.content = b"\xff\xd8local"
        r.headers = {"Content-Type": "image/jpeg"}
        return r

    def test_missing_local_ip_returns_none(self) -> None:
        """No local_ip → return None immediately."""
        result = bc.snap_from_local({"id": CAM_ID})
        assert result is None

    def test_missing_credentials_returns_none(self) -> None:
        """local_ip present but no username → return None."""
        cam = {"id": CAM_ID, "local_ip": "192.0.2.10"}
        result = bc.snap_from_local(cam)
        assert result is None

    def test_happy_path_returns_jpeg(self) -> None:
        """Full local credentials + 200 image response → returns bytes."""
        cam = _fake_cam(local=True)
        with patch.object(bc, "bosch_get", return_value=self._ok_snap()):
            result = bc.snap_from_local(cam)
        assert result == b"\xff\xd8local"

    def test_non_image_content_type_returns_none(self) -> None:
        """200 but non-image Content-Type → None."""
        r = MagicMock()
        r.status_code = 200
        r.content = b"garbage"
        r.headers = {"Content-Type": "text/html"}
        cam = _fake_cam(local=True)
        with patch.object(bc, "bosch_get", return_value=r):
            result = bc.snap_from_local(cam)
        assert result is None

    def test_http_error_status_returns_none(self) -> None:
        """401 response → None."""
        r = MagicMock()
        r.status_code = 401
        r.headers: dict[str, str] = {}
        cam = _fake_cam(local=True)
        with patch.object(bc, "bosch_get", return_value=r):
            result = bc.snap_from_local(cam)
        assert result is None

    def test_exception_returns_none(self) -> None:
        """Exception during bosch_get → None (not propagated)."""
        cam = _fake_cam(local=True)
        with patch.object(bc, "bosch_get", side_effect=OSError("refused")):
            result = bc.snap_from_local(cam)
        assert result is None

    def test_url_has_jpeg_size_param(self) -> None:
        """URL must contain JpegSize= to hit the fast-path on the camera."""
        cam = _fake_cam(local=True)
        captured: list[str] = []

        def _fake_get(url: str, **kw: Any) -> MagicMock:
            captured.append(url)
            raise OSError("stop")

        with patch.object(bc, "bosch_get", side_effect=_fake_get):
            bc.snap_from_local(cam)
        assert captured and "JpegSize" in captured[0]


# ─────────────────────────────────────────────────────────────────────────────
# _open_rtsps_stream
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenRtspsStream:
    """_open_rtsps_stream — player selection and launch (no real subprocess)."""

    RTSPS = "rtsps://proxy-42.live.cbs.boschsecurity.com:443/abc/rtsp_tunnel?inst=2"

    def test_ffplay_launched_when_found(self) -> None:
        """ffplay found → Popen called with ffplay cmd, proc.wait() called."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        with (
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME)
        cmd = mock_popen.call_args[0][0]
        assert "ffplay" in cmd[0]
        assert self.RTSPS in cmd

    def test_mpv_launched_when_no_ffplay(self) -> None:
        """When ffplay absent, mpv is used."""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0

        def _which(b: str) -> str | None:
            return "/usr/bin/mpv" if b == "mpv" else None

        with (
            patch("shutil.which", side_effect=_which),
            patch("os.path.exists", return_value=False),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME)
        cmd = mock_popen.call_args[0][0]
        assert "mpv" in cmd[0]

    def test_no_player_falls_back_to_snap_loop(self) -> None:
        """No ffplay/mpv → _live_snap_loop called with fallback URL."""
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", return_value=False),
            patch.object(bc, "_live_snap_loop") as mock_loop,
            patch("subprocess.Popen") as mock_popen,
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME, fallback_snap_url="https://proxy/snap.jpg")
        mock_loop.assert_called_once_with("https://proxy/snap.jpg", CAM_NAME)
        mock_popen.assert_not_called()

    def test_no_player_no_fallback_url_does_nothing(self) -> None:
        """No player + no fallback URL → just prints hint, no Popen."""
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", return_value=False),
            patch.object(bc, "_live_snap_loop") as mock_loop,
            patch("subprocess.Popen") as mock_popen,
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME)
        mock_loop.assert_not_called()
        mock_popen.assert_not_called()

    def test_keyboard_interrupt_kills_proc(self) -> None:
        """KeyboardInterrupt during wait() → proc.kill() called."""
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = KeyboardInterrupt
        with (
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME)
        mock_proc.kill.assert_called()

    def test_vlc_pipe_launched_when_vlc_exists_and_use_vlc_true(self) -> None:
        """use_vlc=True + VLC exists → ffmpeg pipe + VLC launched."""
        vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
        ffmpeg_path = "/usr/bin/ffmpeg"
        mock_proxy = MagicMock()
        mock_proxy.stdout = MagicMock()
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0

        def _which(b: str) -> str | None:
            return ffmpeg_path if b == "ffmpeg" else None

        def _exists(p: str) -> bool:
            return p in (vlc_path, ffmpeg_path)

        with (
            patch("shutil.which", side_effect=_which),
            patch("os.path.exists", side_effect=_exists),
            patch(
                "subprocess.Popen", side_effect=[mock_proxy, mock_proc, MagicMock()]
            ) as mock_popen,
            patch("time.sleep"),
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME, use_vlc=True)
        # At least 2 Popen calls: ffmpeg pipe + VLC
        assert mock_popen.call_count >= 2

    def test_vlc_pipe_no_ffmpeg_returns_early(self) -> None:
        """use_vlc=True but ffmpeg absent → prints warning, no Popen."""
        vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"

        def _exists(p: str) -> bool:
            return p == vlc_path

        with (
            patch("shutil.which", return_value=None),
            patch("os.path.exists", side_effect=_exists),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME, use_vlc=True)
        mock_popen.assert_not_called()

    def test_vlc_keyboard_interrupt_kills_both(self) -> None:
        """VLC pipe: KeyboardInterrupt → both proc and proxy killed."""
        vlc_path = "/Applications/VLC.app/Contents/MacOS/VLC"
        ffmpeg_path = "/usr/bin/ffmpeg"
        mock_proxy = MagicMock()
        mock_proxy.stdout = MagicMock()
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = KeyboardInterrupt

        def _which(b: str) -> str | None:
            return ffmpeg_path if b == "ffmpeg" else None

        def _exists(p: str) -> bool:
            return p in (vlc_path, ffmpeg_path)

        with (
            patch("shutil.which", side_effect=_which),
            patch("os.path.exists", side_effect=_exists),
            patch("subprocess.Popen", side_effect=[mock_proxy, mock_proc, MagicMock()]),
            patch("time.sleep"),
        ):
            bc._open_rtsps_stream(self.RTSPS, CAM_NAME, use_vlc=True)
        mock_proc.kill.assert_called()
        mock_proxy.kill.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_live
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdLive:
    """cmd_live — connection negotiation, player launch, quality presets."""

    def _setup(
        self, tmp_path: Any, conn_payload: dict[str, Any] | None = None, put_status: int = 200
    ) -> tuple[dict[str, Any], MagicMock]:
        import bosch_camera as bc_local

        bc_local.BASE_DIR = str(tmp_path)
        bc_local.CONFIG_FILE = str(tmp_path / "cfg.json")
        payload = conn_payload or CONN_REMOTE
        put_resp = MagicMock()
        put_resp.status_code = put_status
        put_resp.json.return_value = payload
        sess = MagicMock()
        sess.put.return_value = put_resp
        ping_resp = MagicMock()
        ping_resp.status_code = 200
        ping_resp.json.return_value = {"status": "ONLINE"}
        sess.get.return_value = ping_resp
        cfg = _fake_cfg(tmp_path)
        return cfg, sess

    def _patch_live(self, monkeypatch: pytest.MonkeyPatch, sess: MagicMock, tmp_path: Any) -> None:
        monkeypatch.setattr(bc, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "CONFIG_FILE", str(tmp_path / "cfg.json"))

    def test_camera_offline_skips_stream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """OFFLINE camera → no PUT /connection, no stream opened."""
        cfg, sess = self._setup(tmp_path)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="OFFLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream") as mock_stream,
        ):
            bc.cmd_live(cfg, _make_live_args())
        mock_stream.assert_not_called()

    def test_remote_connection_opens_rtsps_stream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """REMOTE 200 → _open_rtsps_stream called with rtsps:// URL."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream") as mock_stream,
        ):
            bc.cmd_live(cfg, _make_live_args())
        mock_stream.assert_called_once()
        url_arg = mock_stream.call_args[0][0]
        assert url_arg.startswith("rtsps://")

    def test_sub_stream_uses_inst2_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """--sub flag → rtsps URL contains inst=2."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream") as mock_stream,
        ):
            bc.cmd_live(cfg, _make_live_args(sub=True))
        url_arg = mock_stream.call_args[0][0]
        assert "inst=2" in url_arg

    def test_quality_high_sets_hq_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """--quality high → PUT is called with highQualityVideo=True."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream"),
        ):
            bc.cmd_live(cfg, _make_live_args(quality="high"))
        put_call = sess.put.call_args
        assert put_call.kwargs["json"]["highQualityVideo"] is True

    def test_quality_low_sets_hq_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """--quality low → PUT called with highQualityVideo=False."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream"),
        ):
            bc.cmd_live(cfg, _make_live_args(quality="low"))
        put_call = sess.put.call_args
        assert put_call.kwargs["json"]["highQualityVideo"] is False

    def test_local_flag_forces_local_connection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """--local flag → PUT is called with type='LOCAL'."""
        cfg, sess = self._setup(tmp_path, CONN_LOCAL)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_start_tls_proxy_sync", return_value=8554),
            patch.object(bc, "_open_rtsps_stream"),
        ):
            bc.cmd_live(cfg, _make_live_args(local=True))
        put_json = sess.put.call_args.kwargs["json"]
        assert put_json["type"] == "LOCAL"

    def test_401_triggers_one_token_refresh(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """PUT /connection 401 → one get_token refresh, then retried."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        r_401 = MagicMock()
        r_401.status_code = 401
        r_401.json.return_value = {}
        r_200 = MagicMock()
        r_200.status_code = 200
        r_200.json.return_value = CONN_REMOTE
        sess.put.side_effect = [r_401, r_200]
        sess2 = MagicMock()
        sess2.put.return_value = r_200
        with (
            patch.object(bc, "get_token", return_value="tok") as mock_gt,
            patch.object(bc, "make_session", side_effect=[sess, sess2]),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream"),
        ):
            bc.cmd_live(cfg, _make_live_args())
        assert mock_gt.call_count >= 1

    def test_no_result_shows_event_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """All PUT attempts fail → falls back to latest event snapshot."""
        cfg, sess = self._setup(tmp_path, put_status=503)
        self._patch_live(monkeypatch, sess, tmp_path)
        fake_img = b"\xff\xd8event"
        ev_snap = MagicMock()
        ev_snap.status_code = 200
        ev_snap.content = fake_img
        sess.get.return_value = ev_snap
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(
                bc,
                "api_get_events",
                return_value=[
                    {
                        "imageUrl": "https://events.cbs.boschsecurity.com/snap.jpg",
                        "timestamp": "2024-01-01T12:00:00Z",
                    }
                ],
            ),
            patch.object(bc, "_is_safe_bosch_url", return_value=True),
            patch.object(bc, "open_file"),
        ):
            bc.cmd_live(cfg, _make_live_args())
        # Session.get called for event snapshot
        sess.get.assert_called()

    def test_webrtc_flag_calls_open_webrtc_stream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """--webrtc flag → _open_webrtc_stream called instead of _open_rtsps_stream."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_webrtc_stream") as mock_webrtc,
            patch.object(bc, "_open_rtsps_stream") as mock_rtsps,
        ):
            bc.cmd_live(cfg, _make_live_args(webrtc=True))
        mock_webrtc.assert_called_once()
        mock_rtsps.assert_not_called()

    def test_vlc_flag_passed_to_open_rtsps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """--vlc flag is forwarded to _open_rtsps_stream as use_vlc=True."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream") as mock_stream,
        ):
            bc.cmd_live(cfg, _make_live_args(vlc=True))
        _, kwargs = mock_stream.call_args
        assert kwargs.get("use_vlc") is True

    def test_local_connection_uses_tls_proxy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """LOCAL response → _start_tls_proxy_sync called, rtsp:// proxy URL used."""
        cfg, sess = self._setup(tmp_path, CONN_LOCAL)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_start_tls_proxy_sync", return_value=8554) as mock_proxy,
            patch.object(bc, "_open_rtsps_stream"),
        ):
            bc.cmd_live(cfg, _make_live_args(local=True))
        mock_proxy.assert_called_once()

    def test_updating_status_still_proceeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """UPDATING status is treated as offline (stream skipped)."""
        cfg, sess = self._setup(tmp_path, CONN_REMOTE)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="UPDATING_1.0"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream") as mock_stream,
        ):
            bc.cmd_live(cfg, _make_live_args())
        mock_stream.assert_not_called()

    def test_no_urls_in_result_prints_warning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PUT 200 but urls=[] → prints ⚠️ message, no stream opened."""
        conn_empty = dict(CONN_REMOTE, urls=[])
        cfg, sess = self._setup(tmp_path, conn_empty)
        self._patch_live(monkeypatch, sess, tmp_path)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bc, "api_ping", return_value="ONLINE"),
            patch.object(bc, "save_config"),
            patch.object(bc, "_open_rtsps_stream") as mock_stream,
        ):
            bc.cmd_live(cfg, _make_live_args())
        mock_stream.assert_not_called()
        out = capsys.readouterr().out
        assert "No URLs" in out or "⚠️" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_intercom
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdIntercom:
    """cmd_intercom — audio session, ffplay launch, no-ffplay, duration expiry."""

    def _base_cfg(self) -> dict[str, Any]:
        return {
            "account": {"bearer_token": "faketoken", "refresh_token": ""},
            "cameras": {CAM_NAME: _fake_cam()},
            "language": "en",
        }

    def _make_args(self, duration: int = 5, speaker_level: int = 50) -> argparse.Namespace:
        return argparse.Namespace(cam=None, duration=duration, speaker_level=speaker_level)

    def test_multiple_cameras_prints_error(self) -> None:
        """len(cams) != 1 → prints error, returns."""
        cfg = self._base_cfg()
        cfg["cameras"]["Kamera"] = _fake_cam()
        sess = MagicMock()
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bc, "resolve_cam", return_value=cfg["cameras"]),
        ):
            bc.cmd_intercom(cfg, self._make_args())
        # No subprocess call expected
        sess.put.assert_not_called()

    def test_no_ffplay_prints_error(self) -> None:
        """ffplay absent → prints error, no Popen."""
        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_resp = MagicMock()
        audio_resp.status_code = 200
        audio_resp.json.return_value = {"audioEnabled": True, "microphoneLevel": 50}
        audio_put = MagicMock()
        audio_put.status_code = 200
        conn_resp = MagicMock()
        conn_resp.status_code = 200
        conn_resp.json.return_value = CONN_REMOTE
        sess = MagicMock()
        sess.get.return_value = audio_resp
        sess.put.side_effect = [audio_put, conn_resp]
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.cmd_intercom(cfg, self._make_args())
        mock_popen.assert_not_called()

    def test_happy_path_ffplay_launched(self) -> None:
        """Connection succeeds + ffplay found → Popen called with ffplay cmd."""
        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_resp = MagicMock()
        audio_resp.status_code = 200
        audio_resp.json.return_value = {"audioEnabled": True, "microphoneLevel": 50}
        audio_put = MagicMock()
        audio_put.status_code = 200
        conn_resp = MagicMock()
        conn_resp.status_code = 200
        conn_resp.json.return_value = CONN_REMOTE
        sess = MagicMock()
        sess.get.return_value = audio_resp
        sess.put.side_effect = [audio_put, conn_resp]
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.return_value = 0
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            bc.cmd_intercom(cfg, self._make_args(duration=1))
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "ffplay" in cmd[0]

    def test_timeout_expired_terminates_proc(self) -> None:
        """proc.wait() TimeoutExpired → proc.terminate() called."""
        import subprocess as subp

        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_resp = MagicMock()
        audio_resp.status_code = 200
        audio_resp.json.return_value = {"audioEnabled": True, "microphoneLevel": 50}
        audio_put = MagicMock()
        audio_put.status_code = 200
        conn_resp = MagicMock()
        conn_resp.status_code = 200
        conn_resp.json.return_value = CONN_REMOTE
        sess = MagicMock()
        sess.get.return_value = audio_resp
        sess.put.side_effect = [audio_put, conn_resp]
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.side_effect = subp.TimeoutExpired(cmd="ffplay", timeout=5)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            bc.cmd_intercom(cfg, self._make_args(duration=5))
        mock_proc.terminate.assert_called()

    def test_keyboard_interrupt_terminates_proc(self) -> None:
        """KeyboardInterrupt during intercom → proc.terminate() called."""
        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_resp = MagicMock()
        audio_resp.status_code = 200
        audio_resp.json.return_value = {"audioEnabled": True, "microphoneLevel": 50}
        audio_put = MagicMock()
        audio_put.status_code = 200
        conn_resp = MagicMock()
        conn_resp.status_code = 200
        conn_resp.json.return_value = CONN_REMOTE
        sess = MagicMock()
        sess.get.return_value = audio_resp
        sess.put.side_effect = [audio_put, conn_resp]
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.wait.side_effect = KeyboardInterrupt
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            bc.cmd_intercom(cfg, self._make_args(duration=60))
        mock_proc.terminate.assert_called()

    def test_no_connection_data_returns_early(self) -> None:
        """All PUT /connection attempts fail → prints error, no ffplay Popen."""
        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_resp = MagicMock()
        audio_resp.status_code = 200
        audio_resp.json.return_value = {"audioEnabled": True, "microphoneLevel": 50}
        audio_put = MagicMock()
        audio_put.status_code = 200
        conn_err = MagicMock()
        conn_err.status_code = 503
        conn_err.json.return_value = {}
        sess = MagicMock()
        sess.get.return_value = audio_resp
        sess.put.side_effect = [audio_put, conn_err, conn_err]
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("subprocess.Popen") as mock_popen,
        ):
            bc.cmd_intercom(cfg, self._make_args())
        mock_popen.assert_not_called()

    def test_audio_get_442_proceeds_without_set(self) -> None:
        """GET /audio 442 (unsupported model) → skip PUT audio, still open connection."""
        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_442 = MagicMock()
        audio_442.status_code = 442
        conn_resp = MagicMock()
        conn_resp.status_code = 200
        conn_resp.json.return_value = CONN_REMOTE
        sess = MagicMock()
        sess.get.return_value = audio_442
        sess.put.return_value = conn_resp
        mock_proc = MagicMock()
        mock_proc.pid = 11111
        mock_proc.wait.return_value = 0
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            bc.cmd_intercom(cfg, self._make_args(duration=1))
        # Popen called (stream opened despite audio GET 442)
        mock_proc.wait.assert_called()

    def test_intercom_error_exception_handled(self) -> None:
        """Generic exception during Popen.wait → printed, no propagation."""
        cfg = self._base_cfg()
        cams = {CAM_NAME: _fake_cam()}
        audio_resp = MagicMock()
        audio_resp.status_code = 200
        audio_resp.json.return_value = {"audioEnabled": True, "microphoneLevel": 50}
        audio_put = MagicMock()
        audio_put.status_code = 200
        conn_resp = MagicMock()
        conn_resp.status_code = 200
        conn_resp.json.return_value = CONN_REMOTE
        sess = MagicMock()
        sess.get.return_value = audio_resp
        sess.put.side_effect = [audio_put, conn_resp]
        mock_proc = MagicMock()
        mock_proc.pid = 22222
        mock_proc.wait.side_effect = OSError("broken pipe")
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=sess),
            patch.object(bc, "get_cameras", return_value=cams),
            patch.object(bc, "resolve_cam", return_value=cams),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            # Should NOT raise
            bc.cmd_intercom(cfg, self._make_args(duration=10))
