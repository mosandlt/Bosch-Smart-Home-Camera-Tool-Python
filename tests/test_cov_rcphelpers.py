"""
Coverage tests for RCP helper functions and _send_signal_alert / cmd_friends leftover branches.

Targets (previously uncovered):
  rcp_session        (4722-4775)  — session handshake paths
  rcp_session_cached (4784-4793)  — cache hit / miss / expired
  rcp_read           (4809-4837)  — success, HTTP error, network exc, malformed XML
  _send_signal_alert (3053-3083)  — success, failure, image attach, image-dl failure
  cmd_friends        (6134-6135, 6149, 6172-6176, 6195-6202, 6219-6226, 6239-6243,
                      6257-6258)  — 444-except branches + share-no-cam-found

FAKE IDs only: UUID AABBCCDD-…, MAC aa:bb:cc:…, IPs 192.0.2.x
PIN_EVERY_MODE applied throughout.
"""

from __future__ import annotations

import argparse
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    _send_signal_alert,
    cmd_friends,
    rcp_read,
    rcp_session,
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
# rcp_session
# ─────────────────────────────────────────────────────────────────────────────


class TestRcpSession:
    """Unit tests for rcp_session() — session handshake via cloud proxy."""

    def _hello_resp(self, sessionid: str = SESSION_ID) -> MagicMock:
        """Fake HELLO response with <sessionid> element."""
        return MagicMock(
            status_code=200,
            text=f"<reply><sessionid>{sessionid}</sessionid></reply>",
        )

    def _hello_resp_str_field(self, hex_payload: str) -> MagicMock:
        """Fake HELLO response using <str>HEX</str> fallback format."""
        return MagicMock(status_code=200, text=f"<reply><str>{hex_payload}</str></reply>")

    def _init_resp(self) -> MagicMock:
        return MagicMock(status_code=200, text="<reply><ok/></reply>")

    def test_success_via_sessionid_element(self) -> None:
        """Normal path: HELLO returns <sessionid>, SESSION_INIT succeeds."""
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud",
            side_effect=[self._hello_resp(), self._init_resp()],
        ):
            result = rcp_session(PROXY_BASE)
        assert result == SESSION_ID

    def test_success_via_str_hex_fallback(self) -> None:
        """HELLO returns <str>HEX</str> encoding containing 0xNN bytes → sessionid extracted."""
        # Encode "0x1a2b3c4d" as hex inside <str>
        inner = "0x1a2b3c4d".encode()
        hex_val = inner.hex()
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud",
            side_effect=[self._hello_resp_str_field(hex_val), self._init_resp()],
        ):
            result = rcp_session(PROXY_BASE)
        assert result == "0x1a2b3c4d"

    def test_hello_http_error_raises(self) -> None:
        """Non-200 HELLO response → RuntimeError raised."""
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud",
            return_value=MagicMock(status_code=503, text="err"),
        ):
            with pytest.raises(RuntimeError, match="RCP HELLO returned HTTP 503"):
                rcp_session(PROXY_BASE)

    def test_hello_no_sessionid_no_str_raises(self) -> None:
        """HELLO response has neither <sessionid> nor <str> → RuntimeError."""
        bad = MagicMock(status_code=200, text="<reply><err>1</err></reply>")
        with patch.object(bosch_camera, "requests_get_bosch_cloud", return_value=bad):
            with pytest.raises(RuntimeError, match="No sessionid in HELLO response"):
                rcp_session(PROXY_BASE)

    def test_hello_str_field_but_no_0x_pattern_raises(self) -> None:
        """HELLO <str> present but bytes don't contain 0x pattern → RuntimeError."""
        # Encode something that has no 0x prefix after decoding
        inner = b"justgarbage"
        hex_val = inner.hex()
        bad = MagicMock(status_code=200, text=f"<reply><str>{hex_val}</str></reply>")
        with patch.object(bosch_camera, "requests_get_bosch_cloud", return_value=bad):
            with pytest.raises(RuntimeError, match="Cannot parse sessionid"):
                rcp_session(PROXY_BASE)

    def test_session_init_http_error_raises(self) -> None:
        """SESSION_INIT (0xff0d) returns non-200 → RuntimeError raised."""
        bad_init = MagicMock(status_code=401, text="Unauthorized")
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud",
            side_effect=[self._hello_resp(), bad_init],
        ):
            with pytest.raises(RuntimeError, match="RCP SESSION_INIT returned HTTP 401"):
                rcp_session(PROXY_BASE)


# ─────────────────────────────────────────────────────────────────────────────
# rcp_session_cached
# ─────────────────────────────────────────────────────────────────────────────


class TestRcpSessionCached:
    """Unit tests for rcp_session_cached() — TTL-based cache."""

    def setup_method(self) -> None:
        """Clear the session cache before each test."""
        bosch_camera._RCP_SESSION_CACHE.clear()  # type: ignore[attr-defined]

    def test_cache_miss_calls_rcp_session(self) -> None:
        """Cold cache → rcp_session() is called and result is stored."""
        with patch.object(bosch_camera, "rcp_session", return_value=SESSION_ID) as mock_sess:
            result = rcp_session_cached(PROXY_BASE)
        assert result == SESSION_ID
        mock_sess.assert_called_once_with(PROXY_BASE)

    def test_cache_hit_returns_cached_session(self) -> None:
        """Warm valid cache → rcp_session() NOT called again."""
        future = time.time() + 200.0
        bosch_camera._RCP_SESSION_CACHE[PROXY_BASE] = (SESSION_ID, future)  # type: ignore[attr-defined]
        with patch.object(bosch_camera, "rcp_session", return_value="0xnew") as mock_sess:
            result = rcp_session_cached(PROXY_BASE)
        assert result == SESSION_ID
        mock_sess.assert_not_called()

    def test_expired_cache_evicted_and_refreshed(self) -> None:
        """Expired cache entry → evicted and rcp_session() called again."""
        past = time.time() - 10.0
        bosch_camera._RCP_SESSION_CACHE[PROXY_BASE] = (SESSION_ID, past)  # type: ignore[attr-defined]
        new_sid = "0xcafebabe"
        with patch.object(bosch_camera, "rcp_session", return_value=new_sid):
            result = rcp_session_cached(PROXY_BASE)
        assert result == new_sid
        assert PROXY_BASE in bosch_camera._RCP_SESSION_CACHE  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# rcp_read
# ─────────────────────────────────────────────────────────────────────────────


class TestRcpRead:
    """Unit tests for rcp_read() — RCP READ request and XML parsing."""

    def _ok(self, hex_str: str) -> MagicMock:
        return MagicMock(status_code=200, text=f"<reply><str>{hex_str}</str></reply>")

    def test_success_returns_bytes(self) -> None:
        """Valid <str>HEXHEX</str> → bytes returned."""
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud", return_value=self._ok("deadbeef")
        ):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result == b"\xde\xad\xbe\xef"

    def test_http_error_returns_none(self) -> None:
        """Non-200 status → None returned."""
        bad = MagicMock(status_code=403, text="Forbidden")
        with patch.object(bosch_camera, "requests_get_bosch_cloud", return_value=bad):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_network_exception_returns_none(self) -> None:
        """Connection error → None returned (no exception propagated)."""
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud",
            side_effect=ConnectionError("timeout"),
        ):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_no_str_tag_returns_none(self) -> None:
        """Response without <str> tag → None."""
        bad = MagicMock(status_code=200, text="<reply><err>1</err></reply>")
        with patch.object(bosch_camera, "requests_get_bosch_cloud", return_value=bad):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_empty_str_tag_returns_none(self) -> None:
        """<str></str> (empty payload) → None (len=0)."""
        empty = MagicMock(status_code=200, text="<reply><str></str></reply>")
        with patch.object(bosch_camera, "requests_get_bosch_cloud", return_value=empty):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_malformed_hex_in_str_returns_none(self) -> None:
        """<str> contains invalid hex (odd length or non-hex chars) → None."""
        bad_hex = MagicMock(status_code=200, text="<reply><str>GGGG</str></reply>")
        with patch.object(bosch_camera, "requests_get_bosch_cloud", return_value=bad_hex):
            result = rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_type_and_num_forwarded(self) -> None:
        """type_ and num parameters are passed in the request params."""
        ok = self._ok("aabb")
        with patch.object(
            bosch_camera, "requests_get_bosch_cloud", return_value=ok
        ) as mock_get:
            rcp_read(RCP_URL, "0x0c22", SESSION_ID, type_="T_WORD", num=2)
        call_params = mock_get.call_args[1]["params"]
        assert call_params["type"] == "T_WORD"
        assert call_params["num"] == 2


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
        with patch.object(
            bosch_camera.requests, "post", side_effect=ConnectionError("refused")
        ):
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

        with patch.object(bosch_camera.requests, "get", return_value=img_resp), \
             patch.object(bosch_camera.requests, "post", side_effect=_fake_post):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS,
                "Terrasse", "PERSON", "16:00",
                image_url="http://192.0.2.1/snap.jpg", token="faketoken",
            )
        assert "base64_attachments" in captured_body
        assert len(captured_body["base64_attachments"]) == 1

    def test_image_download_failure_still_sends(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """image download raises exception → warning printed but signal still sent."""
        post_resp = MagicMock(status_code=200, text="")

        with patch.object(
            bosch_camera.requests, "get", side_effect=ConnectionError("img unreachable")
        ), patch.object(bosch_camera.requests, "post", return_value=post_resp):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS,
                "Terrasse", "MOVEMENT", "17:00",
                image_url="http://192.0.2.2/snap.jpg", token="faketoken",
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

        with patch.object(bosch_camera.requests, "get", return_value=non_img), \
             patch.object(bosch_camera.requests, "post", side_effect=_fake_post):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS,
                "Terrasse", "PERSON", "18:00",
                image_url="http://192.0.2.3/snap.jpg", token="faketoken",
            )
        assert "base64_attachments" not in captured_body

    def test_no_image_when_missing_url_or_token(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """image_url or token absent → GET never called."""
        post_resp = MagicMock(status_code=200, text="")
        with patch.object(bosch_camera.requests, "get") as mock_get, \
             patch.object(bosch_camera.requests, "post", return_value=post_resp):
            _send_signal_alert(
                SIGNAL_URL, SIGNAL_SENDER, SIGNAL_RECIPIENTS,
                "Terrasse", "MOVEMENT", "19:00",
                image_url="", token="",
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
    def test_invite_444_json_raises_prints_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """invite 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("post", self._444_resp_json_raises())
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args(sub="invite", sub_arg=FRIEND_EMAIL))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # share 444-except (lines 6172-6176)
    def test_share_444_json_raises_prints_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """share 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("put", self._444_resp_json_raises())
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam=CAM_NAME),
            )
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # unshare 444-except (lines 6195-6202)
    def test_unshare_444_json_raises_prints_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """unshare 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("put", self._444_resp_json_raises())
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args(sub="unshare", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # resend 444-except (lines 6219-6226)
    def test_resend_444_json_raises_prints_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """resend 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("put", self._444_resp_json_raises())
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args(sub="resend", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # remove 444-except (lines 6239-6243)
    def test_remove_444_json_raises_prints_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """remove 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("delete", self._444_resp_json_raises())
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args(sub="remove", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()

    # list 444-except (lines 6257-6258)
    def test_list_444_json_raises_prints_text(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """list 444 + json() raises → r.text printed instead."""
        sess = self._sess_with_method("get", self._444_resp_json_raises())
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "raw-offline-text" in out or "offline" in out.lower()


class TestCmdFriendsErrorBranches:
    """Cover the non-200/non-444 HTTP error branches for unshare and resend (lines 6202, 6226)."""

    def test_unshare_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """unshare 500 → HTTP status printed."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500, text="server error")
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args(sub="unshare", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "500" in out

    def test_resend_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """resend 500 → HTTP status printed."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=500, text="server error")
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"):
            cmd_friends(_make_cfg(), _args(sub="resend", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "500" in out


class TestCmdFriendsShareNoCamFound:
    """share sub with a camera name that resolves to empty dict → early return (line 6149)."""

    def test_share_unknown_cam_returns_early(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """resolve_cam returns {} → function returns without calling session.put."""
        sess = MagicMock()
        with patch.object(bosch_camera, "get_token", return_value="tok"), \
             patch.object(bosch_camera, "make_session", return_value=sess), \
             patch.object(bosch_camera, "get_cameras"), \
             patch.object(bosch_camera, "resolve_cam", return_value={}):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam="NonExistent"),
            )
        sess.put.assert_not_called()
