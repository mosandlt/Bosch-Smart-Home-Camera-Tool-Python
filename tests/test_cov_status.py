"""
Tests for cmd_status, cmd_events, cmd_snapshot, cmd_timestamp.

PIN_EVERY_MODE: one test per discrete branch (online/offline/updating,
live/event snapshot, hq flag, action=on/off/None, HTTP error codes).

Fake IDs only — NEVER real device values, IPs, tokens, or secrets.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_events,
    cmd_snapshot,
    cmd_status,
    cmd_timestamp,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants / helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_ID2 = "BBBBCCDD-1111-2222-3333-444455556666"
CAM_NAME = "Terrasse"
CAM_NAME2 = "Kamera"
FAKE_TOKEN = "tok"
FAKE_IP = "192.0.2.1"
FAKE_SNAP_BYTES = b"\xff\xd8" + b"\xab" * 300  # minimal valid JPEG header


def _jwt() -> str:
    import base64
    import json as _j
    import time

    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = (
        base64.urlsafe_b64encode(_j.dumps({"exp": int(time.time()) + 3600}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{hdr}.{pay}.sig"


def _make_cfg(*, model: str = "HOME_Eyes_Outdoor", mac: str = "aa:bb:cc:dd:ee:ff") -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME: {
                "id": CAM_ID,
                "name": CAM_NAME,
                "model": model,
                "firmware": "9.40.102",
                "mac": mac,
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _make_cfg_two_cams() -> dict[str, Any]:
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME: {
                "id": CAM_ID,
                "name": CAM_NAME,
                "model": "HOME_Eyes_Outdoor",
                "firmware": "9.40.102",
                "mac": "aa:bb:cc:dd:ee:ff",
            },
            CAM_NAME2: {
                "id": CAM_ID2,
                "name": CAM_NAME2,
                "model": "HOME_Eyes_Indoor",
                "firmware": "9.40.100",
                "mac": "aa:bb:cc:dd:ee:00",
            },
        },
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "live": False,
        "hq": False,
        "quality": None,
        "output": None,
        "json": False,
        "action": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _sess_ok(
    ping_text: str = "ONLINE",
    live_inputs: list[Any] | None = None,
) -> MagicMock:
    """Return a MagicMock session whose .get() responds sensibly for cmd_status."""
    live_inputs = live_inputs if live_inputs is not None else []

    def _get_side_effect(url: str, **kwargs: Any) -> MagicMock:
        if "ping" in url:
            return MagicMock(status_code=200, text=f'"{ping_text}"')
        if "video_inputs" in url and "ping" not in url and "events" not in url:
            return MagicMock(status_code=200, json=lambda: live_inputs)
        return MagicMock(status_code=200, json=lambda: [])

    sess = MagicMock()
    sess.get.side_effect = _get_side_effect
    return sess


# ─────────────────────────────────────────────────────────────────────────────
# cmd_status
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdStatus:
    """Tests for cmd_status — ONLINE / OFFLINE / UPDATING paths."""

    def test_online_camera_prints_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ONLINE camera → output contains cam name and ONLINE."""
        cfg = _make_cfg()
        sess = _sess_ok(ping_text="ONLINE")
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())
        out = capsys.readouterr().out
        assert CAM_NAME in out
        assert "ONLINE" in out

    def test_offline_camera_prints_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """OFFLINE camera → output contains cam name and OFFLINE."""
        cfg = _make_cfg()
        sess = _sess_ok(ping_text="OFFLINE")
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())
        out = capsys.readouterr().out
        assert "OFFLINE" in out

    def test_updating_camera_shows_firmware_label(self, capsys: pytest.CaptureFixture[str]) -> None:
        """UPDATING ping response → label includes 'UPDATING (firmware)'."""
        cfg = _make_cfg()
        sess = _sess_ok(ping_text="UPDATING_1234")
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())
        out = capsys.readouterr().out
        assert "UPDATING" in out

    def test_new_cam_hint_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Live /video_inputs contains a title not in config → 'rescan' hint shown."""
        cfg = _make_cfg()
        sess = _sess_ok(ping_text="ONLINE", live_inputs=[{"title": "Eingang"}])
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())
        out = capsys.readouterr().out
        assert "rescan" in out.lower()

    def test_live_list_http_error_does_not_crash(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Non-200 from /video_inputs → no crash, status still printed."""
        cfg = _make_cfg()

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if "ping" in url:
                return MagicMock(status_code=200, text='"ONLINE"')
            # live list fails
            return MagicMock(status_code=500)

        sess = MagicMock()
        sess.get.side_effect = _get
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())  # must not raise
        out = capsys.readouterr().out
        assert CAM_NAME in out

    def test_live_list_network_exception_does_not_crash(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Network exception on live-list call → silently ignored, status shown."""
        cfg = _make_cfg()

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if "ping" in url:
                return MagicMock(status_code=200, text='"ONLINE"')
            raise ConnectionError("network error")

        sess = MagicMock()
        sess.get.side_effect = _get
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())  # must not raise
        out = capsys.readouterr().out
        assert CAM_NAME in out

    def test_cam_id_and_model_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam_id and model/fw line are present in output."""
        cfg = _make_cfg()
        sess = _sess_ok()
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())
        out = capsys.readouterr().out
        assert CAM_ID in out
        assert "HOME_Eyes_Outdoor" in out

    def test_multiple_cameras_all_shown(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Two cameras → both names appear in output."""
        cfg = _make_cfg_two_cams()

        def _get(url: str, **kwargs: Any) -> MagicMock:
            if "video_inputs" in url and "ping" not in url:
                return MagicMock(status_code=200, json=lambda: [])
            return MagicMock(status_code=200, text='"ONLINE"')

        sess = MagicMock()
        sess.get.side_effect = _get
        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_status(cfg, _args())
        out = capsys.readouterr().out
        assert CAM_NAME in out
        assert CAM_NAME2 in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_events
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdEvents:
    """Tests for cmd_events — which is a no-op stub (cloud event listing removed)."""

    def test_prints_message_and_returns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_events must not crash and must print something."""
        cfg = _make_cfg()
        cmd_events(cfg, _args())
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_no_network_calls(self) -> None:
        """cmd_events must not call get_token or make_session (stub handler)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token") as mock_token,
            patch.object(bosch_camera, "make_session") as mock_sess,
        ):
            cmd_events(cfg, _args())
        assert not mock_token.called
        assert not mock_sess.called


# ─────────────────────────────────────────────────────────────────────────────
# cmd_snapshot
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdSnapshot:
    """Tests for cmd_snapshot — event, live-proxy, live-local, fallback paths."""

    def test_event_snapshot_default(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Any
    ) -> None:
        """Default (no --live) → snap_from_events called; file saved + opened."""
        cfg = _make_cfg()
        sess = MagicMock()
        ts = "2024-06-01T12:00:00.000Z"

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_events", return_value=(FAKE_SNAP_BYTES, ts)),
            patch.object(bosch_camera, "_save_and_open") as mock_save,
        ):
            cmd_snapshot(cfg, _args(live=False))
        mock_save.assert_called_once()
        _, _, saved_ts, method = mock_save.call_args[0]
        assert method == "event"
        assert saved_ts == ts

    def test_event_snapshot_no_data_prints_unavailable(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Default, no event data → prints 'unavailable' message."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_events", return_value=(None, "")),
            patch.object(bosch_camera, "_save_and_open") as mock_save,
        ):
            cmd_snapshot(cfg, _args(live=False))
        assert not mock_save.called

    def test_live_proxy_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--live, proxy succeeds → _save_and_open called with method='proxy_live'."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=FAKE_SNAP_BYTES),
            patch.object(bosch_camera, "_save_and_open") as mock_save,
        ):
            cmd_snapshot(cfg, _args(live=True))
        mock_save.assert_called_once()
        _, _, _, method = mock_save.call_args[0]
        assert method == "proxy_live"

    def test_live_local_fallback(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--live, proxy fails → tries snap_from_local → success saves as 'local_live'."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=None),
            patch.object(bosch_camera, "snap_from_local", return_value=FAKE_SNAP_BYTES),
            patch.object(bosch_camera, "_save_and_open") as mock_save,
        ):
            cmd_snapshot(cfg, _args(live=True))
        mock_save.assert_called_once()
        _, _, _, method = mock_save.call_args[0]
        assert method == "local_live"

    def test_live_failure_does_not_save_stale_event(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Regression: --live with proxy + local both failing must NOT fall back
        to a (possibly days-old) event snapshot — neither fetched nor saved
        (mirrors the HA stale-event fix; the user asked for live, not an event)."""
        cfg = _make_cfg()
        sess = MagicMock()
        ts = "2024-06-01T09:00:00.000Z"

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=None),
            patch.object(bosch_camera, "snap_from_local", return_value=None),
            patch.object(
                bosch_camera, "snap_from_events", return_value=(FAKE_SNAP_BYTES, ts)
            ) as mock_events,
            patch.object(bosch_camera, "_save_and_open") as mock_save,
        ):
            cmd_snapshot(cfg, _args(live=True))
        mock_events.assert_not_called()
        mock_save.assert_not_called()

    def test_live_all_methods_fail_no_save(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--live, all methods fail including event → _save_and_open never called."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=None),
            patch.object(bosch_camera, "snap_from_local", return_value=None),
            patch.object(bosch_camera, "snap_from_events", return_value=(None, "")),
            patch.object(bosch_camera, "_save_and_open") as mock_save,
        ):
            cmd_snapshot(cfg, _args(live=True))
        assert not mock_save.called

    def test_hq_flag_passed_to_proxy(self) -> None:
        """--hq → snap_from_proxy called with hq=True."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=FAKE_SNAP_BYTES) as mock_proxy,
            patch.object(bosch_camera, "_save_and_open"),
        ):
            cmd_snapshot(cfg, _args(live=True, hq=True))
        _, kwargs = mock_proxy.call_args
        assert kwargs.get("hq") is True

    def test_quality_high_sets_hq_true(self) -> None:
        """--quality=high → snap_from_proxy called with hq=True."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=FAKE_SNAP_BYTES) as mock_proxy,
            patch.object(bosch_camera, "_save_and_open"),
        ):
            cmd_snapshot(cfg, _args(live=True, quality="high"))
        _, kwargs = mock_proxy.call_args
        assert kwargs.get("hq") is True

    def test_quality_non_high_sets_hq_false(self) -> None:
        """--quality=low → snap_from_proxy called with hq=False."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "snap_from_proxy", return_value=FAKE_SNAP_BYTES) as mock_proxy,
            patch.object(bosch_camera, "_save_and_open"),
        ):
            cmd_snapshot(cfg, _args(live=True, quality="low"))
        _, kwargs = mock_proxy.call_args
        assert kwargs.get("hq") is False

    def test_specific_cam_selection(self) -> None:
        """--cam Terrasse → only that camera snapshot requested."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(
                bosch_camera, "snap_from_events", return_value=(FAKE_SNAP_BYTES, "2024-01-01T00:00:00Z")
            ) as mock_events,
            patch.object(bosch_camera, "_save_and_open"),
        ):
            cmd_snapshot(cfg, _args(cam=CAM_NAME))
        # should be called exactly once (one camera)
        assert mock_events.call_count == 1

    def test_missing_cam_name_exits(self) -> None:
        """--cam unknown → SystemExit (resolve_cam exits)."""
        cfg = _make_cfg()
        sess = MagicMock()

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            with pytest.raises(SystemExit):
                cmd_snapshot(cfg, _args(cam="NoSuchCamera"))


# ─────────────────────────────────────────────────────────────────────────────
# cmd_timestamp
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdTimestamp:
    """Tests for cmd_timestamp — GET, set-on, set-off, already-set, error paths."""

    def _make_get_resp(self, enabled: bool, status: int = 200) -> MagicMock:
        return MagicMock(status_code=status, json=lambda: {"result": enabled})

    def _make_put_resp(self, status: int = 200) -> MagicMock:
        return MagicMock(status_code=status, text="")

    def test_show_enabled_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No action → shows current ENABLED state, no PUT called."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=True)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action=None))
        out = capsys.readouterr().out
        assert "ENABLED" in out
        sess.put.assert_not_called()

    def test_show_disabled_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No action → shows current DISABLED state."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action=None))
        out = capsys.readouterr().out
        assert "DISABLED" in out
        sess.put.assert_not_called()

    def test_set_on_sends_put_true(self) -> None:
        """action='on' → PUT with {result: true} sent to correct endpoint."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)  # currently OFF
        sess.put.return_value = self._make_put_resp(200)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs.get("json") == {"result": True}
        url = sess.put.call_args[0][0]
        assert CAM_ID in url
        assert "timestamp" in url

    def test_set_off_sends_put_false(self) -> None:
        """action='off' → PUT with {result: false} sent."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=True)  # currently ON
        sess.put.return_value = self._make_put_resp(200)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="off"))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs.get("json") == {"result": False}

    def test_already_enabled_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='on' but already ENABLED → no PUT, prints 'Already ENABLED'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=True)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))
        out = capsys.readouterr().out
        sess.put.assert_not_called()
        assert "Already" in out or "already" in out

    def test_already_disabled_no_put(self, capsys: pytest.CaptureFixture[str]) -> None:
        """action='off' but already DISABLED → no PUT."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="off"))
        sess.put.assert_not_called()

    def test_get_returns_401_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 401 → prints token expired message and returns (no PUT)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=401)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))  # must not raise
        out = capsys.readouterr().out
        assert "401" in out or "expired" in out.lower() or "Token" in out
        sess.put.assert_not_called()

    def test_get_returns_444_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 444 (camera offline) → no PUT, prints offline message."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=444)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))  # must not raise
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "unavailable" in out.lower()
        sess.put.assert_not_called()

    def test_get_returns_other_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GET returns 500 → no PUT, error message shown."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=500)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))  # must not raise
        out = capsys.readouterr().out
        assert "500" in out
        sess.put.assert_not_called()

    def test_put_returns_204_accepted(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT returns 204 → also treated as success."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)
        sess.put.return_value = self._make_put_resp(204)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))
        out = capsys.readouterr().out
        assert "ENABLED" in out

    def test_put_returns_444_camera_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT returns 444 → offline message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)
        sess.put.return_value = self._make_put_resp(444)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "unavailable" in out.lower()

    def test_put_fails_http_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT returns 500 → failure message printed."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)
        sess.put.return_value = MagicMock(status_code=500, text="Server Error")

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(action="on"))
        out = capsys.readouterr().out
        assert "500" in out or "Failed" in out or "failed" in out

    def test_cam_arg_as_on_action(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cam='on' + action=None → interpreted as action='on', cam=None (all cameras)."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)
        sess.put.return_value = self._make_put_resp(200)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(cam="on", action=None))
        # PUT must have been called (action was interpreted as 'on')
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs.get("json") == {"result": True}

    def test_cam_arg_as_off_action(self) -> None:
        """cam='off' + action=None → interpreted as action='off'."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=True)
        sess.put.return_value = self._make_put_resp(200)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(cam="off", action=None))
        sess.put.assert_called_once()
        _, kwargs = sess.put.call_args
        assert kwargs.get("json") == {"result": False}

    def test_specific_cam_selection(self) -> None:
        """--cam Terrasse → PUT only for that camera's ID in URL."""
        cfg = _make_cfg_two_cams()
        sess = MagicMock()
        sess.get.return_value = self._make_get_resp(enabled=False)
        sess.put.return_value = self._make_put_resp(200)

        with (
            patch.object(bosch_camera, "get_token", return_value=FAKE_TOKEN),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            cmd_timestamp(cfg, _args(cam=CAM_NAME, action="on"))
        assert sess.put.call_count == 1
        url = sess.put.call_args[0][0]
        assert CAM_ID in url
        assert CAM_ID2 not in url
