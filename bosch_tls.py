"""Trust-on-first-use (TOFU) TLS helpers for Bosch camera LAN connections.

Bosch cameras use self-signed certificates with no CA chain. The standard
verify=False approach silences urllib3 warnings but leaves connections
vulnerable to MITM. This module implements TOFU fingerprint pinning:

  - First connection to a host: connect with verify=False, extract the
    peer certificate's SHA-256 fingerprint, persist it to bosch_config.json
    under cam_cert_fingerprints[<ip>].
  - Subsequent connections: verify that the live cert fingerprint matches
    the stored one. Mismatch raises CertPinningError.

Usage — drop-in wrappers around requests.get / post / put:

    from bosch_tls import bosch_get, bosch_post, bosch_put, CertPinningError

    r = bosch_get("https://192.0.2.149/snap.jpg", cfg=cfg, ...)

The ``cfg`` dict is the loaded bosch_config.json dict.  Pass it by reference;
fingerprints are stored in-memory immediately and must be persisted to disk
by the caller (via save_config) after the call returns.

For calls where cfg is not available (e.g. module-level helpers), pass
``cfg=None`` — the module degrades to verify=False (legacy behaviour) and
logs a warning.

Keycloak / cloud API calls (smarthome.authz.bosch.com, residential.cbs…):
These endpoints have valid CA-signed certificates.  Do NOT route them through
this module — use ``requests.get/post(verify=True, …)`` directly.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import ssl
from typing import Any, Optional

import requests
import urllib3

_LOGGER = logging.getLogger(__name__)

# Silence urllib3's InsecureRequestWarning — we handle TLS security ourselves
# via fingerprint pinning.  Only applied to LAN camera connections.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_CFG_KEY = "cam_cert_fingerprints"


class CertPinningError(Exception):
    """Raised when a camera's live TLS certificate does not match the stored fingerprint.

    This indicates either certificate rotation (legitimate — run with
    ``--cert-reset <cam>`` to re-pin) or a potential MITM attack.
    """


def _fetch_fingerprint(host: str, port: int = 443, timeout: float = 5.0) -> str:
    """Open a raw TLS connection to host:port and return the SHA-256 fingerprint.

    Does not verify the certificate chain (self-signed cameras have no CA).
    Returns the fingerprint as a lower-case hex string (64 chars).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw_sock:
            with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
                der_bytes_raw: bytes | None = tls_sock.getpeercert(binary_form=True)
                if not der_bytes_raw:
                    raise CertPinningError(f"No certificate received from {host}:{port}")
                return hashlib.sha256(der_bytes_raw).hexdigest()
    except CertPinningError:
        raise
    except Exception as exc:
        raise CertPinningError(f"Could not fetch certificate from {host}:{port}: {exc}") from exc


def _host_from_url(url: str) -> tuple[str, int]:
    """Extract (host, port) from an https:// URL. Default port = 443."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 443
    return host, port


def pin_or_verify(
    host: str,
    port: int = 443,
    cfg: Optional[dict[str, Any]] = None,
    timeout: float = 5.0,
) -> bool:
    """Perform TOFU fingerprint pinning for a Bosch camera LAN host.

    Returns True when the connection can proceed (fingerprint matches or was
    just stored).  Raises CertPinningError on mismatch.  Returns True and
    logs a warning when cfg is None (degrades to legacy verify=False behaviour).

    Side-effect: when a new fingerprint is stored, ``cfg[_CFG_KEY][host]`` is
    set in-memory.  The caller must persist cfg to disk via save_config().
    """
    if cfg is None:
        _LOGGER.warning(
            "bosch_tls: no config passed for %s:%s — skipping fingerprint check (verify=False)",
            host, port,
        )
        return True

    stored: dict[str, str] = cfg.setdefault(_CFG_KEY, {})
    live_fp = _fetch_fingerprint(host, port, timeout)

    if host not in stored:
        stored[host] = live_fp
        _LOGGER.info("bosch_tls: stored new fingerprint for %s:%s — %s…", host, port, live_fp[:16])
        return True

    if stored[host] != live_fp:
        raise CertPinningError(
            f"Certificate fingerprint mismatch for {host}:{port}!\n"
            f"  Stored : {stored[host]}\n"
            f"  Live   : {live_fp}\n"
            "If the camera certificate was legitimately rotated, run:\n"
            "  python3 bosch_camera.py cert-reset <cam-name>"
        )

    return True


def clear_fingerprint(host: str, cfg: dict[str, Any]) -> bool:
    """Remove the stored fingerprint for ``host`` from cfg (in-memory).

    Returns True if a fingerprint was removed, False if none was stored.
    Caller must persist cfg to disk via save_config() afterwards.
    """
    stored: dict[str, str] = cfg.get(_CFG_KEY, {})
    if host in stored:
        del stored[host]
        _LOGGER.info("bosch_tls: cleared fingerprint for %s", host)
        return True
    return False


# ---------------------------------------------------------------------------
# Drop-in wrappers — route all LAN camera HTTP calls through these.
# ---------------------------------------------------------------------------

def bosch_get(
    url: str,
    cfg: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> requests.Response:
    """requests.get() with TOFU fingerprint pinning for https:// camera URLs.

    Passes ``verify=False`` to requests (since the camera is self-signed) but
    first verifies / stores the fingerprint via pin_or_verify().  Raises
    CertPinningError on fingerprint mismatch before the request is sent.
    """
    if url.startswith("https://"):
        host, port = _host_from_url(url)
        pin_or_verify(host, port, cfg)
    kwargs.setdefault("verify", False)
    return requests.get(url, **kwargs)


def bosch_post(
    url: str,
    cfg: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> requests.Response:
    """requests.post() with TOFU fingerprint pinning for https:// camera URLs."""
    if url.startswith("https://"):
        host, port = _host_from_url(url)
        pin_or_verify(host, port, cfg)
    kwargs.setdefault("verify", False)
    return requests.post(url, **kwargs)


def bosch_put(
    url: str,
    cfg: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> requests.Response:
    """requests.put() with TOFU fingerprint pinning for https:// camera URLs."""
    if url.startswith("https://"):
        host, port = _host_from_url(url)
        pin_or_verify(host, port, cfg)
    kwargs.setdefault("verify", False)
    return requests.put(url, **kwargs)
