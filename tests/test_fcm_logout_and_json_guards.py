"""Regression tests: FCM old-token logout + re-register gating, non-JSON 200
responses (info/discover/events/privacy) and light-override read-failure abort.

Fake IDs/tokens only.
"""

from __future__ import annotations

import argparse
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    FCM_REGISTERED_AT_KEY,
    FCM_REGISTERED_KEY,
    _fcm_sync_registration,
)

OLD = "fake-old-fcm-token"
NEW = "fake-new-fcm-token"


def _resp(status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    return r


def _cfg(prev: Any = None, age: float = 0.0) -> dict[str, Any]:
    s: dict[str, Any] = {}
    if prev:
        s[FCM_REGISTERED_KEY] = prev
        s[FCM_REGISTERED_AT_KEY] = time.time() - age
    return {"settings": s}


class TestFcmSync:
    def test_token_change_logs_out_old_after_confirmed_post(self) -> None:
        cfg = _cfg(OLD)
        with (
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=_resp(201)),
            patch.object(bosch_camera, "requests_put_bosch_cloud", return_value=_resp(204)) as put,
            patch.object(bosch_camera, "save_config") as save,
        ):
            assert _fcm_sync_registration(cfg, "bearer", NEW) is True
        assert put.call_args.args[0].endswith("/v11/registration/logout")
        assert put.call_args.kwargs["params"] == {"deviceToken": OLD}
        assert put.call_args.kwargs["headers"]["Authorization"] == "Bearer bearer"
        assert cfg["settings"][FCM_REGISTERED_KEY] == NEW
        save.assert_called()

    def test_no_logout_when_registration_unconfirmed(self) -> None:
        cfg = _cfg(OLD)
        with (
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=_resp(500)),
            patch.object(bosch_camera, "requests_put_bosch_cloud") as put,
            patch.object(bosch_camera, "save_config") as save,
        ):
            assert _fcm_sync_registration(cfg, "bearer", NEW) is False
        put.assert_not_called()
        save.assert_not_called()
        assert cfg["settings"][FCM_REGISTERED_KEY] == OLD

    def test_logout_failure_never_raises_and_no_retry(self) -> None:
        cfg = _cfg(OLD)
        with (
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=_resp(200)),
            patch.object(
                bosch_camera, "requests_put_bosch_cloud", side_effect=OSError(f"x {OLD}")
            ) as put,
            patch.object(bosch_camera, "save_config"),
        ):
            assert _fcm_sync_registration(cfg, "bearer", NEW) is True
        assert put.call_count == 1

    def test_unchanged_fresh_token_skips_post(self) -> None:
        cfg = _cfg(NEW, age=60)
        with (
            patch.object(bosch_camera, "requests_post_bosch_cloud") as post,
            patch.object(bosch_camera, "requests_put_bosch_cloud") as put,
        ):
            assert _fcm_sync_registration(cfg, "bearer", NEW) is None
        post.assert_not_called()
        put.assert_not_called()

    def test_stale_same_token_reposts_without_logout(self) -> None:
        cfg = _cfg(NEW, age=8 * 24 * 3600)
        with (
            patch.object(
                bosch_camera, "requests_post_bosch_cloud", return_value=_resp(200)
            ) as post,
            patch.object(bosch_camera, "requests_put_bosch_cloud") as put,
            patch.object(bosch_camera, "save_config"),
        ):
            assert _fcm_sync_registration(cfg, "bearer", NEW) is True
        post.assert_called_once()
        put.assert_not_called()

    def test_first_registration_no_logout_and_token_not_logged(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = _cfg()
        with (
            patch.object(bosch_camera, "requests_post_bosch_cloud", return_value=_resp(200)),
            patch.object(bosch_camera, "requests_put_bosch_cloud") as put,
            patch.object(bosch_camera, "save_config"),
        ):
            _fcm_sync_registration(cfg, "bearer", NEW)
        put.assert_not_called()
        assert NEW not in capsys.readouterr().out


class TestNonJson200:
    def _bad(self) -> MagicMock:
        r = _resp(200)
        r.json.side_effect = ValueError("bad json")
        return r

    def test_cmd_info_exits_cleanly(self, capsys: pytest.CaptureFixture[str]) -> None:
        sess = MagicMock()
        sess.request.return_value = self._bad()
        with (
            patch.object(bosch_camera, "get_token", return_value="t"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            pytest.raises(SystemExit) as ei,
        ):
            bosch_camera.cmd_info({}, argparse.Namespace(full=False))
        assert ei.value.code == 1
        assert "non-JSON" in capsys.readouterr().out
        sess.request.assert_called_once()

    def test_discover_cameras_exits_cleanly(self) -> None:
        sess = MagicMock()
        sess.get.return_value = self._bad()
        with pytest.raises(SystemExit):
            bosch_camera.discover_cameras({"cameras": {}}, sess)

    def test_api_get_events_exits_cleanly(self) -> None:
        sess = MagicMock()
        sess.request.return_value = self._bad()
        with pytest.raises(SystemExit):
            bosch_camera.api_get_events(sess, "AABBCCDD-0000-1111-2222-333344445555")


CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"


class TestCmdLightOverrideReadFailure:
    def _run(self, get_override: Any) -> MagicMock:
        sess = MagicMock()
        feat = MagicMock(status_code=200)
        feat.json.return_value = [
            {"id": CAM_ID, "featureSupport": {"light": True}, "featureStatus": {}}
        ]

        def _get(url: str, **kw: Any) -> Any:
            if url.endswith("lighting_override"):
                if isinstance(get_override, Exception):
                    raise get_override
                return get_override
            return feat

        sess.get.side_effect = _get
        cfg = {
            "account": {"bearer_token": "t"},
            "cameras": {"Cam": {"id": CAM_ID, "name": "Cam", "model": "HOME_Eyes_Outdoor"}},
            "settings": {},
            "lan_ips": {},
        }
        args = argparse.Namespace(cam="Cam", action="wall", extra_args=["on"], local=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="t"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
        ):
            bosch_camera.cmd_light(cfg, args)
        return sess

    def test_non_200_aborts_without_put(self) -> None:
        sess = self._run(MagicMock(status_code=503))
        sess.put.assert_not_called()

    def test_exception_aborts_without_put(self) -> None:
        sess = self._run(OSError("boom"))
        sess.put.assert_not_called()
