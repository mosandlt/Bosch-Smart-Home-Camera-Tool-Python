"""Regression tests for TOFU certificate fingerprint pinning (bosch_tls.py).

Introduced in security fix vX.X.X: bosch_tls.bosch_get / bosch_post / bosch_put
replace bare requests.get(verify=False, …) for LAN camera connections and add
trust-on-first-use (TOFU) fingerprint pinning to detect cert changes (rotation
or MITM) after the initial connection.

Test strategy:
- Unit-test _fetch_fingerprint against a real SSL socket (mocked).
- Test TOFU state machine: first_connect stores, match succeeds, mismatch raises.
- Test bosch_get / bosch_post / bosch_put call pin_or_verify then requests.
- Test cfg=None degrades to verify=False without raising.
- Test that non-https URLs skip pinning entirely.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from bosch_tls import (
    CertPinningError,
    _CFG_KEY,
    _fetch_fingerprint,
    bosch_get,
    bosch_post,
    bosch_put,
    clear_fingerprint,
    pin_or_verify,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_DER_A = b"fake-cert-der-bytes-camera-A"
_FAKE_DER_B = b"fake-cert-der-bytes-camera-B-rotated"
_FP_A = hashlib.sha256(_FAKE_DER_A).hexdigest()
_FP_B = hashlib.sha256(_FAKE_DER_B).hexdigest()


def _make_tls_socket(der_bytes: bytes) -> MagicMock:
    """Return a mock TLS socket whose getpeercert(binary_form=True) returns der_bytes."""
    tls_sock = MagicMock()
    tls_sock.getpeercert.return_value = der_bytes
    tls_sock.__enter__ = MagicMock(return_value=tls_sock)
    tls_sock.__exit__ = MagicMock(return_value=False)
    return tls_sock


def _mock_ssl_connect(der_bytes: bytes):
    """Context manager helper: patch socket.create_connection + ctx.wrap_socket."""
    raw_sock = MagicMock()
    raw_sock.__enter__ = MagicMock(return_value=raw_sock)
    raw_sock.__exit__ = MagicMock(return_value=False)

    tls_sock = _make_tls_socket(der_bytes)

    def _fake_wrap_socket(sock, server_hostname=None):
        return tls_sock

    return raw_sock, _fake_wrap_socket


# ---------------------------------------------------------------------------
# _fetch_fingerprint
# ---------------------------------------------------------------------------


class TestFetchFingerprint:
    def test_returns_sha256_hex_of_der_cert(self) -> None:
        """_fetch_fingerprint returns SHA-256 hex of the DER-encoded certificate."""
        raw_sock, fake_wrap = _mock_ssl_connect(_FAKE_DER_A)
        with (
            patch("bosch_tls.socket.create_connection", return_value=raw_sock),
            patch("bosch_tls.ssl.SSLContext") as mock_ctx_cls,
        ):
            mock_ctx = MagicMock()
            mock_ctx.wrap_socket = fake_wrap
            mock_ctx_cls.return_value = mock_ctx

            result = _fetch_fingerprint("192.168.1.100")

        assert result == _FP_A

    def test_uses_default_port_443(self) -> None:
        """_fetch_fingerprint connects to port 443 by default."""
        raw_sock, fake_wrap = _mock_ssl_connect(_FAKE_DER_A)
        captured_calls: list[tuple] = []

        def _fake_create_connection(addr, timeout=None):
            captured_calls.append(addr)
            return raw_sock

        with (
            patch("bosch_tls.socket.create_connection", side_effect=_fake_create_connection),
            patch("bosch_tls.ssl.SSLContext") as mock_ctx_cls,
        ):
            mock_ctx = MagicMock()
            mock_ctx.wrap_socket = fake_wrap
            mock_ctx_cls.return_value = mock_ctx

            _fetch_fingerprint("192.168.1.100")

        assert captured_calls[0] == ("192.168.1.100", 443)

    def test_raises_cert_pinning_error_on_empty_cert(self) -> None:
        """_fetch_fingerprint raises CertPinningError when no certificate is returned."""
        raw_sock, _ = _mock_ssl_connect(b"")
        tls_sock = _make_tls_socket(b"")
        tls_sock.getpeercert.return_value = b""

        def _fake_wrap(sock, server_hostname=None):
            return tls_sock

        with (
            patch("bosch_tls.socket.create_connection", return_value=raw_sock),
            patch("bosch_tls.ssl.SSLContext") as mock_ctx_cls,
        ):
            mock_ctx = MagicMock()
            mock_ctx.wrap_socket = _fake_wrap
            mock_ctx_cls.return_value = mock_ctx

            with pytest.raises(CertPinningError, match="No certificate received"):
                _fetch_fingerprint("192.168.1.100")

    def test_wraps_socket_errors_in_cert_pinning_error(self) -> None:
        """Network failures are wrapped in CertPinningError."""
        with patch(
            "bosch_tls.socket.create_connection", side_effect=OSError("Connection refused")
        ):
            with pytest.raises(CertPinningError, match="Could not fetch certificate"):
                _fetch_fingerprint("192.0.2.1")


# ---------------------------------------------------------------------------
# pin_or_verify — TOFU state machine
# ---------------------------------------------------------------------------


class TestPinOrVerify:
    def test_first_connect_stores_fingerprint(self) -> None:
        """First call for a host stores the fingerprint in cfg[_CFG_KEY][host]."""
        cfg: dict = {}
        with patch("bosch_tls._fetch_fingerprint", return_value=_FP_A):
            result = pin_or_verify("192.168.1.100", cfg=cfg)

        assert result is True
        assert cfg[_CFG_KEY]["192.168.1.100"] == _FP_A

    def test_subsequent_connect_with_matching_fingerprint_succeeds(self) -> None:
        """If stored fingerprint matches live fingerprint, call succeeds."""
        cfg = {_CFG_KEY: {"192.168.1.100": _FP_A}}
        with patch("bosch_tls._fetch_fingerprint", return_value=_FP_A):
            result = pin_or_verify("192.168.1.100", cfg=cfg)

        assert result is True

    def test_subsequent_connect_with_different_fingerprint_raises_cert_pinning_error(
        self,
    ) -> None:
        """If live fingerprint differs from stored, CertPinningError is raised."""
        cfg = {_CFG_KEY: {"192.168.1.100": _FP_A}}
        with patch("bosch_tls._fetch_fingerprint", return_value=_FP_B):
            with pytest.raises(CertPinningError, match="fingerprint mismatch"):
                pin_or_verify("192.168.1.100", cfg=cfg)

    def test_no_cfg_returns_true_without_storing(self) -> None:
        """When cfg=None, pin_or_verify returns True (degrades to legacy behaviour)."""
        result = pin_or_verify("192.168.1.100", cfg=None)
        assert result is True

    def test_different_hosts_stored_independently(self) -> None:
        """Each host has its own fingerprint entry in cfg."""
        cfg: dict = {}
        with patch("bosch_tls._fetch_fingerprint", return_value=_FP_A):
            pin_or_verify("192.168.1.100", cfg=cfg)
        with patch("bosch_tls._fetch_fingerprint", return_value=_FP_B):
            pin_or_verify("192.168.1.200", cfg=cfg)

        assert cfg[_CFG_KEY]["192.168.1.100"] == _FP_A
        assert cfg[_CFG_KEY]["192.168.1.200"] == _FP_B

    def test_cam_cert_fingerprints_key_created_if_missing(self) -> None:
        """cfg[_CFG_KEY] dict is created automatically if not present."""
        cfg: dict = {"account": {}, "cameras": {}}
        with patch("bosch_tls._fetch_fingerprint", return_value=_FP_A):
            pin_or_verify("10.0.0.1", cfg=cfg)

        assert _CFG_KEY in cfg


# ---------------------------------------------------------------------------
# clear_fingerprint
# ---------------------------------------------------------------------------


class TestClearFingerprint:
    def test_removes_stored_fingerprint(self) -> None:
        cfg = {_CFG_KEY: {"192.168.1.100": _FP_A}}
        removed = clear_fingerprint("192.168.1.100", cfg)
        assert removed is True
        assert "192.168.1.100" not in cfg.get(_CFG_KEY, {})

    def test_returns_false_when_no_fingerprint_stored(self) -> None:
        cfg: dict = {}
        removed = clear_fingerprint("192.168.1.100", cfg)
        assert removed is False


# ---------------------------------------------------------------------------
# bosch_get / bosch_post / bosch_put — call pin_or_verify then requests
# ---------------------------------------------------------------------------


class TestBoschGet:
    def test_https_url_calls_pin_or_verify(self) -> None:
        """bosch_get calls pin_or_verify for https:// URLs before the request."""
        cfg: dict = {}
        mock_response = MagicMock()
        mock_response.status_code = 200

        with (
            patch("bosch_tls.pin_or_verify", return_value=True) as mock_pin,
            patch("bosch_tls.requests.get", return_value=mock_response),
        ):
            bosch_get("https://192.168.1.100/snap.jpg", cfg=cfg, timeout=5)

        mock_pin.assert_called_once_with("192.168.1.100", 443, cfg)

    def test_http_url_skips_pin_or_verify(self) -> None:
        """bosch_get skips fingerprint check for http:// URLs."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with (
            patch("bosch_tls.pin_or_verify") as mock_pin,
            patch("bosch_tls.requests.get", return_value=mock_response),
        ):
            bosch_get("http://192.168.1.100/rcp.xml", cfg=None, timeout=5)

        mock_pin.assert_not_called()

    def test_cert_pinning_error_propagates(self) -> None:
        """CertPinningError from pin_or_verify propagates out of bosch_get."""
        with patch("bosch_tls.pin_or_verify", side_effect=CertPinningError("mismatch")):
            with pytest.raises(CertPinningError):
                bosch_get("https://192.168.1.100/snap.jpg", cfg={})

    def test_passes_verify_false_to_requests(self) -> None:
        """bosch_get passes verify=False to requests.get (cert CA not checked)."""
        captured_kwargs: list[dict] = []
        mock_response = MagicMock()

        def _fake_get(url, **kwargs):
            captured_kwargs.append(kwargs)
            return mock_response

        with (
            patch("bosch_tls.pin_or_verify", return_value=True),
            patch("bosch_tls.requests.get", side_effect=_fake_get),
        ):
            bosch_get("https://192.168.1.100/snap.jpg", cfg=None)

        assert captured_kwargs[0].get("verify") is False

    def test_none_cfg_calls_pin_or_verify_with_none(self) -> None:
        """bosch_get passes cfg=None to pin_or_verify when not provided."""
        mock_response = MagicMock()

        with (
            patch("bosch_tls.pin_or_verify", return_value=True) as mock_pin,
            patch("bosch_tls.requests.get", return_value=mock_response),
        ):
            bosch_get("https://192.168.1.100/snap.jpg")

        # cfg defaults to None
        mock_pin.assert_called_once_with("192.168.1.100", 443, None)


class TestBoschPost:
    def test_https_url_calls_pin_or_verify(self) -> None:
        mock_response = MagicMock()
        with (
            patch("bosch_tls.pin_or_verify", return_value=True) as mock_pin,
            patch("bosch_tls.requests.post", return_value=mock_response),
        ):
            bosch_post("https://192.168.1.100/api", cfg={})

        mock_pin.assert_called_once()

    def test_cert_pinning_error_propagates(self) -> None:
        with patch("bosch_tls.pin_or_verify", side_effect=CertPinningError("mismatch")):
            with pytest.raises(CertPinningError):
                bosch_post("https://192.168.1.100/api", cfg={})


class TestBoschPut:
    def test_https_url_calls_pin_or_verify(self) -> None:
        mock_response = MagicMock()
        with (
            patch("bosch_tls.pin_or_verify", return_value=True) as mock_pin,
            patch("bosch_tls.requests.put", return_value=mock_response),
        ):
            bosch_put("https://192.168.1.100/api", cfg={})

        mock_pin.assert_called_once()

    def test_cert_pinning_error_propagates(self) -> None:
        with patch("bosch_tls.pin_or_verify", side_effect=CertPinningError("mismatch")):
            with pytest.raises(CertPinningError):
                bosch_put("https://192.168.1.100/api", cfg={})
