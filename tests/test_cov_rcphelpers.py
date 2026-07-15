"""
Coverage tests for RCP helper functions and _send_signal_alert / cmd_friends leftover branches.

Targets (previously uncovered):
  rcp_session_cached / rcp_read — thin sync wrappers around bosch_rcp_client
    (bosch-shc-camera-client library, RCP session handshake + READ). Mocked
    at the bosch_rcp_client boundary (module-level asyncio.run() wrappers),
    not at the deleted internal requests_get_bosch_cloud-based
    implementation — see bosch_rcp_client.py for the actual protocol code.
  _send_signal_alert (3053-3083)  — success, failure, image attach, image-dl failure
  cmd_friends        (6134-6135, 6149, 6172-6176, 6195-6202, 6219-6226, 6239-6243,
                      6257-6258)  — 444-except branches + share-no-cam-found

FAKE IDs only: UUID AABBCCDD-…, MAC aa:bb:cc:…, IPs 192.0.2.x
PIN_EVERY_MODE applied throughout.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
import bosch_rcp_client
from bosch_camera import (
    _send_signal_alert,
    cmd_friends,
    rcp_read,
    rcp_session_cached,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants / fake IDs (NEVER real values)
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
PROXY_BASE = "https://proxy-01.live.cbs.boschsecurity.com:42090/fakehash"
RCP_URL = f"{PROXY_BASE}/rcp.xml"
SESSION_ID = "0xdeadbeef"
FRIEND_ID = "BBBBCCCC-1111-2222-3333-444455556666"
FRIEND_EMAIL = "friend@example.com"
SIGNAL_URL = "http://192.0.2.99:8080"
SIGNAL_SENDER = "+49000000000"
SIGNAL_RECIPIENTS = ["+49111111111"]


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "sub": None,
        "sub_arg": None,
        "share_cam": None,
        "days": None,
        "token": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


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


def _ok_resp(status: int = 200, json_data: Any = None, text: str = "") -> MagicMock:
    return MagicMock(status_code=status, json=lambda: json_data or {}, text=text)


def _err_resp(status: int, text: str = "error") -> MagicMock:
    return MagicMock(status_code=status, json=lambda: {}, text=text)


# ─────────────────────────────────────────────────────────────────────────────
# rcp_session_cached — bosch_camera's thin wrapper around
# bosch_rcp_client.get_session_id() (bosch-shc-camera-client library).
# The library's own 5-min-TTL cache dict lives at bosch_rcp_client
# .._RCP_SESSION_CACHE now (moved from bosch_camera.py), so cache
# hit/miss/expiry behavior is exercised through get_session_id() directly —
# see test_bosch_rcp_client.py for that. Here we only verify
# rcp_session_cached()'s own contract: pass the sessionid through on
# success, raise RuntimeError on None.
# ─────────────────────────────────────────────────────────────────────────────


class TestRcpSessionCached:
    """Unit tests for rcp_session_cached() — thin wrapper, raises on failure."""

    def test_success_returns_session_id(self) -> None:
        """get_session_id() returns a sessionid → passed through unchanged."""
        with patch.object(bosch_rcp_client, "get_session_id", return_value=SESSION_ID) as mock_get:
            result = rcp_session_cached(PROXY_BASE)
        assert result == SESSION_ID
        mock_get.assert_called_once_with(PROXY_BASE)

    def test_failure_raises_runtime_error(self) -> None:
        """get_session_id() returns None (handshake failed) → RuntimeError raised."""
        with patch.object(bosch_rcp_client, "get_session_id", return_value=None):
            with pytest.raises(RuntimeError, match="RCP session handshake failed"):
                rcp_session_cached(PROXY_BASE)


# ─────────────────────────────────────────────────────────────────────────────
# rcp_read — bosch_camera's thin wrapper around bosch_rcp_client.rcp_read()
# (bosch-shc-camera-client library). Protocol-level success/error-path
# coverage (HTTP errors, malformed XML, <err> tags, etc.) lives in the
# library's own test suite; here we verify the wrapper forwards args and
# the return value correctly.
# ─────────────────────────────────────────────────────────────────────────────


class TestRcpRead:
    """Unit tests for rcp_read() — thin wrapper around bosch_rcp_client.rcp_read()."""

    def test_success_returns_bytes(self) -> None:
        """bosch_rcp_client.rcp_read() result is passed through unchanged."""
        with patch.object(bosch_rcp_client, "rcp_read", return_value=b"\xde\xad\xbe\xef"):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result == b"\xde\xad\xbe\xef"

    def test_failure_returns_none(self) -> None:
        """None from bosch_rcp_client.rcp_read() (any failure mode) → None."""
        with patch.object(bosch_rcp_client, "rcp_read", return_value=None):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_type_and_num_forwarded(self) -> None:
        """type_ and num parameters are forwarded to bosch_rcp_client.rcp_read()."""
        with patch.object(bosch_rcp_client, "rcp_read", return_value=b"\xaa\xbb") as mock_read:
            rcp_read(RCP_URL, "0x0c22", SESSION_ID, type_="T_WORD", num=2)
        mock_read.assert_called_once_with(RCP_URL, "0x0c22", SESSION_ID, type_="T_WORD", num=2)

    def test_defaults_forwarded(self) -> None:
        """Default type_/num ('P_OCTET', 0) are forwarded when not overridden."""
        with patch.object(bosch_rcp_client, "rcp_read", return_value=b"\x01") as mock_read:
            rcp_read(RCP_URL, "0x0d00", SESSION_ID)
        mock_read.assert_called_once_with(RCP_URL, "0x0d00", SESSION_ID, type_="P_OCTET", num=0)


# ─────────────────────────────────────────────────────────────────────────────
# _send_signal_alert
# ─────────────────────────────────────────────────────────────────────────────


class TestSendSignalAlert:
    """Unit tests for _send_signal_alert() — Signal webhook delivery."""

    def test_success_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """POST returns 200 → 'Signal alert sent' printed."""
        ok = MagicMock(status_code=200, text="")
        with patch.object(bosch_camera.requests, "post", return_value=ok):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS, "Terrasse", "MOVEMENT", "12:00"
            )
        out = capsys.readouterr().out
        assert "Signal alert sent" in out

    def test_success_201(self, capsys: pytest.CaptureFixture[str]) -> None:
        """POST returns 201 → 'Signal alert sent' printed (201 is accepted)."""
        ok = MagicMock(status_code=201, text="")
        with patch.object(bosch_camera.requests, "post", return_value=ok):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS, "Terrasse", "PERSON", "13:00"
            )
        out = capsys.readouterr().out
        assert "Signal alert sent" in out

    def test_http_error_prints_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """POST returns 500 → warning with HTTP status printed."""
        err = MagicMock(status_code=500, text="Internal Server Error")
        with patch.object(bosch_camera.requests, "post", return_value=err):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS, "Terrasse", "MOVEMENT", "14:00"
            )
        out = capsys.readouterr().out
        assert "500" in out or "failed" in out.lower() or "warning" in out.lower()

    def test_network_exception_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """POST raises ConnectionError → error line printed, no crash."""
        with patch.object(bosch_camera.requests, "post", side_effect=ConnectionError("refused")):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS, "Terrasse", "MOVEMENT", "15:00"
            )
        out = capsys.readouterr().out
        assert "error" in out.lower() or "refused" in out.lower()

    def test_image_attached_when_url_and_token_present(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """image_url + token → image download attempted and base64 attached."""
        img_resp = MagicMock(
            status_code=200,
            headers={"Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xff\xe0testjpeg",
        )
        post_resp = MagicMock(status_code=200, text="")
        captured_body: dict[str, Any] = {}

        def _fake_post(url: str, json: Any = None, **kw: Any) -> MagicMock:
            captured_body.update(json or {})
            return post_resp

        with (
            patch.object(bosch_camera.requests, "get", return_value=img_resp),
            patch.object(bosch_camera.requests, "post", side_effect=_fake_post),
        ):
            _send_signal_alert(
                SIGNAL_URL,
                SIGNAL_SENDER,
                SIGNAL_RECIPIENTS,
                "Terrasse",
                "PERSON",
                "16:00",
                image_url="http://192.0.2.1/snap.jpg",
                token="faketoken",
            )
        assert "base64_attachments" in captured_body
        assert len(captured_body["base64_attachments"]) == 1

    def test_image_download_failure_still_sends(self, capsys: pytest.CaptureFixture[str]) -> None:
        """image download raises exception → warning printed but signal still sent."""
        post_resp = MagicMock(status_code=200, text="")

        with (
            patch.object(
                bosch_camera.requests, "get", side_effect=ConnectionError("img unreachable")
            ),
            patch.object(bosch_camera.requests, "post", return_value=post_resp),
        ):
            _send_signal_alert(
                SIGNAL_URL,
                SIGNAL_SENDER,
                SIGNAL_RECIPIENTS,
                "Terrasse",
                "MOVEMENT",
                "17:00",
                image_url="http://192.0.2.2/snap.jpg",
                token="faketoken",
            )
        out = capsys.readouterr().out
        # Image download warning printed
        assert "image download failed" in out.lower() or "warning" in out.lower()
        # Signal send still attempted
        assert "Signal alert sent" in out

    def test_image_not_attached_when_non_image_content_type(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GET returns 200 but Content-Type is not image/* → no base64_attachments."""
        non_img = MagicMock(
            status_code=200,
            headers={"Content-Type": "text/plain"},
            content=b"notanimage",
        )
        post_resp = MagicMock(status_code=200, text="")
        captured_body: dict[str, Any] = {}

        def _fake_post(url: str, json: Any = None, **kw: Any) -> MagicMock:
            captured_body.update(json or {})
            return post_resp

        with (
            patch.object(bosch_camera.requests, "get", return_value=non_img),
            patch.object(bosch_camera.requests, "post", side_effect=_fake_post),
        ):
            _send_signal_alert(
                SIGNAL_URL,
                SIGNAL_SENDER,
                SIGNAL_RECIPIENTS,
                "Terrasse",
                "PERSON",
                "18:00",
                image_url="http://192.0.2.3/snap.jpg",
                token="faketoken",
            )
        assert "base64_attachments" not in captured_body

    def test_no_image_when_missing_url_or_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        """image_url or token absent → GET never called."""
        post_resp = MagicMock(status_code=200, text="")
        with (
            patch.object(bosch_camera.requests, "get") as mock_get,
            patch.object(bosch_camera.requests, "post", return_value=post_resp),
        ):
            _send_signal_alert(
                SIGNAL_URL,
                SIGNAL_SENDER,
                SIGNAL_RECIPIENTS,
                "Terrasse",
                "MOVEMENT",
                "19:00",
                image_url="",
                token="",
            )
        mock_get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — leftover 444-except branches + share-no-cam-found
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriends444ExceptBranches:
    """Cover the except-Exception branches inside every 444 handler in cmd_friends."""

    def _sess_with_method(self, method: str, resp: MagicMock) -> MagicMock:
        sess = MagicMock()
        getattr(sess, method).return_value = resp
        return sess

    def _444_resp_json_raises(self) -> MagicMock:
        """444 response where r.json() raises (covers except branch)."""
        r = MagicMock(status_code=444, text="raw-offline-text")
        r.json.side_effect = ValueError("No JSON")
        return r

    # invite 444-except (lines 6134-6135)
    def test_invite_444_json_raises_prints_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        """invite 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("post", self._444_resp_json_raises())
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="invite", sub_arg=FRIEND_EMAIL))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # share 444-except (lines 6172-6176)
    def test_share_444_json_raises_prints_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        """share 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("put", self._444_resp_json_raises())
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam=CAM_NAME),
            )
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # unshare 444-except (lines 6195-6202)
    def test_unshare_444_json_raises_prints_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        """unshare 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("put", self._444_resp_json_raises())
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="unshare", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # resend 444-except (lines 6219-6226)
    def test_resend_444_json_raises_prints_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        """resend 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("put", self._444_resp_json_raises())
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="resend", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # remove 444-except (lines 6239-6243)
    def test_remove_444_json_raises_prints_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        """remove 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("delete", self._444_resp_json_raises())
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="remove", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # list 444-except (lines 6257-6258)
    def test_list_444_json_raises_prints_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        """list 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("get", self._444_resp_json_raises())
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()


class TestCmdFriendsErrorBranches:
    """Cover the non-200/non-444 HTTP error branches for unshare and resend (lines 6202, 6226)."""

    def test_unshare_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """unshare 500 → HTTP status printed."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500, text="server error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="unshare", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "500" in out

    def test_resend_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """resend 500 → HTTP status printed."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500, text="server error")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="resend", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "500" in out


class TestCmdFriendsShareNoCamFound:
    """share sub with a camera name that resolves to empty dict → early return (line 6149)."""

    def test_share_unknown_cam_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        """resolve_cam returns {} → function returns without calling session.put."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
            patch.object(bosch_camera, "resolve_cam", return_value={}),
        ):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam="NonExistent"),
            )
        sess.put.assert_not_called()
