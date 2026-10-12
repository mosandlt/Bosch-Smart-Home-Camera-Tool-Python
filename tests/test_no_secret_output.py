"""Regression: token/credential material must never reach stdout (scrollback/log leak)."""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from tests.test_cov_watch import _build_fake_firebase_module, _jwt, _make_cfg

FAKE_FCM = "fake-fcm-token-" + "x" * 40


def test_fcm_start_does_not_print_token_chars(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _make_cfg()
    resp = MagicMock()
    resp.status_code = 200
    with (
        patch.dict("sys.modules", {"firebase_messaging": _build_fake_firebase_module({})}),
        patch.object(bosch_camera, "make_session", return_value=MagicMock()),
        patch.object(bosch_camera, "api_get_events", return_value=[]),
        patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
        patch.object(bosch_camera, "api_mark_events_read"),
        patch.object(bosch_camera, "_install_stop_handlers"),
        patch.object(bosch_camera, "save_config"),
        patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=resp),
        patch("bosch_camera.requests.post", return_value=resp),
    ):
        bosch_camera._STOP_REQUESTED.clear()
        try:
            bosch_camera._watch_fcm_push(
                cfg,
                _jwt(),
                cfg["cameras"],
                duration=0,
                auto_snap=False,
                signal_url="",
                signal_sender="",
                signal_recipients=[],
            )
        finally:
            bosch_camera._STOP_REQUESTED.clear()
    out = capsys.readouterr().out
    assert "FCM Token" in out
    assert FAKE_FCM[:10] not in out


def test_token_status_does_not_print_token_chars(capsys: pytest.CaptureFixture[str]) -> None:
    access = _jwt()
    refresh = "refresh-" + "r" * 40
    cfg: dict[str, Any] = {"account": {"bearer_token": access, "refresh_token": refresh}}
    with patch.object(bosch_camera, "save_config"):
        bosch_camera.cmd_token(cfg, argparse.Namespace(cam=None, action=None))
    out = capsys.readouterr().out
    assert "Access token" in out and "Refresh token" in out
    assert access[:10] not in out
    assert refresh[:10] not in out
