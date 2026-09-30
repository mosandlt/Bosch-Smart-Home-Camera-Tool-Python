"""
Tests for the local data interface: status query, source planning, and the
`live` / `info` / `get_stream_url` / frigate front-door wiring.

PIN_EVERY_MODE: every status code (200/404/449/garbage/network error), firmware
gate (old/None/garbage/huge digits), Gen1 skip, and every password state
(none/blank/control chars/valid) gets its own test. Fake IDs/IPs/passwords only.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

import bosch_camera as bc
import bosch_frigate_endpoint
import bosch_local_data_interface as ldi

CAM_ID = "11111111-1111-1111-1111-111111111111"
NAME = "Testcam"
IP = "10.0.0.5"
PW = "test-pw"
GEN2 = "HOME_Eyes_Indoor"
FW_OK = "9.40.105"


def _cfg(
    pw: object = PW, ip: str | None = IP, model: str = GEN2, fw: str = FW_OK
) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "account": {"bearer_token": "tok", "refresh_token": ""},
        "cameras": {NAME: {"id": CAM_ID, "name": NAME, "model": model, "firmware": fw}},
    }
    if pw is not None:
        cfg["local_passwords"] = {CAM_ID: pw}
    if ip is not None:
        cfg["lan_ips"] = {CAM_ID: ip}
    return cfg


def _resp(status: int, body: object = None, bad_json: bool = False) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    if bad_json:
        r.json.side_effect = ValueError("bad")
    else:
        r.json.return_value = body
    return r


def _session(resp: MagicMock | Exception) -> MagicMock:
    s = MagicMock()
    if isinstance(resp, Exception):
        s.get.side_effect = resp
    else:
        s.get.return_value = resp
    return s


ACTIVE = _resp(200, {"username": "localuser"})


class TestFirmware:
    @pytest.mark.parametrize("fw", ["9.40.105", "9.40.202", "9.41.0", "10.0.0", " 9.40.105 "])
    def test_supported(self, fw: str) -> None:
        assert ldi.firmware_supports(fw)

    @pytest.mark.parametrize(
        "fw",
        [
            "9.40.104",
            "9.40",
            "8.99.999",
            None,
            9,
            "",
            "abc",
            "9.40.x",
            "9..105",
            "9" * 50,
            "-9.40.105",
        ],
    )
    def test_not_supported(self, fw: object) -> None:
        assert not ldi.firmware_supports(fw)

    def test_gen1_never_eligible(self) -> None:
        assert not ldi.eligible("CAMERA_EYES", FW_OK)
        assert not ldi.eligible("CAMERA_360", FW_OK)
        assert not ldi.eligible(None, FW_OK)

    def test_gen2_variants_eligible(self) -> None:
        assert ldi.eligible("HOME_Eyes_Outdoor", FW_OK)
        assert ldi.eligible("CAMERA_INDOOR_GEN2", FW_OK)
        assert not ldi.eligible(GEN2, "9.40.104")


class TestStatus:
    def test_active(self) -> None:
        assert ldi.fetch_state(_session(ACTIVE), "https://x", CAM_ID) == ldi.STATE_ACTIVE

    def test_active_requests_status_path(self) -> None:
        s = _session(ACTIVE)
        ldi.fetch_state(s, "https://x", CAM_ID)
        assert (
            s.get.call_args.args[0] == f"https://x/v11/video_inputs/{CAM_ID}/{ldi.STATUS_ENDPOINT}"
        )

    def test_inactive_404(self) -> None:
        assert ldi.fetch_state(_session(_resp(404, {})), "https://x", CAM_ID) == ldi.STATE_INACTIVE

    def test_unsupported_449(self) -> None:
        r = _resp(449, {})
        assert ldi.fetch_state(_session(r), "https://x", CAM_ID) == ldi.STATE_UNSUPPORTED

    @pytest.mark.parametrize("status", [401, 403, 500, 502, 204])
    def test_other_status_unknown(self, status: int) -> None:
        assert ldi.fetch_state(_session(_resp(status, {})), "https://x", CAM_ID) is None

    @pytest.mark.parametrize("body", [None, [], "x", {}, {"username": 5}, {"user": "a"}])
    def test_200_garbage_body_unknown(self, body: object) -> None:
        assert ldi.fetch_state(_session(_resp(200, body)), "https://x", CAM_ID) is None

    def test_200_unparseable_body_unknown(self) -> None:
        assert ldi.fetch_state(_session(_resp(200, bad_json=True)), "https://x", CAM_ID) is None

    def test_network_error_unknown(self) -> None:
        s = _session(requests.exceptions.ConnectionError("down"))
        assert ldi.fetch_state(s, "https://x", CAM_ID) is None

    def test_query_skips_gen1(self) -> None:
        s = _session(ACTIVE)
        assert ldi.query_state(s, "https://x", CAM_ID, "CAMERA_EYES", FW_OK) is None
        s.get.assert_not_called()

    @pytest.mark.parametrize("fw", ["9.40.104", None, "junk", "9" * 40])
    def test_query_skips_old_or_bad_firmware(self, fw: object) -> None:
        s = _session(ACTIVE)
        assert ldi.query_state(s, "https://x", CAM_ID, GEN2, fw) is None
        s.get.assert_not_called()

    def test_query_runs_for_eligible(self) -> None:
        assert ldi.query_state(_session(ACTIVE), "https://x", CAM_ID, GEN2, FW_OK) == "active"


class TestPasswordAndIp:
    @pytest.mark.parametrize("pw", [None, "", "   ", "a\nb", "a\x00b", 5, ["x"], "tab\there"])
    def test_invalid_passwords_ignored(self, pw: object) -> None:
        assert ldi.get_password(_cfg(pw=pw), CAM_ID) is None

    def test_valid_password(self) -> None:
        assert ldi.get_password(_cfg(), CAM_ID) == PW

    def test_passwords_not_a_dict(self) -> None:
        assert ldi.get_password({"local_passwords": "x"}, CAM_ID) is None

    @pytest.mark.parametrize("ip", ["10.0.0.5", "192.168.1.9", "172.16.3.4", " 10.0.0.5 "])
    def test_safe_ips(self, ip: str) -> None:
        assert ldi.safe_lan_ip(ip) == ip.strip()

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "169.254.1.1",
            "0.0.0.0",
            "8.8.8.8",
            "224.0.0.1",
            "::1",
            "fd00::1",
            "host.example",
            "",
            None,
            5,
            "10.0.0.5:9554",
            "10.0.0.5@evil.example",
        ],
    )
    def test_unsafe_ips(self, ip: object) -> None:
        assert ldi.safe_lan_ip(ip) is None

    def test_url_quotes_password(self) -> None:
        url = ldi.build_url(IP, "p@ss:w/rd#?")
        assert (
            url
            == "rtsps://localuser:p%40ss%3Aw%2Frd%23%3F@10.0.0.5:9554/rtsp_tunnel?line=1&inst=1&enableaudio=1"
        )


class TestUrlModes:
    @pytest.mark.parametrize(
        ("quality", "audio", "inst", "aud"),
        [("high", True, 1, 1), ("high", False, 1, 0), ("low", True, 2, 1), ("low", False, 2, 0)],
    )
    def test_modes(self, quality: str, audio: bool, inst: int, aud: int) -> None:
        url = ldi.build_url(IP, "pw", quality, audio)
        assert url.endswith(f"/rtsp_tunnel?line=1&inst={inst}&enableaudio={aud}")

    def test_garbage_quality_defaults_high(self) -> None:
        assert "inst=1&" in ldi.build_url(IP, "pw", "bogus")

    def test_plan_passes_quality_and_audio(self) -> None:
        _, url, _ = ldi.plan_source(_cfg(), CAM_ID, "active", IP, "low", False)
        assert url is not None and url.endswith("inst=2&enableaudio=0")


class TestPlan:
    def test_active_with_password_is_local(self) -> None:
        action, url, msg = ldi.plan_source(_cfg(), CAM_ID, "active", IP)
        assert action == ldi.ACTION_LOCAL
        assert url == f"rtsps://localuser:test-pw@{IP}:9554/rtsp_tunnel?line=1&inst=1&enableaudio=1"
        assert msg is None

    def test_active_without_password_stays_cloud_with_hint(self) -> None:
        action, url, msg = ldi.plan_source(_cfg(pw=None), CAM_ID, "active", IP)
        assert (action, url) == (ldi.ACTION_CLOUD, None)
        assert msg and "set-password" in msg

    @pytest.mark.parametrize("state", ["inactive", "unsupported", None])
    def test_not_active_stays_cloud_silently(self, state: str | None) -> None:
        assert ldi.plan_source(_cfg(), CAM_ID, state, IP) == (ldi.ACTION_CLOUD, None, None)

    @pytest.mark.parametrize("ip", [None, "", "8.8.8.8", "127.0.0.1", "169.254.0.2", "0.0.0.0"])
    def test_active_password_bad_ip_blocks(self, ip: object) -> None:
        action, url, msg = ldi.plan_source(_cfg(), CAM_ID, "active", ip)
        assert (action, url) == (ldi.ACTION_BLOCKED, None)
        assert msg and "lan-ips" in msg

    def test_messages_never_contain_password(self) -> None:
        for cfg, ip in ((_cfg(pw=None), IP), (_cfg(), None)):
            msg = ldi.plan_source(cfg, CAM_ID, "active", ip)[2]
            assert msg and PW not in msg


def _live_args(**kw: Any) -> argparse.Namespace:
    base = {
        "cam": NAME,
        "hq": False,
        "inst": None,
        "sub": False,
        "local": False,
        "quality": None,
        "webrtc": False,
        "vlc": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestCmdLive:
    def _run(
        self, cfg: dict[str, Any], state_resp: MagicMock, **kw: Any
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        session = _session(state_resp)
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=session),
            patch.object(bc, "api_ping", return_value="ONLINE") as ping,
            patch.object(bc, "_open_rtsps_stream") as play,
            patch.object(bc, "_open_webrtc_stream") as webrtc,
            patch.object(bc, "save_config"),
            patch.object(bc, "api_get_events", return_value=[]),
        ):
            session.put.return_value = _resp(500, {})
            bc.cmd_live(cfg, _live_args(**kw))
        self.ping = ping
        return session, play, webrtc

    def test_local_source_used_no_cloud_session(self, capsys: pytest.CaptureFixture[str]) -> None:
        session, play, _ = self._run(_cfg(), ACTIVE)
        session.put.assert_not_called()
        self.ping.assert_not_called()
        play.assert_called_once()
        assert (
            play.call_args.args[0]
            == f"rtsps://localuser:test-pw@{IP}:9554/rtsp_tunnel?line=1&inst=1&enableaudio=1"
        )
        out = capsys.readouterr().out
        assert PW not in out
        assert "***:***@" in out

    @pytest.mark.parametrize(
        ("kw", "inst"),
        [({}, 1), ({"quality": "high"}, 1), ({"quality": "low"}, 2), ({"sub": True}, 2)],
    )
    def test_quality_selects_inst(self, kw: dict[str, Any], inst: int) -> None:
        _, play, _ = self._run(_cfg(), ACTIVE, **kw)
        url = play.call_args.args[0]
        assert f"/rtsp_tunnel?line=1&inst={inst}&enableaudio=1" in url

    def test_local_source_webrtc(self) -> None:
        session, play, webrtc = self._run(_cfg(), ACTIVE, webrtc=True)
        session.put.assert_not_called()
        play.assert_not_called()
        assert webrtc.call_args.args[0].startswith("rtsps://localuser:")

    def test_blocked_without_ip_fails_closed(self, capsys: pytest.CaptureFixture[str]) -> None:
        session, play, webrtc = self._run(_cfg(ip=None), ACTIVE)
        session.put.assert_not_called()
        play.assert_not_called()
        webrtc.assert_not_called()
        out = capsys.readouterr().out
        assert "No cloud stream is opened" in out
        assert PW not in out

    def test_active_without_password_uses_cloud_path(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        session, play, _ = self._run(_cfg(pw=None), ACTIVE)
        assert session.put.called
        play.assert_not_called()
        assert "set-password" in capsys.readouterr().out

    @pytest.mark.parametrize("resp", [_resp(404, {}), _resp(449, {}), _resp(500, {})])
    def test_inactive_unsupported_unknown_use_cloud_path(self, resp: MagicMock) -> None:
        session, play, _ = self._run(_cfg(), resp)
        assert session.put.called
        play.assert_not_called()

    def test_network_error_uses_cloud_path(self) -> None:
        session, play, _ = self._run(_cfg(), _resp(200, bad_json=True))
        assert session.put.called

    def test_gen1_never_queried(self) -> None:
        session, _, _ = self._run(_cfg(model="CAMERA_EYES"), ACTIVE)
        assert all(ldi.STATUS_ENDPOINT not in c.args[0] for c in session.get.call_args_list)
        assert session.put.called

    def test_old_firmware_not_queried(self) -> None:
        session, _, _ = self._run(_cfg(fw="9.40.104"), ACTIVE)
        assert all(ldi.STATUS_ENDPOINT not in c.args[0] for c in session.get.call_args_list)
        assert session.put.called


class TestGetStreamUrl:
    @pytest.mark.parametrize(("hq", "inst"), [(True, 1), (False, 2)])
    def test_local_data_source(self, hq: bool, inst: int) -> None:
        cfg = _cfg()
        with patch.object(bc, "make_session", return_value=_session(ACTIVE)):
            res = bc.get_stream_url(cfg["cameras"][NAME], "tok", hq=hq, cfg=cfg)
        assert res is not None
        assert res["type"] == "LOCAL_DATA"
        assert (
            res["url"]
            == f"rtsps://localuser:test-pw@{IP}:9554/rtsp_tunnel?line=1&inst={inst}&enableaudio=1"
        )

    def test_blocked_returns_none_without_cloud_put(self) -> None:
        cfg = _cfg(ip=None)
        with (
            patch.object(bc, "make_session", return_value=_session(ACTIVE)),
            patch.object(bc.requests, "put") as put,
        ):
            assert bc.get_stream_url(cfg["cameras"][NAME], "tok", cfg=cfg) is None
        put.assert_not_called()

    def test_no_cfg_skips_interface(self) -> None:
        cfg = _cfg()
        with (
            patch.object(bc, "make_session") as ms,
            patch.object(bc.requests, "put", return_value=_resp(500, {})),
        ):
            assert bc.get_stream_url(cfg["cameras"][NAME], "tok") is None
        ms.assert_not_called()

    def test_inactive_falls_through_to_cloud(self) -> None:
        cfg = _cfg()
        with (
            patch.object(bc, "make_session", return_value=_session(_resp(404, {}))),
            patch.object(bc.requests, "put", return_value=_resp(500, {})) as put,
        ):
            assert bc.get_stream_url(cfg["cameras"][NAME], "tok", cfg=cfg) is None
        assert put.called


class TestCmdInfo:
    def _info(
        self, cfg: dict[str, Any], state_resp: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> tuple[str, MagicMock]:
        cam = {
            "id": CAM_ID,
            "title": NAME,
            "connectionStatus": "ONLINE",
            "hardwareVersion": GEN2,
            "firmwareVersion": FW_OK,
            "notifications": {},
            "featureSupport": {},
            "featureStatus": {},
        }
        session = MagicMock()

        def _get(url: str, **_kw: Any) -> MagicMock:
            if url.endswith("/" + ldi.STATUS_ENDPOINT):
                return state_resp
            if url.endswith("/video_inputs"):
                return _resp(200, [cam])
            return _resp(404, {})

        session.get.side_effect = _get
        session.put.return_value = _resp(500, {})
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=session),
            patch.object(bc, "check_token_age", return_value="0 min"),
        ):
            bc.cmd_info(cfg, argparse.Namespace(full=False))
        return capsys.readouterr().out, session

    def test_shows_state_and_skips_cloud_stream_when_local(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out, session = self._info(_cfg(), ACTIVE, capsys)
        assert "Local data:    active" in out
        session.put.assert_not_called()
        assert PW not in out

    def test_inactive_keeps_cloud_stream_fetch(self, capsys: pytest.CaptureFixture[str]) -> None:
        out, session = self._info(_cfg(), _resp(404, {}), capsys)
        assert "Local data:    inactive" in out
        assert session.put.called

    def test_unknown_prints_no_state_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        out, session = self._info(_cfg(), _resp(500, {}), capsys)
        assert "Local data:" not in out
        assert session.put.called


class TestCmdLocalData:
    def test_status_active_no_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=_session(ACTIVE)),
        ):
            bc.cmd_local_data(_cfg(pw=None), argparse.Namespace(ldi_sub=None, ldi_cam=None))
        out = capsys.readouterr().out
        assert "active" in out and "not set" in out and "set-password" in out

    def test_status_masks_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=_session(ACTIVE)),
        ):
            bc.cmd_local_data(_cfg(), argparse.Namespace(ldi_sub="status", ldi_cam=NAME))
        out = capsys.readouterr().out
        assert PW not in out and ldi.MASK in out

    def test_status_gen1_not_applicable_no_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.object(bc, "get_token") as gt:
            bc.cmd_local_data(
                _cfg(model="CAMERA_EYES"), argparse.Namespace(ldi_sub=None, ldi_cam=None)
            )
        gt.assert_not_called()
        assert "not applicable" in capsys.readouterr().out

    def test_status_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=_session(_resp(500, {}))),
        ):
            bc.cmd_local_data(_cfg(), argparse.Namespace(ldi_sub=None, ldi_cam=None))
        assert "unknown" in capsys.readouterr().out

    def test_set_password_stores_and_never_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _cfg(pw=None)
        with patch.object(bc, "save_config") as save, patch("getpass.getpass", return_value=PW):
            bc.cmd_local_data(cfg, argparse.Namespace(ldi_sub="set-password", ldi_cam=NAME))
        assert cfg["local_passwords"][CAM_ID] == PW
        save.assert_called_once()
        assert PW not in capsys.readouterr().out

    @pytest.mark.parametrize("bad", ["", "  ", "a\nb"])
    def test_set_password_rejects_bad_format(
        self, bad: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = _cfg(pw=None)
        with patch.object(bc, "save_config") as save, patch("getpass.getpass", return_value=bad):
            bc.cmd_local_data(cfg, argparse.Namespace(ldi_sub="set-password", ldi_cam=NAME))
        assert "local_passwords" not in cfg or CAM_ID not in cfg["local_passwords"]
        save.assert_not_called()
        assert "Invalid password" in capsys.readouterr().out

    def test_set_password_needs_camera(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.object(bc, "save_config") as save:
            bc.cmd_local_data(_cfg(), argparse.Namespace(ldi_sub="set-password", ldi_cam=None))
        save.assert_not_called()
        assert "Usage" in capsys.readouterr().out

    def test_set_password_ambiguous_camera_exits_without_saving(self) -> None:
        cfg = _cfg()
        cfg["cameras"]["Other"] = {"id": "22222222-2222-2222-2222-222222222222"}
        with patch.object(bc, "save_config") as save, pytest.raises(SystemExit):
            bc.cmd_local_data(cfg, argparse.Namespace(ldi_sub="set-password", ldi_cam="e"))
        save.assert_not_called()

    def test_unset_password(self) -> None:
        cfg = _cfg()
        with patch.object(bc, "save_config") as save:
            bc.cmd_local_data(cfg, argparse.Namespace(ldi_sub="unset-password", ldi_cam=NAME))
        assert CAM_ID not in cfg["local_passwords"]
        save.assert_called_once()

    def test_unset_password_needs_camera(self, capsys: pytest.CaptureFixture[str]) -> None:
        bc.cmd_local_data(_cfg(), argparse.Namespace(ldi_sub="unset-password", ldi_cam=None))
        assert "Usage" in capsys.readouterr().out

    def test_dispatch_registered(self) -> None:
        with (
            patch("sys.argv", ["bosch-camera", "local-data", "status"]),
            patch.object(bc, "load_config", return_value=_cfg(model="CAMERA_EYES")),
            patch.object(bc, "set_lang"),
        ):
            bc.main()


class TestFrigateFailClosed:
    def _resolve(self, cfg: dict[str, Any], state_resp: MagicMock) -> tuple[Any, MagicMock]:
        session = _session(state_resp)
        session.put.return_value = _resp(200, {"urls": [f"{IP}:443"], "user": "u", "password": "p"})
        with (
            patch.object(bc, "get_token", return_value="tok"),
            patch.object(bc, "make_session", return_value=session),
        ):
            target = bosch_frigate_endpoint._resolve_camera_target_sync(cfg, CAM_ID, False)
        return target, session

    def test_local_only_camera_opens_no_cloud_session(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            target, session = self._resolve(_cfg(), ACTIVE)
        assert target is None
        session.put.assert_not_called()
        assert PW not in caplog.text

    def test_active_without_password_uses_cloud(self) -> None:
        target, session = self._resolve(_cfg(pw=None), ACTIVE)
        assert target is not None
        assert session.put.called

    def test_inactive_uses_cloud(self) -> None:
        target, _ = self._resolve(_cfg(), _resp(404, {}))
        assert target is not None

    def test_gen1_uses_cloud_without_query(self) -> None:
        target, session = self._resolve(_cfg(model="CAMERA_EYES"), ACTIVE)
        assert target is not None
        session.get.assert_not_called()


class TestNoPasswordInLogs:
    def test_module_logs_nothing_with_password(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG):
            ldi.fetch_state(_session(requests.exceptions.ConnectionError(PW)), "https://x", CAM_ID)
            ldi.plan_source(_cfg(), CAM_ID, "active", IP)
        assert PW not in caplog.text
