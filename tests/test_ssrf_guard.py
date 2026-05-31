"""Tests for the SSRF allowlist guard (_is_safe_bosch_url).

Event snapshot/clip URLs come straight from the cloud API response, so they are
validated against a Bosch-domain allowlist before being fetched with the
bearer-carrying session. Cross-version parity with the HA integration's
_is_safe_bosch_url.
"""

from __future__ import annotations

import bosch_camera


class TestIsSafeBoschUrl:
    def test_accepts_bosch_cloud_https(self) -> None:
        assert bosch_camera._is_safe_bosch_url(
            "https://residential.cbs.boschsecurity.com/events/snap.jpg"
        )

    def test_accepts_bosch_com_subdomain(self) -> None:
        assert bosch_camera._is_safe_bosch_url("https://cdn.bosch.com/img.jpg")

    def test_rejects_non_bosch_host(self) -> None:
        assert not bosch_camera._is_safe_bosch_url("https://evil.example.com/snap.jpg")

    def test_rejects_http_scheme(self) -> None:
        assert not bosch_camera._is_safe_bosch_url(
            "http://residential.cbs.boschsecurity.com/snap.jpg"
        )

    def test_rejects_lookalike_suffix(self) -> None:
        # A host that merely *contains* the domain must not match — the leading
        # dot in the allowlist enforces a real sub-domain boundary.
        assert not bosch_camera._is_safe_bosch_url(
            "https://boschsecurity.com.evil.example/snap.jpg"
        )
        assert not bosch_camera._is_safe_bosch_url("https://notboschsecurity.com/x")

    def test_rejects_empty_and_garbage(self) -> None:
        assert not bosch_camera._is_safe_bosch_url("")
        assert not bosch_camera._is_safe_bosch_url("not-a-url")
        assert not bosch_camera._is_safe_bosch_url("file:///etc/passwd")
