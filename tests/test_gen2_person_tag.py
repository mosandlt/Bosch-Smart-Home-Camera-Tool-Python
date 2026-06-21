"""
Regression tests for _effective_event_type — Gen2 PERSON-tag promotion.

Gen2 cameras send eventType=MOVEMENT with eventTags=["PERSON"] for human
detections, while Gen1 sends eventType=PERSON directly.  The helper must
normalise both so icons, Signal messages, and webhook payloads agree.

Fake IDs / data only — no real device values.
"""

from __future__ import annotations

from typing import Any

from bosch_camera import _effective_event_type


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ev(event_type: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Build a minimal event dict with fake IDs."""
    ev: dict[str, Any] = {
        "id": "11111111-0000-0000-0000-000000000001",
        "eventType": event_type,
        "timestamp": "2024-06-01T10:00:00+02:00",
        "imageUrl": "",
        "videoClipUrl": "",
    }
    if tags is not None:
        ev["eventTags"] = tags
    return ev


# ─────────────────────────────────────────────────────────────────────────────
# Core promotion logic
# ─────────────────────────────────────────────────────────────────────────────


class TestEffectiveEventType:
    def test_gen2_movement_with_person_tag_promoted_to_person(self) -> None:
        """MOVEMENT + eventTags=["PERSON"] must become "PERSON" (Gen2 human)."""
        ev = _ev("MOVEMENT", tags=["PERSON"])
        assert _effective_event_type(ev) == "PERSON"

    def test_plain_movement_no_tags_stays_movement(self) -> None:
        """MOVEMENT without eventTags must remain "MOVEMENT"."""
        ev = _ev("MOVEMENT")
        assert _effective_event_type(ev) == "MOVEMENT"

    def test_plain_movement_empty_tags_stays_movement(self) -> None:
        """MOVEMENT with eventTags=[] must remain "MOVEMENT"."""
        ev = _ev("MOVEMENT", tags=[])
        assert _effective_event_type(ev) == "MOVEMENT"

    def test_explicit_person_event_type_stays_person(self) -> None:
        """Gen1 eventType=PERSON (no tags) must remain "PERSON"."""
        ev = _ev("PERSON")
        assert _effective_event_type(ev) == "PERSON"

    def test_audio_alarm_unchanged(self) -> None:
        """AUDIO_ALARM must pass through unmodified."""
        ev = _ev("AUDIO_ALARM")
        assert _effective_event_type(ev) == "AUDIO_ALARM"

    def test_intrusion_unchanged(self) -> None:
        """INTRUSION must pass through unmodified."""
        ev = _ev("INTRUSION")
        assert _effective_event_type(ev) == "INTRUSION"

    def test_event_tags_none_is_safe(self) -> None:
        """eventTags=None must not raise and must not promote MOVEMENT."""
        ev = _ev("MOVEMENT", tags=None)
        # tags was explicitly set to None in the dict
        assert _effective_event_type(ev) == "MOVEMENT"

    def test_event_tags_key_missing_is_safe(self) -> None:
        """Missing eventTags key must not raise and must not promote MOVEMENT."""
        ev: dict[str, Any] = {
            "id": "11111111-0000-0000-0000-000000000002",
            "eventType": "MOVEMENT",
            "timestamp": "2024-06-01T10:00:00",
        }
        assert _effective_event_type(ev) == "MOVEMENT"

    def test_movement_with_non_person_tag_stays_movement(self) -> None:
        """MOVEMENT with eventTags=["ANIMAL"] must remain "MOVEMENT"."""
        ev = _ev("MOVEMENT", tags=["ANIMAL"])
        assert _effective_event_type(ev) == "MOVEMENT"

    def test_movement_with_person_among_multiple_tags_promoted(self) -> None:
        """MOVEMENT with PERSON in a multi-tag list must become "PERSON"."""
        ev = _ev("MOVEMENT", tags=["MOVEMENT", "PERSON"])
        assert _effective_event_type(ev) == "PERSON"

    def test_empty_event_returns_empty_string(self) -> None:
        """Completely empty dict must return '' without raising."""
        assert _effective_event_type({}) == ""
