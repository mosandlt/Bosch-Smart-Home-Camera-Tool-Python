"""
Unit tests for bosch_rcp_client.py — the thin sync wrappers around the
bosch-shc-camera-client library's async RCP session/read/LOCAL-write
implementation.

Each wrapper is verified for: (1) it forwards arguments correctly to the
library's async function, (2) it returns the library's result unchanged,
(3) the asyncio.run()/aiohttp.ClientSession boundary doesn't leak or crash
for both success and None/False (failure) outcomes.

The library's own protocol/parsing correctness (HTTP error handling, XML
parsing, digest auth, etc.) is out of scope here — that's covered by the
published bosch-shc-camera-client package's own test suite. These tests only
pin *this* CLI's integration with it.

FAKE IDs only: proxy hash 'fakehash', IPs 192.0.2.x (RFC 5737 TEST-NET-1).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import bosch_rcp_client

PROXY_BASE = "https://proxy-01.live.cbs.boschsecurity.com:42090/fakehash"
PROXY_HOST = "proxy-01.live.cbs.boschsecurity.com:42090"
PROXY_HASH = "fakehash"
RCP_URL = f"{PROXY_BASE}/rcp.xml"
SESSION_ID = "0xdeadbeef"
CAM_IP = "192.0.2.1"


# ─────────────────────────────────────────────────────────────────────────────
# split_proxy_base
# ─────────────────────────────────────────────────────────────────────────────


class TestSplitProxyBase:
    def test_splits_host_and_hash(self) -> None:
        host, hash_ = bosch_rcp_client.split_proxy_base(PROXY_BASE)
        assert host == PROXY_HOST
        assert hash_ == PROXY_HASH

    def test_strips_https_scheme(self) -> None:
        host, _ = bosch_rcp_client.split_proxy_base("https://host:1/hash")
        assert host == "host:1"

    def test_no_scheme_still_splits(self) -> None:
        host, hash_ = bosch_rcp_client.split_proxy_base("host:1/hash")
        assert host == "host:1"
        assert hash_ == "hash"


# ─────────────────────────────────────────────────────────────────────────────
# get_session_id
# ─────────────────────────────────────────────────────────────────────────────


class TestGetSessionId:
    def test_success_returns_session_id(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "get_cached_rcp_session",
            AsyncMock(return_value=SESSION_ID),
        ) as mock_get:
            result = bosch_rcp_client.get_session_id(PROXY_BASE)
        assert result == SESSION_ID
        # ssl_context, session_cache, proxy_host, proxy_hash
        args = mock_get.call_args.args
        assert args[2] == PROXY_HOST
        assert args[3] == PROXY_HASH
        assert args[1] is bosch_rcp_client._RCP_SESSION_CACHE

    def test_failure_returns_none(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "get_cached_rcp_session",
            AsyncMock(return_value=None),
        ):
            result = bosch_rcp_client.get_session_id(PROXY_BASE)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# rcp_read
# ─────────────────────────────────────────────────────────────────────────────


class TestRcpReadWrapper:
    def test_success_returns_bytes(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "rcp_read",
            AsyncMock(return_value=b"\xde\xad\xbe\xef"),
        ):
            result = bosch_rcp_client.rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result == b"\xde\xad\xbe\xef"

    def test_failure_returns_none(self) -> None:
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_read", AsyncMock(return_value=None)):
            result = bosch_rcp_client.rcp_read(RCP_URL, "0x0a0f", SESSION_ID)
        assert result is None

    def test_forwards_command_type_num_sessionid(self) -> None:
        mock_read = AsyncMock(return_value=b"\xaa")
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_read", mock_read):
            bosch_rcp_client.rcp_read(RCP_URL, "0x0c22", SESSION_ID, type_="T_WORD", num=2)
        _, called_url, called_cmd, called_sid = mock_read.call_args.args
        assert called_url == RCP_URL
        assert called_cmd == "0x0c22"
        assert called_sid == SESSION_ID
        assert mock_read.call_args.kwargs["type_"] == "T_WORD"
        assert mock_read.call_args.kwargs["num"] == 2
        assert mock_read.call_args.kwargs["session_cache"] is bosch_rcp_client._RCP_SESSION_CACHE

    def test_defaults_type_octet_num_zero(self) -> None:
        mock_read = AsyncMock(return_value=b"\xaa")
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_read", mock_read):
            bosch_rcp_client.rcp_read(RCP_URL, "0x0d00", SESSION_ID)
        assert mock_read.call_args.kwargs["type_"] == "P_OCTET"
        assert mock_read.call_args.kwargs["num"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# lan_write_privacy
# ─────────────────────────────────────────────────────────────────────────────


class TestLanWritePrivacy:
    def test_success_true(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "rcp_local_write_privacy",
            AsyncMock(return_value=True),
        ):
            result = bosch_rcp_client.lan_write_privacy(CAM_IP, True)
        assert result is True

    def test_failure_false(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "rcp_local_write_privacy",
            AsyncMock(return_value=False),
        ):
            result = bosch_rcp_client.lan_write_privacy(CAM_IP, True)
        assert result is False

    def test_forwards_enabled_and_cam_ip(self) -> None:
        mock_write = AsyncMock(return_value=True)
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_local_write_privacy", mock_write):
            bosch_rcp_client.lan_write_privacy(CAM_IP, False)
        _, called_ip, called_enabled = mock_write.call_args.args
        assert called_ip == CAM_IP
        assert called_enabled is False

    def test_no_creds_forwards_none(self) -> None:
        """Empty user/password strings become None (library's unauthenticated fallback)."""
        mock_write = AsyncMock(return_value=True)
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_local_write_privacy", mock_write):
            bosch_rcp_client.lan_write_privacy(CAM_IP, True)
        assert mock_write.call_args.kwargs["user"] is None
        assert mock_write.call_args.kwargs["password"] is None

    def test_creds_forwarded_when_given(self) -> None:
        mock_write = AsyncMock(return_value=True)
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_local_write_privacy", mock_write):
            bosch_rcp_client.lan_write_privacy(
                CAM_IP, True, user="cbs-fakeuser", password="fakepass"
            )
        assert mock_write.call_args.kwargs["user"] == "cbs-fakeuser"
        assert mock_write.call_args.kwargs["password"] == "fakepass"


# ─────────────────────────────────────────────────────────────────────────────
# lan_write_front_light
# ─────────────────────────────────────────────────────────────────────────────


class TestLanWriteFrontLight:
    def test_success_true(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "rcp_local_write_front_light",
            AsyncMock(return_value=True),
        ):
            result = bosch_rcp_client.lan_write_front_light(CAM_IP, 75)
        assert result is True

    def test_failure_false(self) -> None:
        with patch.object(
            bosch_rcp_client._rcp_lib,
            "rcp_local_write_front_light",
            AsyncMock(return_value=False),
        ):
            result = bosch_rcp_client.lan_write_front_light(CAM_IP, 75)
        assert result is False

    def test_forwards_brightness(self) -> None:
        mock_write = AsyncMock(return_value=True)
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_local_write_front_light", mock_write):
            bosch_rcp_client.lan_write_front_light(CAM_IP, 42)
        _, called_ip, called_brightness = mock_write.call_args.args
        assert called_ip == CAM_IP
        assert called_brightness == 42

    def test_creds_forwarded_when_given(self) -> None:
        mock_write = AsyncMock(return_value=True)
        with patch.object(bosch_rcp_client._rcp_lib, "rcp_local_write_front_light", mock_write):
            bosch_rcp_client.lan_write_front_light(
                CAM_IP, 100, user="cbs-fakeuser", password="fakepass"
            )
        assert mock_write.call_args.kwargs["user"] == "cbs-fakeuser"
        assert mock_write.call_args.kwargs["password"] == "fakepass"
