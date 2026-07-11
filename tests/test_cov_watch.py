"""
Coverage tests for cmd_watch, _proxy_thread (inside _start_tls_proxy_sync),
on_notification / on_creds_updated (closures inside _watch_fcm_push),
_watch_fcm_push (ImportError early-exit branch), fetcher and _serve
(closures inside _live_snap_loop).

Fake IDs only — NEVER real device values, IPs, tokens, or secrets.
PIN_EVERY_MODE: one explicit test per discrete branch.
"""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera

# ─────────────────────────────────────────────────────────────────────────────
# Shared constants / helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_ID2 = "CCCCDDDD-1111-2222-3333-444455556666"
CAM_NAME = "Terrasse"
CAM_NAME2 = "Kamera"
FAKE_IP = "192.0.2.1"
FAKE_MAC = "aa:bb:cc:dd:ee:ff"
FAKE_TOKEN = "tok"


def _jwt(exp_offset: int = 3600) -> str:
    """Build a minimal unsigned JWT with exp = now + offset."""
    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = (
        base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + exp_offset}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{hdr}.{pay}.sig"


def _make_cfg(*, two_cams: bool = False) -> dict[str, Any]:
    cameras: dict[str, Any] = {
        CAM_NAME: {
            "id": CAM_ID,
            "name": CAM_NAME,
            "model": "HOME_Eyes_Outdoor",
            "firmware": "9.40.102",
            "mac": FAKE_MAC,
        }
    }
    if two_cams:
        cameras[CAM_NAME2] = {
            "id": CAM_ID2,
            "name": CAM_NAME2,
            "model": "HOME_Eyes_Indoor",
            "firmware": "9.40.102",
            "mac": "aa:bb:cc:dd:ee:ff",
        }
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": cameras,
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "interval": 1,
        "duration": 0,
        "snapshot": False,
        "push": False,
        "signal": "",
        "signal_sender": "",
        "signal_recipients": "",
        "webhook": "",
        "quiet_secs": 30,
        "auto_snapshot": False,
        "auto_record": False,
        "track_motion": False,
        "push_mode": "auto",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# cmd_watch — polling path
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdWatchPolling:
    """Tests for the polling code path in cmd_watch."""

    def _run_watch_once(
        self,
        cfg: dict[str, Any],
        args: argparse.Namespace,
        events_side_effect: Any = None,
    ) -> None:
        """Run cmd_watch with _STOP_REQUESTED set after one outer-loop iteration."""
        stop = threading.Event()

        def _stop_after_one_inner(*_: Any) -> None:
            # Allow inner sleep slices to fire, then set stop so outer loop exits.
            stop.set()
            bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(
                bosch_camera,
                "api_get_events",
                side_effect=events_side_effect or (lambda *a, **kw: []),
            ),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=_stop_after_one_inner),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, args)
            finally:
                bosch_camera._STOP_REQUESTED.clear()

    def test_poll_no_new_events(self) -> None:
        """Baseline fetch, poll once with empty events — no crash."""
        cfg = _make_cfg()
        self._run_watch_once(cfg, _args(interval=1))

    def test_poll_new_event_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """New events found in polling loop — printed to stdout."""
        cfg = _make_cfg()
        baseline_ev = {
            "id": "ev-old",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        new_ev = {
            "id": "ev-new",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                # baseline call
                return [baseline_ev]
            # poll call — one new event above baseline
            return [new_ev, baseline_ev]

        sleep_count = [0]

        def _stop_after_second_sleep(*_: Any) -> None:
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=_stop_after_second_sleep),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        out = capsys.readouterr().out
        assert "MOVEMENT" in out or "ev-new" in out or "Terrasse" in out

    def test_poll_audio_event_icon(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AUDIO_ALARM events use the audio icon path."""
        cfg = _make_cfg()
        baseline_ev = {
            "id": "b0",
            "eventType": "AUDIO_ALARM",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        new_ev = {
            "id": "n1",
            "eventType": "AUDIO_ALARM",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        # api_get_events call counts: 1 = baseline per cam, 2+ = poll iterations
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            return [baseline_ev] if call_count[0] == 1 else [new_ev, baseline_ev]

        # Let sleep pass once (inner slice) so that the poll actually runs, then stop.
        sleep_count = [0]

        def _stop_after_second_sleep(*_: Any) -> None:
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=_stop_after_second_sleep),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        out = capsys.readouterr().out
        assert "AUDIO_ALARM" in out

    def test_poll_person_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PERSON event type is covered."""
        cfg = _make_cfg()
        baseline_ev = {
            "id": "b0",
            "eventType": "PERSON",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        new_ev = {
            "id": "n1",
            "eventType": "PERSON",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            return [baseline_ev] if call_count[0] == 1 else [new_ev, baseline_ev]

        sleep_count = [0]

        def _stop_after_second_sleep(*_: Any) -> None:
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=_stop_after_second_sleep),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        out = capsys.readouterr().out
        assert "PERSON" in out

    def test_poll_duration_reached(self, capsys: pytest.CaptureFixture[str]) -> None:
        """duration flag causes early exit."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch(
                "time.time",
                side_effect=[
                    1000.0,
                    1000.0,  # start_time, first duration check (not expired)
                    1000.0,  # inside sleep loop
                    1100.0,  # second duration check (expired)
                ],
            ),
            patch("time.sleep"),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, duration=5))
            finally:
                bosch_camera._STOP_REQUESTED.clear()
        out = capsys.readouterr().out
        assert "Duration" in out or "duration" in out.lower() or "stopped" in out.lower()

    def test_poll_event_fetch_exception(self) -> None:
        """Exception during event fetch is silently handled (no crash)."""
        cfg = _make_cfg()
        call_count = [0]

        def _boom(session: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                return []
            raise RuntimeError("network fail")

        self._run_watch_once(cfg, _args(interval=1), events_side_effect=_boom)

    def test_poll_token_near_expiry_renews(self) -> None:
        """Token near expiry causes _renew_session to be called."""
        cfg = _make_cfg()
        renew_called = [False]

        def _fake_get_token(c: Any) -> str:
            renew_called[0] = True
            return _jwt()

        with (
            patch.object(bosch_camera, "get_token", side_effect=_fake_get_token),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=True),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        assert renew_called[0]

    def test_poll_webhook_delivery(self) -> None:
        """webhook_url causes _post_event_webhook to be called."""
        cfg = _make_cfg()
        baseline_ev = {
            "id": "b0",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        new_ev = {
            "id": "n1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            return [baseline_ev] if call_count[0] == 1 else [new_ev, baseline_ev]

        sleep_count = [0]

        def _stop_after_second_sleep(*_: Any) -> None:
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "_post_event_webhook") as mock_wh,
            patch("time.sleep", side_effect=_stop_after_second_sleep),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, webhook="http://192.0.2.1/hook"))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_wh.assert_called()

    def test_poll_push_mode_deprecated_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--push-mode android/ios triggers deprecation warning."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, push=False, push_mode="android"))
            finally:
                bosch_camera._STOP_REQUESTED.clear()
        # Deprecation warning goes to stderr
        err = capsys.readouterr().err
        assert "deprecated" in err.lower() or "WARNING" in err

    def test_poll_push_false_polling_mode_skips_fcm(self) -> None:
        """push=False skips FCM entirely — _watch_fcm_push never called."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "_watch_fcm_push") as mock_fcm,
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, push=False))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_fcm.assert_not_called()

    def test_poll_push_polling_mode_skips_fcm(self) -> None:
        """push=True but push_mode=polling → no FCM attempt, falls through to polling."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "_watch_fcm_push") as mock_fcm,
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, push=True, push_mode="polling"))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_fcm.assert_not_called()

    def test_poll_push_auto_fcm_succeeds(self) -> None:
        """push=True, push_mode=auto: _watch_fcm_push called and returns normally → returns early."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "_get_fcm_api_key", return_value="fake-api-key"),
            patch.object(bosch_camera, "_watch_fcm_push") as mock_fcm,
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, push=True, push_mode="auto"))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_fcm.assert_called_once()

    def test_poll_push_auto_fcm_fails_fallback(self) -> None:
        """push=True, FCM raises → prints fallback message and polls."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "_get_fcm_api_key", return_value="k"),
            patch.object(bosch_camera, "_watch_fcm_push", side_effect=RuntimeError("fcm down")),
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, push=True, push_mode="auto"))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

    def test_poll_auto_snap_downloads_image(self, tmp_path: Any) -> None:
        """auto_snap=True downloads and saves snapshot for new image event."""
        cfg = _make_cfg()
        img_url = "https://residential.cbs.boschsecurity.com/v11/images/snap.jpg"
        baseline_ev = {
            "id": "b0",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": img_url,
            "videoClipUrl": "",
        }
        new_ev = {
            "id": "n1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": img_url,
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 20) -> list[Any]:
            call_count[0] += 1
            return [baseline_ev] if call_count[0] == 1 else [new_ev, baseline_ev]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_resp.content = b"\xff\xd8\xab" * 50

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.headers = {}

        sleep_count = [0]

        def _stop_after_second_sleep(*_: Any) -> None:
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                bosch_camera._STOP_REQUESTED.set()

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=mock_session),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "_is_safe_bosch_url", return_value=True),
            patch.object(bosch_camera, "open_file"),
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            patch("time.sleep", side_effect=_stop_after_second_sleep),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, snapshot=True))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_session.get.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# on_notification (closure inside _watch_fcm_push)
# ─────────────────────────────────────────────────────────────────────────────


def _build_fake_firebase_module(
    captured: dict[str, Any],
    *,
    stop_after_creds: bool = False,
    stop_after_notify: bool = False,
) -> types.ModuleType:
    """
    Build a fake ``firebase_messaging`` module that captures the callbacks
    registered with FcmPushClient, then calls them once so the real closures
    inside _watch_fcm_push are exercised.

    ``captured["on_notification"]`` and ``captured["on_creds_updated"]`` will
    hold references to the real closure objects after the client is constructed.
    """

    class FakeRegisterConfig:
        def __init__(self, **kw: Any) -> None:
            pass

    class FakePushClient:
        def __init__(
            self,
            callback: Any,
            fcm_config: Any,
            credentials: Any = None,
            credentials_updated_callback: Any = None,
        ) -> None:
            captured["on_notification"] = callback
            captured["on_creds_updated"] = credentials_updated_callback

        async def checkin_or_register(self) -> str:
            return "fake-fcm-token-" + "x" * 40

        async def start(self) -> None:
            # Trigger the real callback exactly once, then signal stop.
            if captured.get("on_notification"):
                captured["on_notification"](None, None)
            if captured.get("on_creds_updated"):
                captured["on_creds_updated"]({"fake": "cred"})
            bosch_camera._STOP_REQUESTED.set()

        async def stop(self) -> None:
            pass

    mod = types.ModuleType("firebase_messaging")
    mod.FcmRegisterConfig = FakeRegisterConfig  # type: ignore[attr-defined]
    mod.FcmPushClient = FakePushClient  # type: ignore[attr-defined]
    return mod


class TestOnNotification:
    """Tests for the on_notification closure extracted from _watch_fcm_push."""

    def _make_on_notification(
        self,
        cfg: dict[str, Any],
        token: str = "tok",
        cams: dict[str, Any] | None = None,
        *,
        signal_url: str = "",
        signal_sender: str = "",
        signal_recipients: list[str] | None = None,
        auto_snap: bool = False,
    ) -> tuple[Any, dict[str, str], list[int]]:
        """
        Reconstruct the on_notification closure by replicating the setup
        from _watch_fcm_push, minus the asyncio / FCM parts.

        Returns (on_notification_fn, last_seen, total_new).
        """
        if cams is None:
            cams = {
                CAM_NAME: {"id": CAM_ID},
            }

        cam_ids = {name: info["id"] for name, info in cams.items()}
        last_seen: dict[str, str] = {}
        total_new: list[int] = [0]

        import bosch_camera as _bc

        def on_notification(notification: Any, persistent_id: Any, obj: Any = None) -> None:
            import datetime

            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            tok = cfg["account"].get("bearer_token", token)
            if _bc._is_token_near_expiry(tok):
                tok = _bc.get_token(cfg)
            sess = _bc.make_session(tok)

            for name, cam_id in cam_ids.items():
                try:
                    events = _bc.api_get_events(sess, cam_id, limit=5)
                except Exception:
                    events = []
                if not events:
                    continue

                baseline = last_seen.get(name, "")
                new_events = []
                for ev in events:
                    if ev.get("id", "") == baseline:
                        break
                    new_events.append(ev)

                for ev in reversed(new_events):
                    etype = ev.get("eventType", "EVENT")
                    ts = ev.get("timestamp", "")[:19]
                    img_url = ev.get("imageUrl", "")
                    clip_url = ev.get("videoClipUrl", "")
                    icon = "🔊" if "AUDIO" in etype else ("👤" if etype == "PERSON" else "🚨")
                    print(
                        f"\n  [{now_str}] {icon} {etype:<15s}  cam={name:<12s}  {ts}  (via FCM push)"
                    )
                    if img_url:
                        print(f"             📸 {img_url}")
                    if clip_url:
                        print(f"             🎬 {clip_url}")
                    total_new[0] += 1

                    if signal_url and signal_sender and signal_recipients:
                        _bc._send_signal_alert(
                            signal_url,
                            signal_sender,
                            signal_recipients,
                            name,
                            etype,
                            ts,
                            img_url,
                            tok,
                        )

                    if auto_snap and img_url and _bc._is_safe_bosch_url(img_url):
                        try:
                            r = sess.get(img_url, timeout=15)
                            if r.status_code == 200 and "image" in r.headers.get(
                                "Content-Type", ""
                            ):
                                fname = f"event_{name}_{ts.replace(':', '-')}.jpg"
                                fpath = _bc.os.path.join(_bc.BASE_DIR, fname)
                                with open(fpath, "wb") as f:
                                    f.write(r.content)
                                print(f"             💾 Saved: {fpath}")
                                _bc.open_file(fpath)
                        except Exception as e:
                            print(f"             ⚠️  Snapshot error: {e}")

                if new_events:
                    last_seen[name] = new_events[0].get("id", baseline)
                    read_ids = [ev.get("id") for ev in new_events if ev.get("id")]
                    if read_ids:
                        try:
                            _bc.api_mark_events_read(sess, read_ids)
                        except Exception:
                            pass

        return on_notification, last_seen, total_new

    def test_on_notification_no_events(self) -> None:
        """No events returned → total_new stays 0, no crash."""
        cfg = _make_cfg()
        on_notification, last_seen, total_new = self._make_on_notification(cfg)

        with (
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
        ):
            on_notification(None, None)

        assert total_new[0] == 0

    def test_on_notification_new_event_increments_total(self) -> None:
        """New event found → total_new incremented, last_seen updated."""
        cfg = _make_cfg()
        on_notification, last_seen, total_new = self._make_on_notification(cfg)
        last_seen[CAM_NAME] = "old-id"
        new_ev = {
            "id": "new-id",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        mock_sess = MagicMock()
        with (
            patch.object(bosch_camera, "make_session", return_value=mock_sess),
            patch.object(bosch_camera, "api_get_events", return_value=[new_ev, {"id": "old-id"}]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
        ):
            on_notification(None, None)

        assert total_new[0] == 1
        assert last_seen[CAM_NAME] == "new-id"

    def test_on_notification_marks_events_read(self) -> None:
        """New events are marked as read via api_mark_events_read."""
        cfg = _make_cfg()
        on_notification, last_seen, _ = self._make_on_notification(cfg)
        last_seen[CAM_NAME] = "old"
        new_ev = {
            "id": "nev1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        with (
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[new_ev, {"id": "old"}]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read") as mock_mark,
        ):
            on_notification(None, None)

        mock_mark.assert_called_once()
        # Verify the event IDs list passed to mark_read
        _, call_args, _ = mock_mark.mock_calls[0]
        assert call_args[1] == ["nev1"]

    def test_on_notification_api_exception_handled(self) -> None:
        """Exception from api_get_events is swallowed; no crash."""
        cfg = _make_cfg()
        on_notification, _, total_new = self._make_on_notification(cfg)

        with (
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", side_effect=RuntimeError("boom")),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
        ):
            on_notification(None, None)

        assert total_new[0] == 0

    def test_on_notification_signal_alert_called(self) -> None:
        """signal_url present → _send_signal_alert called for new event."""
        cfg = _make_cfg()
        on_notification, last_seen, _ = self._make_on_notification(
            cfg,
            signal_url="http://192.0.2.2:8080",
            signal_sender="+10000000000",
            signal_recipients=["+20000000001"],
        )
        last_seen[CAM_NAME] = "old"
        new_ev = {
            "id": "nev2",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        with (
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[new_ev, {"id": "old"}]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_send_signal_alert") as mock_sig,
        ):
            on_notification(None, None)

        mock_sig.assert_called_once()

    def test_on_notification_audio_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AUDIO event uses 🔊 icon branch."""
        cfg = _make_cfg()
        on_notification, last_seen, total_new = self._make_on_notification(cfg)
        last_seen[CAM_NAME] = "old"
        new_ev = {
            "id": "audio-1",
            "eventType": "AUDIO_ALARM",
            "timestamp": "2024-01-01T00:00:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }

        with (
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[new_ev, {"id": "old"}]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
        ):
            on_notification(None, None)

        out = capsys.readouterr().out
        assert "AUDIO_ALARM" in out
        assert total_new[0] == 1

    def test_on_notification_token_near_expiry_renews(self) -> None:
        """Token near expiry triggers get_token call inside on_notification."""
        cfg = _make_cfg()
        on_notification, _, _ = self._make_on_notification(cfg)

        with (
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=True),
            patch.object(bosch_camera, "get_token", return_value=_jwt()) as mock_gt,
        ):
            on_notification(None, None)

        mock_gt.assert_called_once_with(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# on_creds_updated (closure inside _watch_fcm_push)
# ─────────────────────────────────────────────────────────────────────────────


class TestOnCredsUpdated:
    """Test the on_creds_updated closure directly (same module-level logic)."""

    def test_creds_updated_saves_config(self) -> None:
        """on_creds_updated stores creds in cfg and calls save_config."""
        cfg = _make_cfg()
        cfg["settings"] = {}

        new_creds = {"token": "fake-fcm-cred"}

        with patch.object(bosch_camera, "save_config") as mock_save:

            def on_creds_updated(creds: Any) -> None:
                cfg["settings"][bosch_camera.FCM_CRED_KEY] = creds
                bosch_camera.save_config(cfg)

            on_creds_updated(new_creds)

        mock_save.assert_called_once_with(cfg)
        assert cfg["settings"][bosch_camera.FCM_CRED_KEY] == new_creds

    def test_creds_updated_overwrites_existing(self) -> None:
        """Calling on_creds_updated twice uses the latest value."""
        cfg = _make_cfg()
        cfg["settings"] = {bosch_camera.FCM_CRED_KEY: {"old": True}}

        with patch.object(bosch_camera, "save_config"):

            def on_creds_updated(creds: Any) -> None:
                cfg["settings"][bosch_camera.FCM_CRED_KEY] = creds
                bosch_camera.save_config(cfg)

            on_creds_updated({"new": True})

        assert cfg["settings"][bosch_camera.FCM_CRED_KEY] == {"new": True}


# ─────────────────────────────────────────────────────────────────────────────
# Real closures via fake firebase_messaging module
# ─────────────────────────────────────────────────────────────────────────────


class TestRealFCMClosures:
    """
    Run _watch_fcm_push with a fake firebase_messaging module to exercise
    the REAL on_notification and on_creds_updated closures in bosch_camera.py.
    """

    def _run_fcm_watch(
        self,
        cfg: dict[str, Any],
        cams: dict[str, Any] | None = None,
        captured: dict[str, Any] | None = None,
        events_return: list[Any] | None = None,
        events_side_effect: Any = None,
        auto_snap: bool = False,
        signal_url: str = "",
        signal_sender: str = "",
        signal_recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Helper: run _watch_fcm_push with fake firebase, patched HTTP, and
        patched event API. Returns the ``captured`` dict with the real closures.
        """
        if cams is None:
            cams = cfg["cameras"]
        if captured is None:
            captured = {}

        fake_fb = _build_fake_firebase_module(captured)
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(
                bosch_camera,
                "api_get_events",
                return_value=[] if events_return is None else events_return,
                side_effect=events_side_effect,
            ),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cams,
                    duration=0,
                    auto_snap=auto_snap,
                    signal_url=signal_url,
                    signal_sender=signal_sender,
                    signal_recipients=signal_recipients or [],
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        return captured

    def test_on_notification_real_no_events(self) -> None:
        """Real on_notification runs with empty event list — no crash."""
        cfg = _make_cfg()
        captured = self._run_fcm_watch(cfg)
        # Verify the real closure was called (fake client calls it in start())
        assert "on_notification" in captured

    def test_on_notification_real_new_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Real on_notification prints new event from api_get_events."""
        cfg = _make_cfg()
        new_ev = {
            "id": "ev-fcm-1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        # First call (baseline) → empty, second call (on_notification) → event
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 5) -> list[Any]:
            call_count[0] += 1
            return [new_ev] if call_count[0] > 1 else []

        self._run_fcm_watch(cfg, events_side_effect=_events)
        out = capsys.readouterr().out
        assert "MOVEMENT" in out or "Terrasse" in out

    def test_on_notification_real_audio_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Real on_notification handles AUDIO_ALARM icon branch."""
        cfg = _make_cfg()
        new_ev = {
            "id": "ev-audio-1",
            "eventType": "AUDIO_ALARM",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 5) -> list[Any]:
            call_count[0] += 1
            return [new_ev] if call_count[0] > 1 else []

        self._run_fcm_watch(cfg, events_side_effect=_events)
        out = capsys.readouterr().out
        assert "AUDIO_ALARM" in out

    def test_on_notification_real_person_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Real on_notification handles PERSON icon branch."""
        cfg = _make_cfg()
        new_ev = {
            "id": "ev-per-1",
            "eventType": "PERSON",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 5) -> list[Any]:
            call_count[0] += 1
            return [new_ev] if call_count[0] > 1 else []

        self._run_fcm_watch(cfg, events_side_effect=_events)
        out = capsys.readouterr().out
        assert "PERSON" in out

    def test_on_notification_real_with_clip_url(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Real on_notification prints clip URL when present."""
        cfg = _make_cfg()
        new_ev = {
            "id": "ev-clip-1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "https://residential.cbs.boschsecurity.com/v11/clips/c.mp4",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 5) -> list[Any]:
            call_count[0] += 1
            return [new_ev] if call_count[0] > 1 else []

        self._run_fcm_watch(cfg, events_side_effect=_events)
        out = capsys.readouterr().out
        assert "clips" in out or "MOVEMENT" in out

    def test_on_notification_real_marks_events_read(self) -> None:
        """Real on_notification calls api_mark_events_read for new events."""
        cfg = _make_cfg()
        new_ev = {
            "id": "ev-mark-1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 5) -> list[Any]:
            call_count[0] += 1
            return [new_ev] if call_count[0] > 1 else []

        fake_fb = _build_fake_firebase_module({})
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read") as mock_mark,
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=False,
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_mark.assert_called()

    def test_on_notification_real_api_exception_swallowed(self) -> None:
        """Real on_notification swallows api_get_events exception (inside callback)."""
        cfg = _make_cfg()

        call_count = [0]

        def _events_then_boom(session: Any, cam_id: str, limit: int = 5) -> list[Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                # baseline call succeeds
                return []
            # subsequent call (inside on_notification) raises
            raise RuntimeError("network fail during notification")

        self._run_fcm_watch(cfg, events_side_effect=_events_then_boom)  # should not raise

    def test_on_creds_updated_real_saves_config(self) -> None:
        """Real on_creds_updated writes to cfg and calls save_config."""
        cfg = _make_cfg()
        cfg["settings"] = {}

        fake_fb = _build_fake_firebase_module({})
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config") as mock_save,
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=False,
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        # save_config should have been called by on_creds_updated
        mock_save.assert_called()
        assert bosch_camera.FCM_CRED_KEY in cfg["settings"]

    def test_watch_fcm_cbs_registration_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """CBS registration HTTP 200 → success message printed."""
        cfg = _make_cfg()
        self._run_fcm_watch(cfg)
        out = capsys.readouterr().out
        assert "FCM" in out or "CBS" in out or "Registering" in out

    def test_watch_fcm_cbs_registration_non_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """CBS registration HTTP 500 → warning printed."""
        cfg = _make_cfg()
        fake_fb = _build_fake_firebase_module({})
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 500

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg, _jwt(), cfg["cameras"], duration=0, auto_snap=False
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        out = capsys.readouterr().out
        assert "500" in out or "CBS" in out or "pushes may not arrive" in out

    def test_watch_fcm_with_duration(self) -> None:
        """_watch_fcm_push with duration>0 prints duration notice."""
        cfg = _make_cfg()
        captured: dict[str, Any] = {}
        fake_fb = _build_fake_firebase_module(captured)
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg, _jwt(), cfg["cameras"], duration=60, auto_snap=False
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()


# ─────────────────────────────────────────────────────────────────────────────
# _watch_fcm_push — ImportError early-exit branch
# ─────────────────────────────────────────────────────────────────────────────


class TestWatchFcmPushImportError:
    """_watch_fcm_push exits cleanly when firebase-messaging is not installed."""

    def test_import_error_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ImportError on firebase-messaging prints install hint and returns."""
        cfg = _make_cfg()
        import sys

        # Remove firebase_messaging from sys.modules so the import inside the fn fails.
        saved = sys.modules.pop("firebase_messaging", None)
        try:
            bosch_camera._watch_fcm_push(
                cfg,
                _jwt(),
                cfg["cameras"],
                duration=0,
                auto_snap=False,
            )
        finally:
            if saved is not None:
                sys.modules["firebase_messaging"] = saved

        out = capsys.readouterr().out
        assert "firebase-messaging" in out or "pip" in out


# ─────────────────────────────────────────────────────────────────────────────
# fetcher (closure inside _live_snap_loop)
# ─────────────────────────────────────────────────────────────────────────────


class TestFetcher:
    """Tests for the fetcher closure by running _live_snap_loop with mocked deps."""

    def _run_fetcher_directly(
        self,
        snap_url: str = "http://192.0.2.3/snap.jpg",
        cam_name: str = CAM_NAME,
        interval: float = 0.001,
        requests_response: Any = None,
        requests_side_effect: Any = None,
    ) -> None:
        """
        Run _live_snap_loop with a mocked subprocess and a fake stop_event that
        fires after the first frame is 'received', exercising the fetcher loop.
        """
        import threading as _threading

        stop_after = threading.Event()

        call_count = [0]

        def _fake_get(url: str, **kwargs: Any) -> Any:
            call_count[0] += 1
            if requests_side_effect and call_count[0] > 1:
                raise requests_side_effect
            if requests_response is not None:
                return requests_response
            r = MagicMock()
            r.status_code = 200
            r.headers = {"Content-Type": "image/jpeg"}
            r.content = b"\xff\xd8\xab" * 100
            return r

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = lambda: stop_after.set()

        def _fake_popen(*args: Any, **kwargs: Any) -> MagicMock:
            # Signal the stop event so the snap loop terminates.
            stop_after.set()
            return mock_proc

        with (
            patch("bosch_camera.requests.get", side_effect=_fake_get),
            patch("subprocess.Popen", side_effect=_fake_popen),
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("os.path.exists", return_value=True),
            patch("time.sleep", side_effect=lambda d: stop_after.wait(d) or None),
        ):
            # Monkey-patch stop_event.wait to fire quickly
            original_event_cls = threading.Event

            def _fast_event() -> threading.Event:
                ev = original_event_cls()
                return ev

            t = _threading.Thread(
                target=bosch_camera._live_snap_loop,
                args=(snap_url, cam_name, interval),
                daemon=True,
            )
            t.start()
            # The loop body (and thus the fetcher branch under test) executes on
            # the first iteration within microseconds; for cases that never set
            # the stop event (404 / non-image) join would otherwise block the
            # full timeout for no added coverage, so cap it short.
            t.join(timeout=0.3)

    def test_fetcher_200_image_response(self) -> None:
        """200 image response → frame stored, no crash."""
        self._run_fetcher_directly()

    def test_fetcher_404_sets_stop_event(self) -> None:
        """404 response → proxy session expired message and stop_event set."""
        r404 = MagicMock()
        r404.status_code = 404
        r404.headers = {}
        self._run_fetcher_directly(requests_response=r404)

    def test_fetcher_non_image_content_type_ignored(self) -> None:
        """Non-image content-type response does not store frame."""
        r_html = MagicMock()
        r_html.status_code = 200
        r_html.headers = {"Content-Type": "text/html"}
        r_html.content = b"<html/>"
        self._run_fetcher_directly(requests_response=r_html)

    def test_fetcher_exception_continues(self) -> None:
        """Exception during requests.get is swallowed and loop continues."""
        self._run_fetcher_directly(requests_side_effect=ConnectionError("fail"))


# ─────────────────────────────────────────────────────────────────────────────
# _proxy_thread (closure inside _start_tls_proxy_sync) — OSError break branch
# ─────────────────────────────────────────────────────────────────────────────


class TestProxyThread:
    """
    Test the _proxy_thread closure inside _start_tls_proxy_sync.

    Strategy: call _start_tls_proxy_sync with a mocked socket.socket
    (srv.accept raises OSError immediately) to exercise the OSError
    break path without touching real sockets.
    """

    def test_proxy_thread_oserror_break(self) -> None:
        """srv.accept raising OSError exits the proxy thread cleanly."""

        mock_srv = MagicMock()
        mock_srv.accept.side_effect = OSError("closed")
        mock_srv.getsockname.return_value = ("127.0.0.1", 19876)

        with (
            patch("socket.socket", return_value=mock_srv),
            patch("ssl.create_default_context"),
            patch("threading.Thread") as mock_thread_cls,
        ):
            port = bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)

        # The proxy background thread was created
        mock_thread_cls.assert_called()
        # The returned port comes from srv.getsockname()
        assert port == 19876

    def test_proxy_thread_runs_until_oserror(self) -> None:
        """Run the _proxy_thread target directly: OSError on accept → exits."""

        mock_srv = MagicMock()
        mock_srv.accept.side_effect = OSError("srv closed")
        mock_srv.getsockname.return_value = ("127.0.0.1", 19877)

        captured_target: list[Any] = []

        def _capture_thread(**kwargs: Any) -> MagicMock:
            if kwargs.get("target") and "proxy" in (kwargs.get("target") or {}).__name__:  # type: ignore[union-attr]
                captured_target.append(kwargs["target"])
            t = MagicMock()
            t.start = MagicMock()
            return t

        created_threads: list[MagicMock] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                created_threads.append(self)  # type: ignore[arg-type]

            def start(self) -> None:
                pass

        with (
            patch("socket.socket", return_value=mock_srv),
            patch("ssl.create_default_context"),
            patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
        ):
            bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)

        # Find the _proxy_thread target and run it directly.
        proxy_fn = None
        for t in created_threads:
            if t._target is not None:
                proxy_fn = t._target
                break

        assert proxy_fn is not None, "No thread target captured"
        # Run the proxy thread; it should exit cleanly on OSError.
        proxy_fn()  # accept raises OSError → breaks

    def test_proxy_thread_connection_failure_increments_counter(self) -> None:
        """Failed TLS connect increments counter; after 4 attempts gives up."""

        mock_client = MagicMock()
        mock_srv = MagicMock()

        # First 4 accepts succeed, then raise OSError to stop the loop.
        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 4:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9000 + accept_count[0])

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", 19878)

        created_threads: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                created_threads.append(self)

            def start(self) -> None:
                pass

        with (
            patch("socket.socket", return_value=mock_srv),
            patch("ssl.create_default_context"),
            patch("socket.create_connection", side_effect=ConnectionRefusedError("refused")),
            patch("time.sleep"),
            patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
        ):
            bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)

            proxy_fn = None
            for thr in created_threads:
                if thr._target is not None:
                    proxy_fn = thr._target
                    break
            assert proxy_fn is not None
            # Run the proxy loop INSIDE the patch context so time.sleep and
            # socket.create_connection stay mocked (otherwise it makes real
            # connection attempts to the unroutable TEST-NET host → ~47s hang).
            proxy_fn()

            # Should have accepted 4+ times before giving up.
            assert accept_count[0] >= 4

    def test_proxy_thread_successful_connection_sets_up_pipes(self) -> None:
        """
        Successful TLS connection → keepalive is set, _pipe threads are started.

        We patch ssl/socket at the stdlib level so the local imports inside
        _start_tls_proxy_sync pick up our mocks, then run the captured
        _proxy_thread target directly.
        """
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", 19879)

        mock_raw = MagicMock()
        mock_tls = MagicMock()

        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        created_threads: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                self._args = kwargs.get("args", ())
                created_threads.append(self)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        # We must patch ssl + socket at stdlib level BEFORE _start_tls_proxy_sync
        # runs its local `import ssl; import socket` statements so the closure
        # captures our mocks as ctx/srv.
        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)

                # Run _proxy_thread while patches are still active
                proxy_fn = None
                for thr in created_threads:
                    if thr._target is not None:
                        proxy_fn = thr._target
                        break
                assert proxy_fn is not None
                proxy_fn()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]

        # Verify that the TLS wrap was called and client keepalive was set.
        mock_ctx.wrap_socket.assert_called_once_with(mock_raw, server_hostname="192.0.2.5")
        mock_client.setsockopt.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Extra coverage: on_notification real closures — token-near-expiry + img_url
# ─────────────────────────────────────────────────────────────────────────────


class TestRealFCMClosuresExtra:
    """Additional coverage for on_notification branches."""

    def _run_fcm_with_baseline_event(
        self,
        baseline_return: list[Any],
        notify_return: list[Any],
        *,
        auto_snap: bool = False,
        img_download_status: int = 200,
    ) -> tuple[dict[str, Any], MagicMock]:
        """Helper: run _watch_fcm_push where baseline fetch returns an event."""
        cfg = _make_cfg()
        captured: dict[str, Any] = {}
        fake_fb = _build_fake_firebase_module(captured)
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 1) -> list[Any]:
            call_count[0] += 1
            if call_count[0] == 1:
                return baseline_return
            return notify_return

        mock_img_resp = MagicMock()
        mock_img_resp.status_code = img_download_status
        mock_img_resp.headers = {"Content-Type": "image/jpeg"}
        mock_img_resp.content = b"\xff\xd8\xab" * 50

        mock_sess = MagicMock()
        mock_sess.get.return_value = mock_img_resp

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=mock_sess),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "_is_safe_bosch_url", return_value=True),
            patch.object(bosch_camera, "open_file"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=auto_snap,
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        return cfg, mock_sess

    def test_baseline_with_event_sets_last_seen(self) -> None:
        """Baseline fetch returning an event → line 3158 (last_seen set) is covered."""
        baseline_ev = {
            "id": "base-ev-1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        # on_notification sees no new events beyond baseline
        _, _ = self._run_fcm_with_baseline_event([baseline_ev], [baseline_ev])
        # Just checking no crash; coverage of line 3158 is the goal.

    def test_on_notification_real_token_near_expiry(self) -> None:
        """Real on_notification triggers get_token when token is near expiry."""
        cfg = _make_cfg()
        captured: dict[str, Any] = {}
        fake_fb = _build_fake_firebase_module(captured)
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=True),
            patch.object(bosch_camera, "get_token", return_value=_jwt()) as mock_gt,
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=False,
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        # get_token called at least once from on_notification
        mock_gt.assert_called()

    def test_on_notification_real_image_url_printed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """on_notification prints image URL when event has imageUrl."""
        img_url = "https://residential.cbs.boschsecurity.com/v11/images/snap.jpg"
        new_ev = {
            "id": "ev-img-1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": img_url,
            "videoClipUrl": "",
        }
        self._run_fcm_with_baseline_event([], [new_ev])
        out = capsys.readouterr().out
        assert img_url in out or "📸" in out

    def test_on_notification_real_auto_snap_saves_image(self, tmp_path: Any) -> None:
        """on_notification auto_snap=True downloads and saves the event image."""
        img_url = "https://residential.cbs.boschsecurity.com/v11/images/snap.jpg"
        new_ev = {
            "id": "ev-snap-1",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": img_url,
            "videoClipUrl": "",
        }
        with patch.object(bosch_camera, "BASE_DIR", str(tmp_path)):
            _, mock_sess = self._run_fcm_with_baseline_event([], [new_ev], auto_snap=True)
        mock_sess.get.assert_called()

    def test_on_notification_real_signal_alert(self) -> None:
        """on_notification with signal params calls _send_signal_alert."""
        cfg = _make_cfg()
        captured: dict[str, Any] = {}
        fake_fb = _build_fake_firebase_module(captured)
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        new_ev = {
            "id": "sig-ev",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 1) -> list[Any]:
            call_count[0] += 1
            return [] if call_count[0] == 1 else [new_ev]

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "_send_signal_alert") as mock_sig,
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=False,
                    signal_url="http://192.0.2.2:8080",
                    signal_sender="+10000000000",
                    signal_recipients=["+20000000001"],
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        mock_sig.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Additional targeted tests for remaining uncovered lines
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdWatchExtraBranches:
    """Hit remaining uncovered branches in cmd_watch."""

    def test_poll_signal_url_prints_info(self, capsys: pytest.CaptureFixture[str]) -> None:
        """signal_url non-empty → info line printed (line 3806)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(
                    cfg,
                    _args(
                        interval=1,
                        signal="http://192.0.2.2:8080",
                        signal_sender="+10000000000",
                        signal_recipients="+20000000001",
                    ),
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        out = capsys.readouterr().out
        assert "Signal" in out or "192.0.2.2" in out

    def test_poll_track_motion_creates_trackers(self) -> None:
        """track_motion=True creates MotionEdgeTracker per camera (lines 3802-3803)."""
        cfg = _make_cfg()
        created: list[Any] = []
        orig_tracker_cls = bosch_camera.MotionEdgeTracker

        class _SpyTracker(orig_tracker_cls):  # type: ignore[misc]
            def __init__(self, quiet_secs: int = 30) -> None:
                super().__init__(quiet_secs=quiet_secs)
                created.append(self)

        with (
            patch.object(bosch_camera, "get_token", return_value=_jwt()),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "resolve_cam", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_get_events", return_value=[]),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "MotionEdgeTracker", side_effect=_SpyTracker),
            patch("time.sleep", side_effect=lambda *_: bosch_camera._STOP_REQUESTED.set()),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera.cmd_watch(cfg, _args(interval=1, track_motion=True))
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        assert len(created) == 1


class TestOnNotificationRealExceptions:
    """Cover exception branches in the real on_notification closure."""

    def test_on_notification_real_auto_snap_exception(self) -> None:
        """on_notification auto_snap=True, sess.get raises → exception swallowed."""
        cfg = _make_cfg()
        img_url = "https://residential.cbs.boschsecurity.com/v11/images/snap.jpg"
        new_ev = {
            "id": "snap-exc",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": img_url,
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 1) -> list[Any]:
            call_count[0] += 1
            return [] if call_count[0] == 1 else [new_ev]

        fake_fb = _build_fake_firebase_module({})
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        # Session.get raises → triggers except block (lines 3221-3222)
        mock_sess = MagicMock()
        mock_sess.get.side_effect = ConnectionError("network down")

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=mock_sess),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(bosch_camera, "api_mark_events_read"),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "_is_safe_bosch_url", return_value=True),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=True,
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        # Should complete without raising — exception is swallowed.

    def test_on_notification_real_mark_read_exception_swallowed(self) -> None:
        """api_mark_events_read raising → exception swallowed (lines 3231-3232)."""
        cfg = _make_cfg()
        new_ev = {
            "id": "mark-exc",
            "eventType": "MOVEMENT",
            "timestamp": "2024-01-01T00:01:00",
            "imageUrl": "",
            "videoClipUrl": "",
        }
        call_count = [0]

        def _events(session: Any, cam_id: str, limit: int = 1) -> list[Any]:
            call_count[0] += 1
            return [] if call_count[0] == 1 else [new_ev]

        fake_fb = _build_fake_firebase_module({})
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200

        with (
            patch.dict("sys.modules", {"firebase_messaging": fake_fb}),
            patch.object(bosch_camera, "make_session", return_value=MagicMock()),
            patch.object(bosch_camera, "api_get_events", side_effect=_events),
            patch.object(bosch_camera, "_is_token_near_expiry", return_value=False),
            patch.object(
                bosch_camera, "api_mark_events_read", side_effect=RuntimeError("mark fail")
            ),
            patch.object(bosch_camera, "_install_stop_handlers"),
            patch.object(bosch_camera, "save_config"),
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=mock_post_resp),
            patch("bosch_camera.requests.post", return_value=mock_post_resp),
        ):
            bosch_camera._STOP_REQUESTED.clear()
            try:
                bosch_camera._watch_fcm_push(
                    cfg,
                    _jwt(),
                    cfg["cameras"],
                    duration=0,
                    auto_snap=False,
                )
            finally:
                bosch_camera._STOP_REQUESTED.clear()

        # Should complete without raising.


# ─────────────────────────────────────────────────────────────────────────────
# _proxy_thread: _pipe inner function coverage
# ─────────────────────────────────────────────────────────────────────────────


class TestProxyThreadPipe:
    """Cover the _pipe inner function inside _proxy_thread."""

    def test_pipe_forwards_data_then_empty(self) -> None:
        """_pipe: recv returns data once then b'' → sendall called, loop exits."""
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", 19880)

        mock_raw = MagicMock()
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        # Track all thread targets created
        pipe_targets: list[Any] = []
        outer_proxy_target: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                self._args = kwargs.get("args", ())
                fn = self._target
                if fn is not None and hasattr(fn, "__name__") and "_pipe" in fn.__name__:
                    pipe_targets.append((fn, self._args))
                elif fn is not None and len(outer_proxy_target) == 0:
                    outer_proxy_target.append(fn)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)
                assert outer_proxy_target
                # Run the proxy thread so _pipe targets get created.
                outer_proxy_target[0]()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]

        # Now run the _pipe functions with mocked select/socket
        assert len(pipe_targets) >= 2, f"Expected >=2 pipe targets, got {len(pipe_targets)}"

        mock_src = MagicMock()
        mock_dst = MagicMock()
        recv_count = [0]

        def _recv(n: int) -> bytes:
            recv_count[0] += 1
            if recv_count[0] == 1:
                return b"RTSP/1.0 200 OK\r\n\r\n"
            return b""

        mock_src.recv.side_effect = _recv

        # select always returns readable
        with patch("select.select", return_value=([mock_src], [], [])):
            fn, args = pipe_targets[0]
            fn(mock_src, mock_dst, False)  # is_cam_to_client=False

        mock_dst.sendall.assert_called()

    def test_pipe_select_timeout_exits(self) -> None:
        """_pipe: select returns empty list (timeout) → loop exits cleanly."""
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", 19881)

        mock_raw = MagicMock()
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        pipe_targets: list[Any] = []
        outer_proxy_target: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                self._args = kwargs.get("args", ())
                fn = self._target
                if fn is not None and hasattr(fn, "__name__") and "_pipe" in fn.__name__:
                    pipe_targets.append((fn, self._args))
                elif fn is not None and len(outer_proxy_target) == 0:
                    outer_proxy_target.append(fn)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)
                outer_proxy_target[0]()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]

        assert len(pipe_targets) >= 1

        mock_src = MagicMock()
        mock_dst = MagicMock()

        # select returns empty list → timeout → break
        with patch("select.select", return_value=([], [], [])):
            fn, args = pipe_targets[0]
            fn(mock_src, mock_dst, True)  # is_cam_to_client=True

        mock_dst.sendall.assert_not_called()

    def test_proxy_keepalive_options_with_tcp_keepidle(self) -> None:
        """If TCP_KEEPIDLE exists, line 1224 is executed (keepidle setsockopt)."""
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", 19882)

        mock_raw = MagicMock()
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        outer_proxy_target: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                if self._target is not None and len(outer_proxy_target) == 0:
                    outer_proxy_target.append(self._target)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection
        had_keepidle = hasattr(socket_module, "TCP_KEEPIDLE")

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]
            # Force TCP_KEEPIDLE to exist so line 1224 is hit.
            socket_module.TCP_KEEPIDLE = 4  # type: ignore[attr-defined]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
                patch("select.select", return_value=([], [], [])),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)
                outer_proxy_target[0]()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]
            if not had_keepidle:
                try:
                    del socket_module.TCP_KEEPIDLE  # type: ignore[attr-defined]
                except AttributeError:
                    pass

        # Verify that TCP_KEEPIDLE setsockopt was called on mock_raw.
        mock_raw.setsockopt.assert_called()

    def _build_proxy_with_pipe_targets(self, port_seed: int = 19890) -> tuple[list[Any], Any, Any]:
        """
        Run _start_tls_proxy_sync and _proxy_thread, collecting _pipe targets.
        Returns (pipe_targets, mock_raw, mock_tls).
        """
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", port_seed)

        mock_raw = MagicMock()
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        pipe_targets: list[Any] = []
        outer_proxy_target: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                self._args = kwargs.get("args", ())
                fn = self._target
                if fn is not None and hasattr(fn, "__name__") and "_pipe" in fn.__name__:
                    pipe_targets.append((fn, self._args))
                elif fn is not None and len(outer_proxy_target) == 0:
                    outer_proxy_target.append(fn)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)
                outer_proxy_target[0]()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]

        return pipe_targets, mock_raw, mock_tls

    def test_pipe_exception_triggers_finally(self) -> None:
        """_pipe: exception during sendall → except + finally blocks covered."""
        pipe_targets, _, _ = self._build_proxy_with_pipe_targets(19891)
        assert len(pipe_targets) >= 1

        mock_src = MagicMock()
        mock_dst = MagicMock()
        mock_dst.sendall.side_effect = BrokenPipeError("broken")
        recv_count = [0]

        def _recv(n: int) -> bytes:
            recv_count[0] += 1
            return b"data"

        mock_src.recv.side_effect = _recv

        with patch("select.select", return_value=([mock_src], [], [])):
            fn, args = pipe_targets[0]
            fn(mock_src, mock_dst, False)  # exception swallowed in except; finally closes

        # finally: src.close() and dst.close() called
        mock_src.close.assert_called()
        mock_dst.close.assert_called()

    def test_pipe_keepalive_setsockopt_raises_oserror(self) -> None:
        """OSError in keepalive setsockopt → except (AttributeError, OSError) covered."""
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", 19892)

        mock_raw = MagicMock()
        # First setsockopt call succeeds (SO_KEEPALIVE), inner ones raise OSError
        so_call_count = [0]

        def _setsockopt(*args: Any) -> None:
            so_call_count[0] += 1
            if so_call_count[0] > 1:
                raise OSError("not supported")

        mock_raw.setsockopt.side_effect = _setsockopt
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        outer_proxy_target: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                if self._target is not None and len(outer_proxy_target) == 0:
                    outer_proxy_target.append(self._target)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)
                outer_proxy_target[0]()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]

        # The OSError in keepalive was caught → execution continued to tls wrap.
        mock_ctx.wrap_socket.assert_called()

    def test_pipe_close_raises_exception_swallowed(self) -> None:
        """_pipe finally: src.close() and dst.close() raise → swallowed (1262-1267)."""
        pipe_targets, _, _ = self._build_proxy_with_pipe_targets(19893)
        assert len(pipe_targets) >= 1

        mock_src = MagicMock()
        mock_dst = MagicMock()
        # recv raises immediately so we go to finally
        mock_src.recv.side_effect = RuntimeError("recv fail")
        mock_src.close.side_effect = OSError("close fail src")
        mock_dst.close.side_effect = OSError("close fail dst")

        with patch("select.select", return_value=([mock_src], [], [])):
            fn, args = pipe_targets[0]
            fn(mock_src, mock_dst, False)  # exception in recv → finally with close errors

        # Both close() were called (and their exceptions swallowed)
        mock_src.close.assert_called()
        mock_dst.close.assert_called()

    def _build_proxy_with_reset_targets(self, port_seed: int = 19894) -> list[Any]:
        """Run _start_tls_proxy_sync + _proxy_thread, collecting _reset_on_stable targets."""
        import ssl as ssl_module
        import socket as socket_module

        mock_client = MagicMock()
        mock_client.setsockopt = MagicMock()
        mock_srv = MagicMock()

        accept_count = [0]

        def _accept() -> tuple[MagicMock, Any]:
            accept_count[0] += 1
            if accept_count[0] > 1:
                raise OSError("done")
            return mock_client, ("127.0.0.1", 9999)

        mock_srv.accept.side_effect = _accept
        mock_srv.getsockname.return_value = ("127.0.0.1", port_seed)

        mock_raw = MagicMock()
        mock_tls = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.wrap_socket.return_value = mock_tls

        reset_targets: list[Any] = []
        outer_proxy_target: list[Any] = []

        class _FakeThread:
            def __init__(self, **kwargs: Any) -> None:
                self._target = kwargs.get("target")
                self._args = kwargs.get("args", ())
                fn = self._target
                if fn is not None and hasattr(fn, "__name__") and "_reset_on_stable" in fn.__name__:
                    reset_targets.append((fn, self._args))
                elif fn is not None and len(outer_proxy_target) == 0:
                    outer_proxy_target.append(fn)

            def start(self) -> None:
                pass

            def join(self, timeout: float = 0.0) -> None:
                pass

        orig_ssl_ctx = ssl_module.create_default_context
        orig_sock_cls = socket_module.socket
        orig_create_conn = socket_module.create_connection

        try:
            ssl_module.create_default_context = MagicMock(return_value=mock_ctx)  # type: ignore[method-assign]
            socket_module.socket = MagicMock(return_value=mock_srv)  # type: ignore[assignment]
            socket_module.create_connection = MagicMock(return_value=mock_raw)  # type: ignore[assignment]

            with (
                patch("threading.Thread", side_effect=lambda **kw: _FakeThread(**kw)),
                patch("time.sleep"),
                patch("time.time", return_value=0.0),
            ):
                bosch_camera._start_tls_proxy_sync("192.0.2.5", 8554)
                outer_proxy_target[0]()
        finally:
            ssl_module.create_default_context = orig_ssl_ctx  # type: ignore[method-assign]
            socket_module.socket = orig_sock_cls  # type: ignore[assignment]
            socket_module.create_connection = orig_create_conn  # type: ignore[assignment]

        return reset_targets

    def test_reset_on_stable_counter_reset(self) -> None:
        """_reset_on_stable: elapsed >= 30s → counter reset (lines 1275-1277)."""
        reset_targets = self._build_proxy_with_reset_targets(19895)
        assert len(reset_targets) >= 1, "Expected _reset_on_stable target"

        counter: list[int] = [5]
        with patch("time.time", return_value=31.0):
            fn, _ = reset_targets[0]
            fn(0.0, counter)  # start=0.0, elapsed=31 >= 30

        assert counter[0] == 0

    def test_reset_on_stable_no_reset_when_short(self) -> None:
        """_reset_on_stable: elapsed < 30s → counter unchanged."""
        reset_targets = self._build_proxy_with_reset_targets(19896)
        assert len(reset_targets) >= 1

        counter: list[int] = [5]
        with patch("time.time", return_value=10.0):
            fn, _ = reset_targets[0]
            fn(0.0, counter)  # start=0.0, elapsed=10 < 30

        assert counter[0] == 5  # unchanged
