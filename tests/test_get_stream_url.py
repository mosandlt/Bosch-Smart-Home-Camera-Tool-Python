"""Tests for bosch_camera.get_stream_url — the shared RTSP(S) stream-URL accessor
behind the CLI `live` command and the NiceGUI frontend's WebRTC player.

Pins the URL construction for LOCAL (rtsp:// + embedded Digest creds) and REMOTE
(rtsps:// proxy) plus the candidate fallback + 401 token refresh.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import bosch_camera

_RTSP_PARAMS = "inst=2&enableaudio=1&fmtp=1&maxSessionDuration=3600"


def _resp(status: int = 200, data: dict[str, Any] | None = None) -> MagicMock:
    m = MagicMock(status_code=status)
    m.json.return_value = data or {}
    return m


class TestGetStreamUrlLocal:
    def test_local_builds_rtsp_with_encoded_creds(self) -> None:
        data = {"urls": ["192.168.2.50:443"], "user": "usr", "password": "p@ss"}
        with patch.object(bosch_camera.requests, "put", return_value=_resp(200, data)):
            r = bosch_camera.get_stream_url({"id": "cam1"}, "tok", conn_type="LOCAL")
        assert r is not None
        assert r["type"] == "LOCAL"
        # creds URL-encoded (@ -> %40), embedded before host
        assert r["url"] == f"rtsp://usr:p%40ss@192.168.2.50:443/rtsp_tunnel?{_RTSP_PARAMS}"

    def test_hq_selects_main_stream_inst1(self) -> None:
        data = {"urls": ["192.168.2.50:443"], "user": "", "password": ""}
        with patch.object(bosch_camera.requests, "put", return_value=_resp(200, data)):
            r = bosch_camera.get_stream_url(
                {"id": "cam1"}, "tok", hq=True, conn_type="LOCAL"
            )
        assert r is not None
        # hq=True → inst=1 (main stream); sub-stream params stay inst=2
        assert "inst=1&" in r["url"]
        assert "inst=2&" not in r["url"]

    def test_local_without_creds(self) -> None:
        data = {"urls": ["192.168.2.50:443"], "user": "", "password": ""}
        with patch.object(bosch_camera.requests, "put", return_value=_resp(200, data)):
            r = bosch_camera.get_stream_url({"id": "cam1"}, "tok", conn_type="LOCAL")
        assert r is not None
        assert r["url"] == f"rtsp://192.168.2.50:443/rtsp_tunnel?{_RTSP_PARAMS}"


class TestGetStreamUrlRemote:
    def test_remote_builds_rtsps_proxy(self) -> None:
        data = {
            "urls": ["proxy-7.live.cbs.boschsecurity.com:42090/abc123hash"],
            "user": "u",
            "password": "p",
        }
        with patch.object(bosch_camera.requests, "put", return_value=_resp(200, data)):
            r = bosch_camera.get_stream_url({"id": "cam1"}, "tok", conn_type="REMOTE")
        assert r is not None
        assert r["type"] == "REMOTE"
        assert r["url"] == (
            f"rtsps://proxy-7.live.cbs.boschsecurity.com:443/abc123hash"
            f"/rtsp_tunnel?{_RTSP_PARAMS}"
        )


class TestGetStreamUrlGuards:
    def test_no_id_returns_none(self) -> None:
        assert bosch_camera.get_stream_url({}, "tok") is None

    def test_non_200_returns_none(self) -> None:
        with patch.object(bosch_camera.requests, "put", return_value=_resp(500, {})):
            assert (
                bosch_camera.get_stream_url({"id": "c"}, "tok", conn_type="LOCAL")
                is None
            )

    def test_empty_urls_returns_none(self) -> None:
        with patch.object(
            bosch_camera.requests, "put", return_value=_resp(200, {"urls": []})
        ):
            assert (
                bosch_camera.get_stream_url({"id": "c"}, "tok", conn_type="LOCAL")
                is None
            )


class TestGetStreamUrlCandidateLoop:
    def test_falls_back_remote_then_local(self) -> None:
        # Default order is REMOTE, LOCAL — REMOTE fails (500), LOCAL succeeds.
        local_ok = _resp(200, {"urls": ["192.168.0.9:443"], "user": "", "password": ""})
        with patch.object(
            bosch_camera.requests, "put", side_effect=[_resp(500, {}), local_ok]
        ):
            r = bosch_camera.get_stream_url({"id": "c"}, "tok")
        assert r is not None
        assert r["type"] == "LOCAL"
        assert r["url"].startswith("rtsp://192.168.0.9:443/")

    def test_all_fail_returns_none(self) -> None:
        with patch.object(
            bosch_camera.requests, "put", side_effect=[_resp(500, {}), _resp(500, {})]
        ):
            assert bosch_camera.get_stream_url({"id": "c"}, "tok") is None


class TestGetStreamUrl401Refresh:
    def test_401_then_refresh_succeeds(self) -> None:
        ok = _resp(200, {"urls": ["192.168.0.9:443"], "user": "u", "password": "p"})
        with (
            patch.object(
                bosch_camera.requests, "put", side_effect=[_resp(401, {}), ok]
            ),
            patch.object(bosch_camera, "get_token", return_value="fresh-token"),
            patch.object(bosch_camera, "make_session"),
        ):
            r = bosch_camera.get_stream_url(
                {"id": "c"}, "old", conn_type="LOCAL", cfg={"account": {}}
            )
        assert r is not None
        assert r["type"] == "LOCAL"
