"""
Tests for 6 social/account CLI handlers:
  cmd_friends, cmd_accept_invite, cmd_shared_with_friends,
  cmd_profile, cmd_account, cmd_rename

PIN_EVERY_MODE: one test per discrete subcommand/action + default + error/garbage path.
FAKE IDs only — no real devices, tokens, IPs, or names.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import (
    cmd_accept_invite,
    cmd_account,
    cmd_friends,
    cmd_profile,
    cmd_rename,
    cmd_shared_with_friends,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants / fixtures
# ─────────────────────────────────────────────────────────────────────────────

CLOUD_API = "https://residential.cbs.boschsecurity.com"

CAM_ID_GEN2 = "AABBCCDD-0000-1111-2222-333344445555"
CAM_ID_GEN1 = "FFFF0000-CAFE-BABE-DEAD-BEEFDEADBEEF"
CAM_NAME_GEN2 = "Terrasse"
CAM_NAME_GEN1 = "Kamera"
FRIEND_ID = "BBBBCCCC-1111-2222-3333-444455556666"
FRIEND_EMAIL = "friend@example.com"
INVITE_TOKEN = "invite-token-fake-12345"


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "sub": None,
        "sub_arg": None,
        "share_cam": None,
        "days": None,
        "token": None,
        "new_name": None,
        "display_name": None,
        "marketing": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_cfg(gen2: bool = True) -> dict[str, Any]:
    if gen2:
        return {
            "account": {"bearer_token": "tok", "refresh_token": "", "username": ""},
            "cameras": {
                CAM_NAME_GEN2: {
                    "id": CAM_ID_GEN2,
                    "name": CAM_NAME_GEN2,
                    "model": "HOME_Eyes_Outdoor",
                    "firmware": "9.40.102",
                }
            },
            "settings": {},
            "lan_ips": {},
        }
    return {
        "account": {"bearer_token": "tok", "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME_GEN1: {
                "id": CAM_ID_GEN1,
                "name": CAM_NAME_GEN1,
                "model": "CAMERA_360",
                "firmware": "7.91.56",
            }
        },
        "settings": {},
        "lan_ips": {},
    }


def _ok(data: Any = None, status: int = 200) -> MagicMock:
    return MagicMock(status_code=status, json=lambda: data or {}, text="")


def _err(status: int, text: str = "error") -> MagicMock:
    return MagicMock(status_code=status, json=lambda: {}, text=text)


def _sess(**overrides: Any) -> MagicMock:
    sess = MagicMock()
    for attr, val in overrides.items():
        setattr(sess, attr, val)
    return sess


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — list (default)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriendsList:
    """Default (no sub) → GET /v11/friends."""

    def test_list_returns_friends(self, capsys: pytest.CaptureFixture[str]) -> None:
        """List with one friend → ID and email shown."""
        friends_data = [
            {
                "id": FRIEND_ID,
                "email": FRIEND_EMAIL,
                "nickName": "Alice",
                "status": "ACCEPTED",
                "sharedVideoInputs": [{"videoInputId": CAM_ID_GEN2}],
            }
        ]
        sess = MagicMock()
        sess.get.return_value = _ok(friends_data)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args())
        sess.get.assert_called_once_with(f"{CLOUD_API}/v11/friends", timeout=10)
        out = capsys.readouterr().out
        assert FRIEND_ID in out
        assert FRIEND_EMAIL in out

    def test_list_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty list → no-friends hint shown."""
        sess = MagicMock()
        sess.get.return_value = _ok([])
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "no friends" in out.lower() or "invite" in out.lower()

    def test_list_401_prints_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """401 → token-expired message printed, no crash."""
        sess = MagicMock()
        sess.get.return_value = _err(401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "Token expired" in out or "expired" in out.lower()

    def test_list_444_prints_offline_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 → camera-offline warning printed."""
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=444, json=lambda: {}, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_list_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-200/401/444 → error code printed."""
        sess = MagicMock()
        sess.get.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "500" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — invite
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriendsInvite:
    """sub=invite → POST /v11/friends."""

    def test_invite_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """201 response → friend ID shown from response."""
        resp_data = {"id": FRIEND_ID, "email": FRIEND_EMAIL}
        sess = MagicMock()
        sess.post.return_value = _ok(resp_data, status=201)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="invite", sub_arg=FRIEND_EMAIL))
        sess.post.assert_called_once()
        call_kwargs = sess.post.call_args
        assert "/v11/friends" in call_kwargs[0][0]
        body = call_kwargs[1]["json"]
        assert body["invitationEmail"] == FRIEND_EMAIL
        out = capsys.readouterr().out
        assert FRIEND_ID in out

    def test_invite_missing_email_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No email → error message, no POST."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="invite", sub_arg=None))
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "Email is required" in out or "required" in out.lower()

    def test_invite_444_offline_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 → offline warning printed."""
        sess = MagicMock()
        sess.post.return_value = MagicMock(status_code=444, json=lambda: {}, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="invite", sub_arg=FRIEND_EMAIL))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_invite_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 → HTTP status printed."""
        sess = MagicMock()
        sess.post.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="invite", sub_arg=FRIEND_EMAIL))
        out = capsys.readouterr().out
        assert "500" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — share
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriendsShare:
    """sub=share → PUT /v11/friends/{id}/share."""

    def test_share_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """204 → success; PUT body contains videoInputId."""
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam=CAM_NAME_GEN2),
            )
        sess.put.assert_called_once()
        (url,) = (sess.put.call_args[0][0],)
        assert f"/v11/friends/{FRIEND_ID}/share" in url
        body = sess.put.call_args[1]["json"]
        assert isinstance(body, list)
        assert body[0]["videoInputId"] == CAM_ID_GEN2

    def test_share_with_days_adds_share_time(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--days N → shareTime start/end added to body."""
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam=CAM_NAME_GEN2, days=7),
            )
        body = sess.put.call_args[1]["json"]
        assert "shareTime" in body[0]
        assert "start" in body[0]["shareTime"]
        assert "end" in body[0]["shareTime"]

    def test_share_missing_args_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No friend_id/cam_name → error, no PUT."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="share", sub_arg=None, share_cam=None))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Usage" in out or "usage" in out.lower()

    def test_share_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 → HTTP status printed."""
        sess = MagicMock()
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(
                _make_cfg(),
                _args(sub="share", sub_arg=FRIEND_ID, share_cam=CAM_NAME_GEN2),
            )
        out = capsys.readouterr().out
        assert "500" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — unshare
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriendsUnshare:
    """sub=unshare → PUT /v11/friends/{id}/share with empty list."""

    def test_unshare_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """204 → success; PUT body is empty list."""
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="unshare", sub_arg=FRIEND_ID))
        (url,) = (sess.put.call_args[0][0],)
        assert f"/v11/friends/{FRIEND_ID}/share" in url
        body = sess.put.call_args[1]["json"]
        assert body == []

    def test_unshare_missing_id_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No friend_id → error, no PUT."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="unshare", sub_arg=None))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Usage" in out or "unshare" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — resend
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriendsResend:
    """sub=resend → PUT /v11/friends/{id}/resend_invite."""

    def test_resend_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """204 → success; URL includes resend_invite."""
        sess = MagicMock()
        sess.put.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="resend", sub_arg=FRIEND_ID))
        (url,) = (sess.put.call_args[0][0],)
        assert f"/v11/friends/{FRIEND_ID}/resend_invite" in url

    def test_resend_missing_id_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No friend_id → error, no PUT."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="resend", sub_arg=None))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Usage" in out or "resend" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_friends — remove
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdFriendsRemove:
    """sub=remove → DELETE /v11/friends/{id}."""

    def test_remove_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """204 → success; DELETE URL contains friend ID."""
        sess = MagicMock()
        sess.delete.return_value = _ok(status=204)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="remove", sub_arg=FRIEND_ID))
        sess.delete.assert_called_once()
        (url,) = (sess.delete.call_args[0][0],)
        assert f"/v11/friends/{FRIEND_ID}" in url

    def test_remove_missing_id_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No friend_id → error, no DELETE."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="remove", sub_arg=None))
        sess.delete.assert_not_called()
        out = capsys.readouterr().out
        assert "Usage" in out or "remove" in out.lower()

    def test_remove_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 → HTTP status printed."""
        sess = MagicMock()
        sess.delete.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_friends(_make_cfg(), _args(sub="remove", sub_arg=FRIEND_ID))
        out = capsys.readouterr().out
        assert "500" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_accept_invite
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAcceptInvite:
    """POST /v11/friends/accept with invite token."""

    def test_accept_success_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """200 → success; POST body contains invite token."""
        resp_data = {"friendId": FRIEND_ID, "status": "ACCEPTED"}
        sess = MagicMock()
        sess.post.return_value = _ok(resp_data, status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_accept_invite(_make_cfg(), _args(token=INVITE_TOKEN))
        sess.post.assert_called_once()
        (url,) = (sess.post.call_args[0][0],)
        assert "/v11/friends/accept" in url
        body = sess.post.call_args[1]["json"]
        assert body["token"] == INVITE_TOKEN
        out = capsys.readouterr().out
        assert "accepted" in out.lower() or FRIEND_ID in out

    def test_accept_success_204(self, capsys: pytest.CaptureFixture[str]) -> None:
        """204 (no body) → accepted message printed without crash."""
        sess = MagicMock()
        # 204 responses have no JSON body — simulate json() raising an exception
        mock_resp = MagicMock(status_code=204, text="")
        mock_resp.json.side_effect = ValueError("No JSON")
        sess.post.return_value = mock_resp
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_accept_invite(_make_cfg(), _args(token=INVITE_TOKEN))
        out = capsys.readouterr().out
        assert "accepted" in out.lower()

    def test_accept_missing_token_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No token → error, no POST."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_accept_invite(_make_cfg(), _args(token=None))
        sess.post.assert_not_called()
        out = capsys.readouterr().out
        assert "required" in out.lower() or "token" in out.lower()

    def test_accept_444_offline_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 → offline warning."""
        sess = MagicMock()
        sess.post.return_value = MagicMock(status_code=444, json=lambda: {}, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_accept_invite(_make_cfg(), _args(token=INVITE_TOKEN))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_accept_400_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """400 → HTTP status printed."""
        sess = MagicMock()
        sess.post.return_value = _err(400, "Bad Request")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_accept_invite(_make_cfg(), _args(token=INVITE_TOKEN))
        out = capsys.readouterr().out
        assert "400" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_shared_with_friends
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdSharedWithFriends:
    """GET /v11/video_inputs/{id}/shared_with_friends (Gen2 only)."""

    def test_shared_gen2_with_friends(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen2 camera, 200 response with friends → friend listed."""
        friends_data = [
            {
                "id": FRIEND_ID,
                "email": FRIEND_EMAIL,
                "nickName": "Bob",
                "status": "ACTIVE",
                "shareTime": {},
            }
        ]
        sess = MagicMock()
        sess.get.return_value = _ok(friends_data)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_shared_with_friends(_make_cfg(gen2=True), _args(cam=CAM_NAME_GEN2))
        sess.get.assert_called_once()
        (url,) = (sess.get.call_args[0][0],)
        assert f"/v11/video_inputs/{CAM_ID_GEN2}/shared_with_friends" in url
        out = capsys.readouterr().out
        assert FRIEND_ID in out

    def test_shared_gen2_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen2 camera, no shares → 'not shared' message."""
        sess = MagicMock()
        sess.get.return_value = _ok([])
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_shared_with_friends(_make_cfg(gen2=True), _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "not shared" in out.lower() or "shared" in out.lower()

    def test_shared_gen1_skips_request(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen1 camera → API call skipped, warning printed."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_shared_with_friends(_make_cfg(gen2=False), _args(cam=CAM_NAME_GEN1))
        sess.get.assert_not_called()
        out = capsys.readouterr().out
        assert "Gen1" in out or "not supported" in out.lower() or "CAMERA_360" in out

    def test_shared_gen2_444_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 → offline warning."""
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=444, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_shared_with_friends(_make_cfg(gen2=True), _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_shared_gen2_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 → HTTP status printed."""
        sess = MagicMock()
        sess.get.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_shared_with_friends(_make_cfg(gen2=True), _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "500" in out

    def test_shared_gen2_with_share_time(self, capsys: pytest.CaptureFixture[str]) -> None:
        """shareTime present → start/end printed."""
        friends_data = [
            {
                "id": FRIEND_ID,
                "email": FRIEND_EMAIL,
                "nickName": "Carol",
                "status": "ACCEPTED",
                "shareTime": {
                    "start": "2026-01-01T00:00:00.000Z",
                    "end": "2026-12-31T23:59:59.000Z",
                },
            }
        ]
        sess = MagicMock()
        sess.get.return_value = _ok(friends_data)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_shared_with_friends(_make_cfg(gen2=True), _args(cam=CAM_NAME_GEN2))
        out = capsys.readouterr().out
        assert "2026-01-01" in out
        assert "2026-12-31" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rename
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRename:
    """PUT /v11/video_inputs to rename a camera."""

    def test_rename_success_updates_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        """200 → config updated, old key removed, new key added."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
            patch.object(bosch_camera, "save_config") as mock_save,
        ):
            cmd_rename(cfg, _args(cam=CAM_NAME_GEN2, new_name="Garten"))
        sess.put.assert_called_once()
        (url,) = (sess.put.call_args[0][0],)
        assert "/v11/video_inputs" in url
        body = sess.put.call_args[1]["json"]
        assert body["videoInputId"] == CAM_ID_GEN2
        assert body["title"] == "Garten"
        assert body["timeZone"] == "Europe/Berlin"
        mock_save.assert_called_once()
        assert "Garten" in cfg["cameras"]
        assert CAM_NAME_GEN2 not in cfg["cameras"]

    def test_rename_missing_args_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No cam or new_name → error, no PUT."""
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_rename(_make_cfg(), _args(cam=None, new_name=None))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "Usage" in out or "usage" in out.lower()

    def test_rename_444_offline_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 → offline warning."""
        sess = MagicMock()
        sess.put.return_value = MagicMock(status_code=444, json=lambda: {}, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_rename(_make_cfg(), _args(cam=CAM_NAME_GEN2, new_name="Eingang"))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_rename_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 → HTTP status printed."""
        sess = MagicMock()
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
        ):
            cmd_rename(_make_cfg(), _args(cam=CAM_NAME_GEN2, new_name="Eingang"))
        out = capsys.readouterr().out
        assert "500" in out

    def test_rename_same_name_no_key_delete(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Rename to same name → config entry stays, save_config still called."""
        cfg = _make_cfg()
        sess = MagicMock()
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras"),
            patch.object(bosch_camera, "save_config") as mock_save,
        ):
            cmd_rename(cfg, _args(cam=CAM_NAME_GEN2, new_name=CAM_NAME_GEN2))
        assert CAM_NAME_GEN2 in cfg["cameras"]
        mock_save.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_profile
# ─────────────────────────────────────────────────────────────────────────────

_PROFILE_DATA = {
    "userInformation": {
        "email": "user@example.com",
        "firstName": "Jane",
        "lastName": "Doe",
        "displayName": "jdoe",
        "marketingContact": False,
        "iotThingsIntegration": True,
        "language": "de_DE",
        "timeZone": "Europe/Berlin",
        "locale": "de_DE",
    },
    "lastLoginTime": "2026-01-01T10:00:00Z",
    "tokenExpirationTime": "2026-01-02T10:00:00Z",
    "loginProblems": [],
}


class TestCmdProfile:
    """GET /v11/registration/check + PUT /v11/registration."""

    def test_show_profile_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default (no sub) → profile info printed, no PUT."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args())
        sess.get.assert_called_once()
        (url,) = (sess.get.call_args[0][0],)
        assert "/v11/registration/check" in url
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "user@example.com" in out

    def test_show_profile_401_token_expired(self, capsys: pytest.CaptureFixture[str]) -> None:
        """401 → token-expired message, no further processing."""
        sess = MagicMock()
        sess.get.return_value = _err(401)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="expired"),
        ):
            cmd_profile(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "Token expired" in out or "expired" in out.lower()

    def test_show_profile_444_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 → offline warning."""
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=444, json=lambda: {}, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="?"),
        ):
            cmd_profile(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_show_profile_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 → HTTP status printed."""
        sess = MagicMock()
        sess.get.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="?"),
        ):
            cmd_profile(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "500" in out

    def test_edit_display_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=edit + display_name → PUT /v11/registration with new displayName."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args(sub="edit", display_name="newname"))
        sess.put.assert_called_once()
        (url,) = (sess.put.call_args[0][0],)
        assert "/v11/registration" in url
        body = sess.put.call_args[1]["json"]
        assert body["displayName"] == "newname"
        out = capsys.readouterr().out
        assert "updated" in out.lower() or "Profile updated" in out

    def test_edit_marketing_on(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=edit + marketing=on → PUT body has marketingContact=True."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args(sub="edit", marketing="on"))
        body = sess.put.call_args[1]["json"]
        assert body["marketingContact"] is True

    def test_edit_marketing_off(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=edit + marketing=off → PUT body has marketingContact=False."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        sess.put.return_value = _ok(status=200)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args(sub="edit", marketing="off"))
        body = sess.put.call_args[1]["json"]
        assert body["marketingContact"] is False

    def test_edit_no_changes_prints_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """sub=edit but no display_name or marketing → warning, no PUT."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args(sub="edit"))
        sess.put.assert_not_called()
        out = capsys.readouterr().out
        assert "No changes" in out or "no changes" in out.lower()

    def test_edit_put_444_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 444 → offline warning."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        sess.put.return_value = MagicMock(status_code=444, json=lambda: {}, text="offline")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args(sub="edit", display_name="x"))
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_edit_put_500_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """PUT 500 → HTTP status printed."""
        sess = MagicMock()
        sess.get.return_value = _ok(_PROFILE_DATA)
        sess.put.return_value = _err(500)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "check_token_age", return_value="OK"),
        ):
            cmd_profile(_make_cfg(), _args(sub="edit", display_name="x"))
        out = capsys.readouterr().out
        assert "500" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_account
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdAccount:
    """GET /v11/feature_flags + /v11/contracts + /v11/purchases."""

    def _make_sess_all_ok(self) -> MagicMock:
        """Session where all three GETs succeed with typical data."""
        flags_data = {"sharing": True, "recording": False}
        contracts_data = {
            "tacVersion": "v3",
            "tacURL": "https://example.com/tac",
            "dpnVersion": "v2",
            "dpnURL": "https://example.com/dpn",
        }
        purchases_data = [{"name": "Pro Plan", "status": "ACTIVE", "expiryDate": "2027-01-01"}]
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return _ok(flags_data)
            if "contracts" in url:
                return _ok(contracts_data)
            if "purchases" in url:
                return _ok(purchases_data)
            return _err(404)

        sess.get.side_effect = get_side_effect
        return sess

    def test_account_shows_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Feature flags dict → keys printed."""
        sess = self._make_sess_all_ok()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "sharing" in out
        assert "recording" in out

    def test_account_shows_contracts(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Contracts response → tac/dpn versions shown."""
        sess = self._make_sess_all_ok()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "v3" in out  # tacVersion

    def test_account_shows_purchases(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Purchases list → plan name and status shown."""
        sess = self._make_sess_all_ok()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "Pro Plan" in out
        assert "ACTIVE" in out

    def test_account_flags_as_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Feature flags as list of dicts → name and value printed."""
        flags_data = [
            {"name": "sharing", "value": True},
            {"name": "recording", "value": False},
        ]
        contracts_data: dict[str, Any] = {}
        purchases_data: list[Any] = []
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return _ok(flags_data)
            if "contracts" in url:
                return _ok(contracts_data)
            if "purchases" in url:
                return _ok(purchases_data)
            return _err(404)

        sess.get.side_effect = get_side_effect
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "sharing" in out

    def test_account_flags_as_string_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Feature flags as list of strings → each flag printed."""
        flags_data = ["sharing", "recording"]
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return _ok(flags_data)
            if "contracts" in url:
                return _ok({})
            if "purchases" in url:
                return _ok([])
            return _err(404)

        sess.get.side_effect = get_side_effect
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "sharing" in out

    def test_account_444_flags_offline(self, capsys: pytest.CaptureFixture[str]) -> None:
        """444 on feature_flags → offline warning."""
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return MagicMock(status_code=444, text="offline")
            if "contracts" in url:
                return _ok({})
            if "purchases" in url:
                return _ok([])
            return _err(404)

        sess.get.side_effect = get_side_effect
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "offline" in out.lower() or "444" in out

    def test_account_500_flags_prints_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """500 on feature_flags → warning with HTTP code."""
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return _err(500)
            if "contracts" in url:
                return _ok({})
            if "purchases" in url:
                return _ok([])
            return _err(404)

        sess.get.side_effect = get_side_effect
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "500" in out

    def test_account_empty_purchases_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty purchases list → no-purchases message."""
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return _ok({})
            if "contracts" in url:
                return _ok({})
            if "purchases" in url:
                return _ok([])
            return _err(404)

        sess.get.side_effect = get_side_effect
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "no active" in out.lower() or "purchases" in out.lower()

    def test_account_contracts_as_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Contracts as list → each entry printed as JSON."""
        contracts_data = [{"type": "TAC", "version": "v3"}]
        sess = MagicMock()

        def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "feature_flags" in url:
                return _ok({})
            if "contracts" in url:
                return _ok(contracts_data)
            if "purchases" in url:
                return _ok([])
            return _err(404)

        sess.get.side_effect = get_side_effect
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
        ):
            cmd_account(_make_cfg(), _args())
        out = capsys.readouterr().out
        assert "TAC" in out or "v3" in out
