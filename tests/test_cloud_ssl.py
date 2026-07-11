"""Regression tests for bosch_cloud_ssl (CWE-295 TLS-pinning fix).

Verifies that:
- build_bosch_cloud_ssl_context() returns an SSLContext with VERIFY_X509_PARTIAL_CHAIN
- get_bosch_cloud_ssl_context() caches and returns the same object
- _BoschCloudAdapter initialises its pool manager with the ssl_context kwarg
- make_bosch_cloud_session() mounts the adapter on https:// (not http://)
- requests_get/put/post_bosch_cloud strip any caller-supplied verify= kwarg
- The BOSCH_CLOUD_CA_PEM constant is well-formed PEM (parseable by ssl)
"""

from __future__ import annotations

import ssl
from unittest.mock import MagicMock, patch

import bosch_cloud_ssl
from bosch_cloud_ssl import (
    BOSCH_CLOUD_CA_PEM,
    _BoschCloudAdapter,
    build_bosch_cloud_ssl_context,
    get_bosch_cloud_ssl_context,
    make_bosch_cloud_session,
    requests_get_bosch_cloud,
    requests_post_bosch_cloud,
    requests_put_bosch_cloud,
)


# ---------------------------------------------------------------------------
# CA PEM constant
# ---------------------------------------------------------------------------


class TestBoschCloudCaPem:
    def test_pem_is_str(self) -> None:
        assert isinstance(BOSCH_CLOUD_CA_PEM, str)

    def test_pem_has_begin_marker(self) -> None:
        assert "-----BEGIN CERTIFICATE-----" in BOSCH_CLOUD_CA_PEM

    def test_pem_has_end_marker(self) -> None:
        assert "-----END CERTIFICATE-----" in BOSCH_CLOUD_CA_PEM

    def test_pem_parseable_by_ssl(self) -> None:
        """ssl.create_default_context() + load_verify_locations must not raise."""
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cadata=BOSCH_CLOUD_CA_PEM)  # raises on bad PEM


# ---------------------------------------------------------------------------
# build_bosch_cloud_ssl_context
# ---------------------------------------------------------------------------


class TestBuildBoschCloudSslContext:
    def test_returns_ssl_context(self) -> None:
        ctx = build_bosch_cloud_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_verify_x509_partial_chain_set(self) -> None:
        ctx = build_bosch_cloud_ssl_context()
        assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN

    def test_verify_mode_is_required(self) -> None:
        ctx = build_bosch_cloud_ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_check_hostname_enabled(self) -> None:
        ctx = build_bosch_cloud_ssl_context()
        assert ctx.check_hostname is True

    def test_returns_new_instance_each_call(self) -> None:
        """build_ must not share state — each call returns a fresh context."""
        ctx1 = build_bosch_cloud_ssl_context()
        ctx2 = build_bosch_cloud_ssl_context()
        assert ctx1 is not ctx2


# ---------------------------------------------------------------------------
# get_bosch_cloud_ssl_context (caching)
# ---------------------------------------------------------------------------


class TestGetBoschCloudSslContext:
    def test_caches_on_second_call(self) -> None:
        # Reset module-level cache first so the test is deterministic.
        bosch_cloud_ssl._SSL_CONTEXT = None
        ctx1 = get_bosch_cloud_ssl_context()
        ctx2 = get_bosch_cloud_ssl_context()
        assert ctx1 is ctx2

    def test_returns_ssl_context(self) -> None:
        bosch_cloud_ssl._SSL_CONTEXT = None
        assert isinstance(get_bosch_cloud_ssl_context(), ssl.SSLContext)

    def teardown_method(self, _method: object) -> None:
        # Restore cache-neutral state for other tests.
        bosch_cloud_ssl._SSL_CONTEXT = None


# ---------------------------------------------------------------------------
# _BoschCloudAdapter
# ---------------------------------------------------------------------------


class TestBoschCloudAdapter:
    def test_init_poolmanager_passes_ssl_context(self) -> None:
        ssl_ctx = MagicMock(spec=ssl.SSLContext)
        adapter = _BoschCloudAdapter(ssl_context=ssl_ctx, pool_connections=2, pool_maxsize=4)

        with patch("bosch_cloud_ssl.PoolManager") as mock_pm:
            adapter.init_poolmanager(num_pools=2, maxsize=4)
            call_kwargs = mock_pm.call_args.kwargs
            assert call_kwargs.get("ssl_context") is ssl_ctx

    def test_proxy_manager_for_sets_ssl_context(self) -> None:
        ssl_ctx = MagicMock(spec=ssl.SSLContext)
        adapter = _BoschCloudAdapter(ssl_context=ssl_ctx, pool_connections=2, pool_maxsize=4)

        with patch.object(
            _BoschCloudAdapter.__bases__[0],  # HTTPAdapter
            "proxy_manager_for",
            return_value=MagicMock(),
        ) as mock_super:
            adapter.proxy_manager_for("https://proxy.example.com")
            _, kwargs = mock_super.call_args
            assert kwargs.get("ssl_context") is ssl_ctx


# ---------------------------------------------------------------------------
# make_bosch_cloud_session
# ---------------------------------------------------------------------------


class TestMakeBoschCloudSession:
    def test_returns_requests_session(self) -> None:
        import requests as req_lib

        s = make_bosch_cloud_session()
        assert isinstance(s, req_lib.Session)

    def test_https_adapter_is_bosch_cloud_adapter(self) -> None:
        s = make_bosch_cloud_session()
        adapter = s.get_adapter("https://residential.cbs.boschsecurity.com")
        assert isinstance(adapter, _BoschCloudAdapter)

    def test_http_adapter_is_plain(self) -> None:
        """http:// should NOT use the Bosch SSL adapter (no TLS)."""
        s = make_bosch_cloud_session()
        adapter = s.get_adapter("http://example.com")
        # The default HTTPAdapter is used; it must not be a _BoschCloudAdapter.
        assert not isinstance(adapter, _BoschCloudAdapter)

    def test_each_call_returns_new_session(self) -> None:
        s1 = make_bosch_cloud_session()
        s2 = make_bosch_cloud_session()
        assert s1 is not s2


# ---------------------------------------------------------------------------
# requests_*_bosch_cloud helpers — verify= stripping
# ---------------------------------------------------------------------------


class TestRequestsBoschCloudHelpers:
    """Drop-in helpers must strip any caller-supplied verify= kwarg."""

    def _mock_session(self, method: str, status_code: int = 200):
        """Return a patch context that intercepts make_bosch_cloud_session."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_session = MagicMock()
        getattr(mock_session, method).return_value = mock_resp
        return patch("bosch_cloud_ssl.make_bosch_cloud_session", return_value=mock_session)

    def test_get_strips_verify_false(self) -> None:
        with self._mock_session("get") as mock_make:
            requests_get_bosch_cloud(
                "https://residential.cbs.boschsecurity.com/test", verify=False, timeout=5
            )
            _, call_kwargs = mock_make.return_value.get.call_args
            assert "verify" not in call_kwargs

    def test_get_strips_verify_true(self) -> None:
        with self._mock_session("get") as mock_make:
            requests_get_bosch_cloud(
                "https://residential.cbs.boschsecurity.com/test", verify=True, timeout=5
            )
            _, call_kwargs = mock_make.return_value.get.call_args
            assert "verify" not in call_kwargs

    def test_put_strips_verify(self) -> None:
        with self._mock_session("put") as mock_make:
            requests_put_bosch_cloud(
                "https://residential.cbs.boschsecurity.com/test", verify=False, timeout=5
            )
            _, call_kwargs = mock_make.return_value.put.call_args
            assert "verify" not in call_kwargs

    def test_post_strips_verify(self) -> None:
        with self._mock_session("post") as mock_make:
            requests_post_bosch_cloud(
                "https://residential.cbs.boschsecurity.com/test", verify=False, timeout=5
            )
            _, call_kwargs = mock_make.return_value.post.call_args
            assert "verify" not in call_kwargs

    def test_get_passes_other_kwargs(self) -> None:
        with self._mock_session("get") as mock_make:
            requests_get_bosch_cloud(
                "https://residential.cbs.boschsecurity.com/test",
                timeout=10,
                headers={"X-Foo": "bar"},
            )
            _, call_kwargs = mock_make.return_value.get.call_args
            assert call_kwargs.get("timeout") == 10
            assert call_kwargs.get("headers") == {"X-Foo": "bar"}
