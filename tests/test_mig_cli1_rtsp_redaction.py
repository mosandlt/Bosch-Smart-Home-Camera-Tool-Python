"""
Item: CLI-1
Migration concept: Security / log hygiene (low priority)
Layer: bosch_camera.py — any log/status output that includes an rtsps:// URL

Soll-Assertion (prose):
    When the CLI emits an rtsps:// URL to a log or status line (e.g. via
    print(), logging.info(), or a status message), any embedded credentials
    of the form rtsps://user:pass@host/path must be redacted to
    rtsps://***:***@host/path (host and path kept).
    The explicit stream-URL *command* output (--stream-url flag) may stay
    unredacted by design — this item covers only incidental log/status paths.

    Expected transform:
        Input:  rtsps://fakeuser:fakepass@cam.example:443/rtsp_tunnel?inst=2
        Output: rtsps://***:***@cam.example:443/rtsp_tunnel?inst=2

    Mirrors HA's _redact_rtsp_creds() (switch.py); the CLI ships a parallel
    helper ``redact_rtsp_creds`` and applies it wherever an rtsps/rtsp URL is
    written to a log or status string.

Run: python -m pytest -q tests/test_mig_cli1_rtsp_redaction.py
"""

from __future__ import annotations

from bosch_camera import redact_rtsp_creds


def test_log_output_redacts_rtsps_credentials() -> None:
    """rtsps://user:pass@host:port/path?query in log output -> rtsps://***:***@host:port/path?query.

    Fake fixtures (never real device values):
        host:  cam.example
        user:  fakeuser
        pass:  fakepass
    """
    raw = "rtsps://fakeuser:fakepass@cam.example:443/rtsp_tunnel?inst=2"
    redacted = redact_rtsp_creds(raw)
    assert "fakepass" not in redacted
    assert "fakeuser" not in redacted
    assert "cam.example:443" in redacted
    assert "/rtsp_tunnel" in redacted
    assert "inst=2" in redacted
    assert redacted == "rtsps://***:***@cam.example:443/rtsp_tunnel?inst=2"


def test_log_output_redacts_rtsp_credentials() -> None:
    """rtsp://user:pass@host/path (plain RTSP, LOCAL mode) -> rtsp://***:***@host/path."""
    raw = "rtsp://fakeuser:fakepass@192.0.2.10:443/rtsp_tunnel?inst=1"
    redacted = redact_rtsp_creds(raw)
    assert "fakepass" not in redacted
    assert "fakeuser" not in redacted
    assert "192.0.2.10:443" in redacted
    assert redacted == "rtsp://***:***@192.0.2.10:443/rtsp_tunnel?inst=1"


def test_url_without_userinfo_is_unchanged() -> None:
    """A URL with no userinfo (no credentials) must be returned as-is."""
    url = "rtsps://cam.example:443/rtsp_tunnel?inst=2"
    assert redact_rtsp_creds(url) == url


def test_non_url_string_does_not_crash() -> None:
    """A plain non-URL string must not raise; return it unchanged."""
    plain = "some status message without a URL"
    assert redact_rtsp_creds(plain) == plain


def test_empty_string_returns_empty() -> None:
    """Empty input returns empty string."""
    assert redact_rtsp_creds("") == ""
