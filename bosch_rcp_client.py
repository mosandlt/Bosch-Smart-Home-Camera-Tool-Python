"""Thin synchronous wrappers around the ``bosch-shc-camera-client`` PyPI
library's RCP (Remote Configuration Protocol) session/read and LOCAL-write
implementation.

This CLI is 100% synchronous (``requests``-based); the shared library
(extracted from the sibling Home Assistant integration, same byte-for-byte
protocol logic) is 100% async (``aiohttp``-based). Since this CLI issues
one-shot command invocations rather than running as a long-lived service,
every wrapper here opens a fresh ``aiohttp.ClientSession`` via
``asyncio.run()`` and closes it again before returning -- no cross-invocation
connection pooling is needed or attempted.

Keeping this async/sync boundary in its own module (rather than inlining
``asyncio.run()`` calls throughout ``bosch_camera.py``) keeps the seam in one
place and easy to find/mock in tests.

NOTE on scope (see knowledge-base / CLAUDE.md for the full writeup): the
library's high-level ``fetch_rcp_camera_data`` batch orchestrator was
deliberately NOT used here. It reads a different, narrower set of RCP
commands (dimmer/privacy/clock/lan_ip/product_name/bitrate/alarm_catalog/
motion_zones via 0x0c00/motion_coords/tls_cert/network_services/iva_catalog
via 0x0b60) with different output shapes than this CLI's ``cmd_rcp``
subcommands need (which additionally read cloud-FQDN, MAC, RCP thumbnail
snapshot, raw YUV frame, gzip IVA script, and IVA rule names + resiMotion
config via 0x0ba9/0x0a1b -- none of which exist in the library, and whose
"motion"/"iva" RCP *commands* don't even match the library's). Only the
genuinely-shared low-level primitives (session handshake, single READ, LOCAL
write) are wrapped here; ``cmd_rcp``'s own field-specific parsers stay in
``bosch_camera.py`` since the library exposes no faithful public equivalent
for them.
"""

from __future__ import annotations

import asyncio

import aiohttp
from bosch_shc_camera_client import rcp as _rcp_lib

from bosch_cloud_ssl import get_bosch_cloud_ssl_context

# RCP session cache: proxy_hash -> (session_id, expires_at_monotonic).
# Shape matches bosch_shc_camera_client.rcp.RcpSessionCache -- the library
# owns the 5-minute TTL logic; this module-level dict just persists it across
# calls within one CLI process (mirrors the CLI's previous _RCP_SESSION_CACHE).
_RCP_SESSION_CACHE: _rcp_lib.RcpSessionCache = {}


def split_proxy_base(proxy_base: str) -> tuple[str, str]:
    """Split ``https://HOST:PORT/HASH`` into ``(host_with_port, hash)``.

    ``proxy_base`` is the value returned by ``rcp_open_connection()`` --
    e.g. ``https://proxy-01.live.cbs.boschsecurity.com:42090/abc123``.
    """
    without_scheme = proxy_base.removeprefix("https://").removeprefix("http://")
    proxy_host, _, proxy_hash = without_scheme.partition("/")
    return proxy_host, proxy_hash


def get_session_id(proxy_base: str) -> str | None:
    """Open (or reuse a cached) RCP session for ``proxy_base``.

    Returns ``None`` if the handshake fails for any reason (network error,
    non-200 response, missing/invalid sessionid) -- the library logs the
    specific cause at DEBUG level; this wrapper does not re-raise it as a
    distinct exception type (that specific-error-message-per-failure-mode
    behavior of the CLI's old hand-rolled ``rcp_session()`` is not preserved
    by the library, which is deliberately failure-mode-agnostic at this
    layer). Callers that need a raised error should raise on ``None``.
    """
    proxy_host, proxy_hash = split_proxy_base(proxy_base)

    async def _do() -> str | None:
        ssl_context = get_bosch_cloud_ssl_context()
        return await _rcp_lib.get_cached_rcp_session(
            ssl_context, _RCP_SESSION_CACHE, proxy_host, proxy_hash
        )

    return asyncio.run(_do())


def rcp_read(
    rcp_url: str,
    command: str,
    sessionid: str,
    type_: str = "P_OCTET",
    num: int = 0,
) -> bytes | None:
    """READ one RCP command via the cloud proxy.

    ``rcp_url`` is the full ``.../rcp.xml`` URL (as built by ``_rcp_setup``).
    Returns the decoded payload bytes, or ``None`` on any failure (network
    error, non-200, RCP ``<err>``, or an empty/missing payload) -- matches
    the CLI's pre-existing ``rcp_read()`` contract.
    """

    async def _do() -> bytes | None:
        ssl_context = get_bosch_cloud_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            return await _rcp_lib.rcp_read(
                session,
                rcp_url,
                command,
                sessionid,
                type_=type_,
                num=num,
                session_cache=_RCP_SESSION_CACHE,
            )

    return asyncio.run(_do())


def lan_write_privacy(
    cam_ip: str,
    enabled: bool,
    *,
    user: str = "",
    password: str = "",
) -> bool:
    """Write privacy-mode state via direct LOCAL RCP (Gen2, HTTPS).

    Uses the library's ``rcp_local_write_privacy`` (command 0x0d00, HTTPS on
    port 443) -- a protocol fix over the CLI's previous plain-HTTP-port-80
    implementation, which the sibling Home Assistant integration's commit
    history confirms is not answered by modern Gen2 firmware ("Earlier
    versions issued plain HTTP on port 80 and silently failed -- confirmed
    against live Gen2 hardware 2026-05-20"). Pass ``user``/``password`` (the
    cycling ``cbs-...`` LOCAL Digest credentials, when known) to authorise
    the write; without them the library falls back to an unauthenticated
    HTTPS request, which current firmware will reject with 401.
    """

    async def _do() -> bool:
        ssl_context = get_bosch_cloud_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            return await _rcp_lib.rcp_local_write_privacy(
                session,
                cam_ip,
                enabled,
                user=user or None,
                password=password or None,
            )

    return asyncio.run(_do())


def lan_write_front_light(
    cam_ip: str,
    brightness: int,
    *,
    user: str = "",
    password: str = "",
) -> bool:
    """Write front-light brightness (0-100) via direct LOCAL RCP (Gen2, HTTPS).

    Uses the library's ``rcp_local_write_front_light`` (command 0x0c22,
    T_WORD, HTTPS on port 443) -- same protocol fix as ``lan_write_privacy``.
    """

    async def _do() -> bool:
        ssl_context = get_bosch_cloud_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            return await _rcp_lib.rcp_local_write_front_light(
                session,
                cam_ip,
                brightness,
                user=user or None,
                password=password or None,
            )

    return asyncio.run(_do())
