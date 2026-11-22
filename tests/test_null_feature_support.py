"""Regression: the cloud may send ``"featureSupport": null`` (GitHub #87 in the
HA integration). ``discover_cameras`` must treat it like a missing key."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import bosch_camera


def test_discover_cameras_null_feature_support() -> None:
    resp = MagicMock()
    resp.status_code = 200
    session = MagicMock()
    session.get.return_value = resp
    cam = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Cam",
        "featureSupport": None,
    }
    cfg: dict[str, Any] = {
        "cameras": {"Cam": {"local_ip": "192.0.2.10"}},
    }
    with (
        patch.object(bosch_camera, "_json_or_raise", return_value=[cam]),
        patch.object(bosch_camera, "save_config", create=True),
    ):
        out = bosch_camera.discover_cameras(cfg, session)
    assert out["Cam"]["has_light"] is False
    assert out["Cam"]["pan_limit"] == 0
