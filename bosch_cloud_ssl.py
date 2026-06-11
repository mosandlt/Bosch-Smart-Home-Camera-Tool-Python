"""Centralised TLS trust for Bosch public cloud endpoints (CWE-295 fix).

Bosch's residential cloud API (``residential.cbs.boschsecurity.com``) and the
live video proxy (``proxy-*.live.cbs.boschsecurity.com``) are served by a
*private* Bosch PKI (``Bosch ST Root CA`` -> ``Video CA 2A``) that is absent
from every public trust store, so plain ``verify=True`` rejects them.
The OAuth / Keycloak host (``smarthome.authz.bosch.com``) uses a public
Let's Encrypt certificate.

Historically every outbound call to those hosts used ``verify=False``
(GHSA-6qh5-x5m5-vj6v, CWE-295), which accepted *any* certificate and let an
adjacent-network attacker MITM the OAuth tokens and cloud traffic.

This module builds a single SSL context that trusts BOTH the system roots
(for the Let's Encrypt OAuth host and any other public host) AND the Bosch
private CA (for the cloud REST API and the video proxy).  It rejects
self-signed and otherwise untrusted certificates, closing the MITM hole while
keeping every Bosch cloud call working.

Local camera endpoints (LAN IPs) use per-device self-signed certificates and
intentionally keep ``verify=False`` / CERT_NONE — that is a documented
local-network exception handled by ``bosch_tls`` (TOFU fingerprint pinning)
and is out of scope for this module.
"""

from __future__ import annotations

import ssl
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

# Bosch "Video CA 2A" intermediate CA, issued by the private "Bosch ST Root CA".
# Extracted from the live residential.cbs.boschsecurity.com certificate chain.
# Validity: 2021-03-18 .. 2057-03-20.
# SHA-256 fingerprint:
#   9F:6A:CB:6D:79:38:60:A3:B1:B4:37:EA:D3:A7:D5:A6:
#   28:D0:28:8E:24:41:52:A5:E9:C9:6B:36:51:D6:01:D1
BOSCH_CLOUD_CA_PEM = """\
-----BEGIN CERTIFICATE-----
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
nvQ8Em1LhUA=
-----END CERTIFICATE-----
"""

_SSL_CONTEXT: ssl.SSLContext | None = None


def build_bosch_cloud_ssl_context() -> ssl.SSLContext:
    """Build an SSL context trusting system roots plus the Bosch private CA.

    The Bosch cloud endpoints use a private PKI (Bosch ST Root CA -> Video CA
    2A) that is not present in any public trust store.  This context adds the
    Bosch intermediate CA alongside the system roots so that both the cloud API
    and the OAuth/Let's Encrypt hosts are verified correctly.

    VERIFY_X509_PARTIAL_CHAIN allows OpenSSL to anchor the chain at the pinned
    intermediate without weakening validation of public hosts.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=BOSCH_CLOUD_CA_PEM)
    # The pinned Bosch CA is an intermediate (not a self-signed root), so allow
    # OpenSSL to anchor the chain at it.  This does not weaken validation of
    # public hosts: their chains still terminate at a trusted system root.
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


def get_bosch_cloud_ssl_context() -> ssl.SSLContext:
    """Return a cached SSL context for Bosch public cloud / OAuth hosts."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = build_bosch_cloud_ssl_context()
    return _SSL_CONTEXT


class _BoschCloudAdapter(HTTPAdapter):
    """requests HTTPAdapter that pins the Bosch private CA + system roots.

    Replaces ``verify=False`` on all Bosch cloud/proxy calls (CWE-295).
    Local camera LAN-IP calls are NOT routed through this adapter.
    """

    def __init__(self, ssl_context: ssl.SSLContext, **kwargs: Any) -> None:
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(  # type: ignore[override]
        self,
        num_pools: int,
        maxsize: int,
        block: bool = False,
        **connection_pool_kw: Any,
    ) -> None:
        # Pass the pre-built SSLContext so urllib3 uses it for every connection.
        self.poolmanager = PoolManager(
            num_pools=num_pools,
            maxsize=maxsize,
            block=block,
            ssl_context=self._ssl_context,
            **connection_pool_kw,
        )

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:  # type: ignore[override]
        proxy_kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def make_bosch_cloud_session(
    pool_connections: int = 10,
    pool_maxsize: int = 20,
) -> requests.Session:
    """Return a new requests.Session with the Bosch cloud SSL adapter mounted.

    Mounts the adapter on ``https://`` only — plain http:// is not used for
    cloud calls.  Caller is responsible for setting Authorization headers.
    """
    ssl_ctx = get_bosch_cloud_ssl_context()
    adapter = _BoschCloudAdapter(
        ssl_context=ssl_ctx,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=0,
    )
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def requests_get_bosch_cloud(url: str, **kwargs: Any) -> requests.Response:
    """Drop-in for ``requests.get(url, verify=False)`` on Bosch cloud URLs.

    Uses a one-shot session with the Bosch cloud SSL adapter.  For high-volume
    callers, prefer ``make_bosch_cloud_session()`` and reuse it.
    """
    # Suppress any caller-supplied verify= so the adapter context wins.
    kwargs.pop("verify", None)
    s = make_bosch_cloud_session(pool_connections=1, pool_maxsize=1)
    return s.get(url, **kwargs)


def requests_put_bosch_cloud(url: str, **kwargs: Any) -> requests.Response:
    """Drop-in for ``requests.put(url, verify=False)`` on Bosch cloud URLs."""
    kwargs.pop("verify", None)
    s = make_bosch_cloud_session(pool_connections=1, pool_maxsize=1)
    return s.put(url, **kwargs)


def requests_post_bosch_cloud(url: str, **kwargs: Any) -> requests.Response:
    """Drop-in for ``requests.post(url, verify=False)`` on Bosch cloud URLs."""
    kwargs.pop("verify", None)
    s = make_bosch_cloud_session(pool_connections=1, pool_maxsize=1)
    return s.post(url, **kwargs)


# Re-export for convenient star imports in tests.
__all__ = [
    "BOSCH_CLOUD_CA_PEM",
    "build_bosch_cloud_ssl_context",
    "get_bosch_cloud_ssl_context",
    "make_bosch_cloud_session",
    "requests_get_bosch_cloud",
    "requests_put_bosch_cloud",
    "requests_post_bosch_cloud",
    "_BoschCloudAdapter",
]
