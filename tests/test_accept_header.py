"""Regression: shared HTTP session must not default to Accept: application/json.

Source incident 2026-05-24: downloading event snapshots
(/v11/events/{id}/snap.jpg) and event clips (/v11/events/{id}/clip.mp4) from
the Bosch cloud returned HTTP 500 "sh:internal.error" because the session
default header was Accept: application/json. Bosch nginx routes the binary
endpoint based on Accept and refuses to serve JPEG/MP4 when the client claims
to only accept JSON. Live verification: with Accept: image/jpeg the same URL
returned 200 + 281 kB JPEG; with Accept: video/mp4 it returned 200 + 6.6 MB
MP4. Pinned: default must be Accept: */* so binary fetches work without each
call-site needing an override.
"""

from __future__ import annotations

import pytest
import responses as responses_lib
from responses import matchers

import bosch_camera


def _reset_session() -> None:
    """Force the module-level cached session to be recreated."""
    bosch_camera._HTTP_SESSION = None


@pytest.fixture(autouse=True)
def _isolate_session() -> None:
    _reset_session()
    yield
    _reset_session()


def test_make_session_default_accept_allows_binary() -> None:
    """Default Accept on the shared session must permit binary responses.

    Pin both directions: must NOT be application/json (the Bosch 500 trigger),
    must include either */* or the binary types image/jpeg + video/mp4.
    """
    s = bosch_camera.make_session("dummy-token")
    accept = s.headers.get("Accept", "")
    assert accept != "application/json", (
        "Default Accept: application/json triggers HTTP 500 sh:internal.error "
        "on Bosch /v11/events/{id}/snap.jpg and /clip.mp4 endpoints."
    )
    assert accept == "*/*" or ("image" in accept and "video" in accept), (
        f"Default Accept must allow binary; got {accept!r}"
    )


def test_make_session_token_refresh_preserves_binary_accept() -> None:
    """Re-calling make_session with a new token must keep Accept binary-safe."""
    s1 = bosch_camera.make_session("token-A")
    s2 = bosch_camera.make_session("token-B")
    assert s1 is s2, "session should be reused"
    assert s2.headers["Authorization"] == "Bearer token-B"
    assert s2.headers.get("Accept", "") != "application/json"


@responses_lib.activate
def test_event_image_fetch_does_not_send_json_only_accept() -> None:
    """The session used for api_get_events must send a binary-friendly Accept
    when the caller subsequently fetches the imageUrl. This is the exact
    sequence broken in the 2026-05-24 incident.
    """
    cam_id = "AABBCCDD-DEAD-BEEF-0000-000000000001"
    img_url = "https://residential.cbs.boschsecurity.com/v11/events/E1/snap.jpg"
    clip_url = "https://residential.cbs.boschsecurity.com/v11/events/E1/clip.mp4"

    # /v11/events list — JSON, returns one event with imageUrl + videoClipUrl
    responses_lib.add(
        responses_lib.GET,
        "https://residential.cbs.boschsecurity.com/v11/events",
        json=[
            {
                "id": "E1",
                "timestamp": "2026-05-24T08:18:02.893+02:00",
                "eventType": "MOVEMENT",
                "imageUrl": img_url,
                "videoClipUrl": clip_url,
                "videoClipUploadStatus": "Done",
            }
        ],
        status=200,
        match=[matchers.query_param_matcher({"videoInputId": cam_id, "limit": "10"})],
    )

    captured_accepts: list[str] = []

    def _capture_accept(req):
        captured_accepts.append(req.headers.get("Accept", ""))
        # Refuse with 500 if Accept is application/json — mirrors Bosch's real
        # behavior. Otherwise return image bytes.
        if req.headers.get("Accept", "") == "application/json":
            return (
                500,
                {"Content-Type": "application/json"},
                b'{"status":500,"error":"sh:internal.error"}',
            )
        return (200, {"Content-Type": "image/jpeg"}, b"\xff\xd8\xff\xe0" + b"x" * 100)

    responses_lib.add_callback(responses_lib.GET, img_url, callback=_capture_accept)
    responses_lib.add_callback(responses_lib.GET, clip_url, callback=_capture_accept)

    session = bosch_camera.make_session("test-token")
    events = bosch_camera.api_get_events(session, cam_id, limit=10)
    assert len(events) == 1

    # Mimic a downstream caller doing session.get on the binary URLs without
    # overriding Accept — they must work because the default is binary-safe.
    r_img = session.get(events[0]["imageUrl"], timeout=5)
    r_clip = session.get(events[0]["videoClipUrl"], timeout=5)

    assert r_img.status_code == 200, (
        f"Image fetch failed: {r_img.status_code}; Accept sent={captured_accepts!r}"
    )
    assert r_clip.status_code == 200, (
        f"Clip fetch failed: {r_clip.status_code}; Accept sent={captured_accepts!r}"
    )
    assert all(a != "application/json" for a in captured_accepts), (
        f"At least one binary fetch sent Accept: application/json — Accepts={captured_accepts!r}"
    )
