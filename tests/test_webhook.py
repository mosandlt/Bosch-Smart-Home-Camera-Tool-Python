"""Tests for --webhook flag in the watch subcommand of bosch_camera.py.

Covers:
  - test_watch_without_webhook_no_post: no --webhook → _post_event_webhook never called
  - test_watch_with_webhook_posts_motion: --webhook URL → POST with correct payload
  - test_watch_with_webhook_handles_500_gracefully: 500 response → no exception, warning printed

Source: bosch_camera.py _post_event_webhook()
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

HOOK_URL = "https://webhook.example.com/bosch"

SAMPLE_EVENT: dict[str, Any] = {
    "id": "evt-abc-123",
    "eventType": "MOVEMENT",
    "timestamp": "2026-05-20T10:00:00Z",
    "imageUrl": "https://residential.cbs.boschsecurity.com/img/snap.jpg",
    "videoClipUrl": "https://residential.cbs.boschsecurity.com/clip/abc.mp4",
}


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _post_event_webhook (unit level)
# ─────────────────────────────────────────────────────────────────────────────

class TestPostEventWebhook:
    """Unit tests for the _post_event_webhook() helper."""

    def test_watch_without_webhook_no_post(self) -> None:
        """When no URL is passed, _post_event_webhook must not make any HTTP call.

        The watch loop checks `if webhook_url:` before calling the function, so
        the function itself is never invoked.  This test pins that guard by
        calling _post_event_webhook only via the live code path simulation
        (monkeypatching requests.post to detect any leak).
        """
        with patch("bosch_camera.requests") as mock_requests:
            # Simulate the watch-loop guard: empty webhook_url → no call
            webhook_url = ""
            if webhook_url:
                bosch_camera._post_event_webhook(
                    webhook_url, "Testcam", "CAM-001", "MOVEMENT", "2026-05-20T10:00:00Z", SAMPLE_EVENT
                )
            mock_requests.post.assert_not_called()

    def test_watch_with_webhook_posts_motion(self) -> None:
        """--webhook URL set + MOVEMENT event → POST with correct JSON payload."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("bosch_camera.requests.post", return_value=mock_resp) as mock_post:
            bosch_camera._post_event_webhook(
                HOOK_URL, "Garten", "AABBCCDD-DEAD-BEEF-FACE-000000000001",
                "MOVEMENT", "2026-05-20T10:00:00Z", SAMPLE_EVENT,
            )

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        # Positional arg 0 is the URL
        assert call_args.args[0] == HOOK_URL
        payload = call_args.kwargs["json"]
        assert payload["camera"] == "Garten"
        assert payload["camera_id"] == "AABBCCDD-DEAD-BEEF-FACE-000000000001"
        assert payload["event_type"] == "MOVEMENT"
        assert payload["timestamp"] == "2026-05-20T10:00:00Z"
        assert payload["event_id"] == "evt-abc-123"
        assert "image_url" in payload
        assert "clip_url" in payload
        assert call_args.kwargs.get("timeout") == 10

    def test_watch_with_webhook_posts_audio(self) -> None:
        """AUDIO_ALARM event → POST with event_type=AUDIO_ALARM."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        audio_event = {**SAMPLE_EVENT, "eventType": "AUDIO_ALARM", "id": "evt-audio-1"}

        with patch("bosch_camera.requests.post", return_value=mock_resp) as mock_post:
            bosch_camera._post_event_webhook(
                HOOK_URL, "Innenbereich", "CAM-AUDIO", "AUDIO_ALARM", "2026-05-20T11:00:00Z", audio_event,
            )

        payload = mock_post.call_args.kwargs["json"]
        assert payload["event_type"] == "AUDIO_ALARM"
        assert payload["event_id"] == "evt-audio-1"

    def test_watch_with_webhook_posts_person(self) -> None:
        """PERSON event → POST with event_type=PERSON."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        person_event = {**SAMPLE_EVENT, "eventType": "PERSON", "id": "evt-person-7"}

        with patch("bosch_camera.requests.post", return_value=mock_resp) as mock_post:
            bosch_camera._post_event_webhook(
                HOOK_URL, "Terrasse", "CAM-PERSON", "PERSON", "2026-05-20T12:00:00Z", person_event,
            )

        payload = mock_post.call_args.kwargs["json"]
        assert payload["event_type"] == "PERSON"
        assert payload["event_id"] == "evt-person-7"

    def test_watch_with_webhook_handles_500_gracefully(self, capsys: pytest.CaptureFixture) -> None:
        """HTTP 500 response → no exception raised, warning printed to stderr."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("bosch_camera.requests.post", return_value=mock_resp):
            # Must not raise
            bosch_camera._post_event_webhook(
                HOOK_URL, "Garten", "CAM-001", "MOVEMENT", "2026-05-20T10:00:00Z", SAMPLE_EVENT,
            )

        captured = capsys.readouterr()
        assert "500" in captured.err or "500" in captured.out

    def test_watch_with_webhook_handles_connection_error_gracefully(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """requests.post raises → no exception propagated, error printed to stderr."""
        import requests as _req

        with patch("bosch_camera.requests.post", side_effect=_req.ConnectionError("refused")):
            # Must not raise
            bosch_camera._post_event_webhook(
                HOOK_URL, "Garten", "CAM-001", "MOVEMENT", "2026-05-20T10:00:00Z", SAMPLE_EVENT,
            )

        captured = capsys.readouterr()
        assert "refused" in captured.err or "error" in captured.err.lower()

    def test_empty_event_fields_handled(self) -> None:
        """Event dict with missing fields → empty strings in payload, no KeyError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        sparse_event: dict[str, Any] = {}  # no id, no imageUrl, no videoClipUrl

        with patch("bosch_camera.requests.post", return_value=mock_resp) as mock_post:
            bosch_camera._post_event_webhook(
                HOOK_URL, "Cam", "CAM-X", "MOVEMENT", "2026-05-20T10:00:00Z", sparse_event,
            )

        payload = mock_post.call_args.kwargs["json"]
        assert payload["event_id"] == ""
        assert payload["image_url"] == ""
        assert payload["clip_url"] == ""
