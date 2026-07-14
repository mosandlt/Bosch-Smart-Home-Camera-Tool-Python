"""Regression: live-view hot paths must reuse a pooled HTTP session instead of
opening a fresh TCP/TLS connection on every poll tick.

Source: research found `snap_from_proxy()` (used by `cmd_snapshot --live` and
the motion-triggered auto-snapshot in `cmd_watch`) issued its cloud
PUT /connection via bare module-level `requests.put(...)` (no session) and its
REMOTE snap.jpg GET via `requests_get_bosch_cloud()`, which itself opens a
brand-new one-shot `requests.Session` (fresh TCP/TLS handshake) on EVERY call
— despite that module's own docstring already warning "for high-volume
callers, prefer make_bosch_cloud_session() and reuse it". `_live_snap_loop`'s
fetcher thread (the `cmd_live` fallback MJPEG poller, default 1s interval) had
the identical bug in its own inline snap.jpg GET loop.

Both are now fixed to reuse ONE pooled session (see `make_session()` /
`make_bosch_cloud_session()`) across every call/tick instead of opening a new
one each time. These tests pin that behavior so it cannot regress silently.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import requests
import responses as responses_lib

import bosch_camera as bc

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"

CONN_REMOTE: dict[str, Any] = {
    "urls": ["proxy-42.live.cbs.boschsecurity.com:42090/abc123hash"],
    "imageUrlScheme": "https://{url}/snap.jpg",
    "user": "",
    "password": "",
}


def _cam() -> dict[str, Any]:
    return {"id": CAM_ID, "hardwareVersion": "OUTDOOR", "mac": "aa:bb:cc:dd:ee:ff"}


def _reset_session() -> None:
    bc._HTTP_SESSION = None


# ─────────────────────────────────────────────────────────────────────────────
# snap_from_proxy — PUT /connection + REMOTE snap.jpg GET
# ─────────────────────────────────────────────────────────────────────────────


class TestSnapFromProxySessionReuse:
    def setup_method(self) -> None:
        _reset_session()

    def teardown_method(self) -> None:
        _reset_session()

    @responses_lib.activate
    def test_reuses_cached_session_across_two_calls(self) -> None:
        """Two consecutive snap_from_proxy() calls (no explicit session=) must
        both use the SAME cached module-level session object — not open a
        fresh one per call. Pins the fix; previously every PUT /connection and
        REMOTE snap.jpg GET used one-shot helpers with no session identity to
        even check.
        """
        conn_url = f"{bc.CLOUD_API}/v11/video_inputs/{CAM_ID}/connection"
        snap_url = "https://proxy-42.live.cbs.boschsecurity.com:42090/abc123hash/snap.jpg"

        responses_lib.add(responses_lib.PUT, conn_url, json=CONN_REMOTE, status=200)
        responses_lib.add(
            responses_lib.GET,
            snap_url,
            body=b"\xff\xd8frame1",
            status=200,
            content_type="image/jpeg",
        )
        responses_lib.add(responses_lib.PUT, conn_url, json=CONN_REMOTE, status=200)
        responses_lib.add(
            responses_lib.GET,
            snap_url,
            body=b"\xff\xd8frame2",
            status=200,
            content_type="image/jpeg",
        )

        seen_session_ids: list[int] = []
        real_send = requests.Session.send

        def _spy_send(self: Any, *a: Any, **kw: Any) -> Any:
            seen_session_ids.append(id(self))
            return real_send(self, *a, **kw)

        with patch.object(requests.Session, "send", _spy_send):
            r1 = bc.snap_from_proxy(_cam(), token="tok")
            session_after_first = bc._HTTP_SESSION
            r2 = bc.snap_from_proxy(_cam(), token="tok")
            session_after_second = bc._HTTP_SESSION

        assert r1 == b"\xff\xd8frame1"
        assert r2 == b"\xff\xd8frame2"
        # 4 HTTP calls total (2x PUT /connection + 2x GET snap.jpg) but every
        # single one went out through the SAME session instance.
        assert len(seen_session_ids) == 4, seen_session_ids
        assert len(set(seen_session_ids)) == 1, "each call opened a different session"
        assert session_after_first is not None
        assert session_after_first is session_after_second

    def test_explicit_session_param_is_used_verbatim(self) -> None:
        """A caller-supplied session= must be the one actually used for both
        PUT /connection and the REMOTE snap.jpg GET (not silently replaced by
        a fresh make_session() session).
        """
        put_resp = MagicMock(status_code=200)
        put_resp.json.return_value = CONN_REMOTE
        snap_resp = MagicMock(status_code=200, content=b"\xff\xd8x")
        snap_resp.headers = {"Content-Type": "image/jpeg"}

        fake_session = MagicMock()
        fake_session.put.return_value = put_resp
        fake_session.get.return_value = snap_resp

        result = bc.snap_from_proxy(_cam(), token="tok", session=fake_session)

        assert result == b"\xff\xd8x"
        fake_session.put.assert_called_once()
        fake_session.get.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _live_snap_loop — fetcher thread's per-tick snap.jpg GET (cmd_live fallback)
# ─────────────────────────────────────────────────────────────────────────────


class TestLiveSnapLoopSessionReuse:
    """The fetcher thread must reuse ONE pooled cloud session across every
    poll tick instead of opening a fresh one-shot session per frame (the
    original bug: `requests_get_bosch_cloud(snap_url, timeout=10)` called
    every `interval` seconds, each call building its own new
    `requests.Session` under the hood).
    """

    def test_fetcher_reuses_single_pooled_session_across_ticks(self) -> None:
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {"Content-Type": "image/jpeg"}
        fake_resp.content = b"\xff\xd8frame"

        fake_session = MagicMock()
        fake_session.get.return_value = fake_resp

        mock_proc = MagicMock()
        # Simulate the user hitting Ctrl+C almost immediately — deterministic,
        # fast exit from _live_snap_loop for the test instead of relying on a
        # real subprocess/player.
        mock_proc.wait.side_effect = KeyboardInterrupt

        with (
            patch("shutil.which", return_value="/usr/bin/ffplay"),
            patch("os.path.exists", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
            patch.object(bc, "make_bosch_cloud_session", return_value=fake_session) as mock_factory,
        ):
            bc._live_snap_loop("https://proxy/snap.jpg", CAM_NAME, interval=0.05)

        # Exactly ONE pooled session created for the entire live-view
        # invocation — not one per frame.
        mock_factory.assert_called_once()
        assert fake_session.get.call_count >= 1
        # session torn down on exit, not leaked.
        fake_session.close.assert_called_once()
