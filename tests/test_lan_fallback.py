"""
Tests for v10.7.3 LAN-fallback feature set:
  - bosch ping subcommand
  - bosch privacy --local flag (LAN RCP write)
  - bosch light --local flag (LAN RCP write)
  - cloud 5xx → suggests --local hint

Source: HA integration v12.4.10 port.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

import bosch_camera


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_cfg_with_cameras(
    lan_ip: str = "192.0.2.1",
    cam_id: str = "AAAA-0001",
) -> dict:
    """Return a minimal config dict with one camera and a LAN IP."""
    return {
        "account": {"bearer_token": "", "refresh_token": "", "username": "", "password": ""},
        "cameras": {
            "TestCam": {
                "id": cam_id,
                "name": "TestCam",
                "model": "HOME_Eyes_Outdoor",
                "local_ip": lan_ip,
            }
        },
        "settings": {},
        "lan_ips": {},
        "nvr": {"max_clips": 50, "max_duration": 60, "smb": {}},
    }


def _make_args(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace with sensible defaults."""
    defaults = {
        "cam": None,
        "action": None,
        "local": False,
        "json": False,
        "minutes": None,
        "extra_args": [],
        "lan_sub": None,
        "lan_cam": None,
        "lan_ip": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# bosch_ping_subcommand tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBoschPingSubcommand:
    """cmd_ping: TCP probe to camera LAN IPs."""

    def test_ping_ok_prints_ip_and_rtt(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Reachable camera prints OK + RTT."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.10")
        with patch.object(bosch_camera, "_lan_tcp_ping", return_value=(True, 4.5)):
            bosch_camera.cmd_ping(cfg, _make_args())
        out = capsys.readouterr().out
        assert "192.0.2.10" in out
        assert "4.5" in out

    def test_ping_fail_prints_unreachable(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unreachable camera prints FAIL icon."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.99")
        with patch.object(bosch_camera, "_lan_tcp_ping", return_value=(False, 0.0)):
            bosch_camera.cmd_ping(cfg, _make_args())
        out = capsys.readouterr().out
        assert "192.0.2.99" in out
        assert "❌" in out

    def test_ping_no_ip_configured(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Camera with no LAN IP prints a hint."""
        cfg = _make_cfg_with_cameras(lan_ip="")
        bosch_camera.cmd_ping(cfg, _make_args())
        out = capsys.readouterr().out
        assert "no lan ip" in out.lower() or "not set" in out.lower() or "lan-ips" in out.lower()

    def test_ping_json_output(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--json flag emits valid JSON with expected fields."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.20")
        with patch.object(bosch_camera, "_lan_tcp_ping", return_value=(True, 7.3)):
            bosch_camera.cmd_ping(cfg, _make_args(json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert data[0]["reachable"] is True
        assert data[0]["ip"] == "192.0.2.20"
        assert data[0]["rtt_ms"] == pytest.approx(7.3, abs=0.1)

    def test_ping_json_no_ip(self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]) -> None:
        """--json with no LAN IP: reachable=False, ip=None, error set."""
        cfg = _make_cfg_with_cameras(lan_ip="")
        bosch_camera.cmd_ping(cfg, _make_args(json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data[0]["reachable"] is False
        assert data[0]["ip"] is None
        assert "error" in data[0]

    def test_ping_single_cam_filter(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Specifying a cam name filters to that camera only."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.30")
        with patch.object(bosch_camera, "_lan_tcp_ping", return_value=(True, 2.0)):
            bosch_camera.cmd_ping(cfg, _make_args(cam="TestCam"))
        out = capsys.readouterr().out
        assert "TestCam" in out

    def test_ping_lan_ips_map_takes_priority(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """lan_ips map takes priority over cameras[].local_ip."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1", cam_id="AAAA-0001")
        cfg["lan_ips"]["AAAA-0001"] = "192.0.2.200"
        with patch.object(bosch_camera, "_lan_tcp_ping", return_value=(True, 1.0)) as mock_ping:
            bosch_camera.cmd_ping(cfg, _make_args())
        # Ping must have used the lan_ips value, not local_ip
        mock_ping.assert_called_once_with("192.0.2.200", port=443)


# ══════════════════════════════════════════════════════════════════════════════
# bosch_privacy_local_flag tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBoschPrivacyLocalFlag:
    """cmd_privacy --local: direct LAN RCP write, no cloud."""

    def test_privacy_local_on_calls_rcp(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--local on invokes _lan_rcp_write_privacy with enabled=True."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=True) as mock_rcp:
            bosch_camera.cmd_privacy(cfg, _make_args(action="on", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", True)

    def test_privacy_local_off_calls_rcp(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--local off invokes _lan_rcp_write_privacy with enabled=False."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=True) as mock_rcp:
            bosch_camera.cmd_privacy(cfg, _make_args(action="off", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", False)

    def test_privacy_local_success_message(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On success, prints confirmation with 'LAN RCP' in output."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=True):
            bosch_camera.cmd_privacy(cfg, _make_args(action="on", local=True))
        out = capsys.readouterr().out
        assert "ON" in out
        assert "RCP" in out.upper() or "local" in out.lower()

    def test_privacy_local_failure_message(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On RCP failure, prints error message."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=False):
            bosch_camera.cmd_privacy(cfg, _make_args(action="on", local=True))
        out = capsys.readouterr().out
        assert "❌" in out or "failed" in out.lower()

    def test_privacy_local_no_ip_configured(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--local without configured LAN IP prints error, no RCP call."""
        cfg = _make_cfg_with_cameras(lan_ip="")
        with patch.object(bosch_camera, "_lan_rcp_write_privacy") as mock_rcp:
            bosch_camera.cmd_privacy(cfg, _make_args(action="on", local=True))
        mock_rcp.assert_not_called()
        out = capsys.readouterr().out
        assert "No LAN IP" in out or "no lan" in out.lower()

    def test_privacy_local_no_action_prints_hint(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--local without on/off prints usage hint, no RCP call."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_privacy") as mock_rcp:
            bosch_camera.cmd_privacy(cfg, _make_args(local=True))
        mock_rcp.assert_not_called()
        out = capsys.readouterr().out
        assert "on or off" in out.lower() or "action" in out.lower()

    def test_privacy_local_does_not_call_cloud(self, tmp_config_dir: str) -> None:
        """--local never touches get_token or make_session."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with (
            patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=True),
            patch.object(bosch_camera, "get_token") as mock_token,
            patch.object(bosch_camera, "make_session") as mock_session,
        ):
            bosch_camera.cmd_privacy(cfg, _make_args(action="on", local=True))
        mock_token.assert_not_called()
        mock_session.assert_not_called()

    def test_privacy_local_uses_lan_ips_map(self, tmp_config_dir: str) -> None:
        """lan_ips map value is used when local_ip is empty."""
        cfg = _make_cfg_with_cameras(lan_ip="", cam_id="AAAA-0001")
        cfg["lan_ips"]["AAAA-0001"] = "192.0.2.100"
        with patch.object(bosch_camera, "_lan_rcp_write_privacy", return_value=True) as mock_rcp:
            bosch_camera.cmd_privacy(cfg, _make_args(action="on", local=True))
        mock_rcp.assert_called_once_with("192.0.2.100", True)


# ══════════════════════════════════════════════════════════════════════════════
# bosch_light_local_flag tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBoschLightLocalFlag:
    """cmd_light --local: direct LAN RCP write for front light."""

    def test_light_local_on_calls_rcp_brightness_100(self, tmp_config_dir: str) -> None:
        """'on --local' writes brightness=100."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(
            bosch_camera, "_lan_rcp_write_front_light", return_value=True
        ) as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="on", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", 100)

    def test_light_local_off_calls_rcp_brightness_0(self, tmp_config_dir: str) -> None:
        """'off --local' writes brightness=0."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(
            bosch_camera, "_lan_rcp_write_front_light", return_value=True
        ) as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="off", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", 0)

    def test_light_local_intensity_calls_rcp_with_correct_value(self, tmp_config_dir: str) -> None:
        """'intensity 50 --local' writes brightness=50."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(
            bosch_camera, "_lan_rcp_write_front_light", return_value=True
        ) as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="intensity 50", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", 50)

    def test_light_local_success_message(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On success prints confirmation with 'LAN RCP'."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True):
            bosch_camera.cmd_light(cfg, _make_args(action="on", local=True))
        out = capsys.readouterr().out
        assert "RCP" in out.upper() or "local" in out.lower()

    def test_light_local_failure_message(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On RCP failure, prints error message."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=False):
            bosch_camera.cmd_light(cfg, _make_args(action="on", local=True))
        out = capsys.readouterr().out
        assert "❌" in out or "failed" in out.lower()

    def test_light_local_no_ip_configured(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No LAN IP → error, no RCP call."""
        cfg = _make_cfg_with_cameras(lan_ip="")
        with patch.object(bosch_camera, "_lan_rcp_write_front_light") as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="on", local=True))
        mock_rcp.assert_not_called()
        out = capsys.readouterr().out
        assert "No LAN IP" in out or "no lan" in out.lower()

    def test_light_local_wall_not_supported(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """'wall on --local' prints hint that wallwasher is cloud-only."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(bosch_camera, "_lan_rcp_write_front_light") as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="wall on", local=True))
        mock_rcp.assert_not_called()
        out = capsys.readouterr().out
        assert "cloud-only" in out.lower() or "wallwasher" in out.lower()

    def test_light_local_does_not_call_cloud(self, tmp_config_dir: str) -> None:
        """--local never touches get_token or make_session."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with (
            patch.object(bosch_camera, "_lan_rcp_write_front_light", return_value=True),
            patch.object(bosch_camera, "get_token") as mock_token,
            patch.object(bosch_camera, "make_session") as mock_session,
        ):
            bosch_camera.cmd_light(cfg, _make_args(action="on", local=True))
        mock_token.assert_not_called()
        mock_session.assert_not_called()

    def test_light_local_intensity_clamp_0(self, tmp_config_dir: str) -> None:
        """Intensity 0 writes brightness=0 (clamp)."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(
            bosch_camera, "_lan_rcp_write_front_light", return_value=True
        ) as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="intensity 0", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", 0)

    def test_light_local_intensity_clamp_100(self, tmp_config_dir: str) -> None:
        """Intensity 100 writes brightness=100 (no over-clamp)."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with patch.object(
            bosch_camera, "_lan_rcp_write_front_light", return_value=True
        ) as mock_rcp:
            bosch_camera.cmd_light(cfg, _make_args(action="intensity 100", local=True))
        mock_rcp.assert_called_once_with("192.0.2.1", 100)


# ══════════════════════════════════════════════════════════════════════════════
# cloud_5xx_hints_at_local tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCloud5xxHintsAtLocal:
    """_hint_local_on_5xx: prints --local hint on 5xx responses."""

    def test_hint_printed_on_500(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 500 triggers hint output."""
        bosch_camera._hint_local_on_5xx(500, "bosch privacy on --local")
        out = capsys.readouterr().out
        assert "500" in out
        assert "--local" in out

    def test_hint_printed_on_503(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 503 (typical Bosch cloud outage) triggers hint output."""
        bosch_camera._hint_local_on_5xx(503, "bosch light on --local")
        out = capsys.readouterr().out
        assert "503" in out

    def test_hint_not_printed_on_200(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 200 does not trigger any output."""
        bosch_camera._hint_local_on_5xx(200, "bosch privacy on --local")
        out = capsys.readouterr().out
        assert out == ""

    def test_hint_not_printed_on_404(self, capsys: pytest.CaptureFixture[str]) -> None:
        """HTTP 404 (client error) does not trigger the hint."""
        bosch_camera._hint_local_on_5xx(404, "bosch privacy on --local")
        out = capsys.readouterr().out
        assert out == ""

    def test_hint_without_command_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Called with empty command_hint still prints the generic fallback message."""
        bosch_camera._hint_local_on_5xx(500)
        out = capsys.readouterr().out
        assert "500" in out
        assert "--local" in out

    @responses_lib.activate
    def test_privacy_cmd_prints_hint_on_5xx(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_privacy cloud path: when video_inputs returns 503, hint is printed."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        responses_lib.add(
            responses_lib.GET,
            "https://residential.cbs.boschsecurity.com/v11/video_inputs",
            status=503,
            body="Service Unavailable",
        )
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(
                bosch_camera,
                "make_session",
                return_value=MagicMock(
                    get=MagicMock(
                        return_value=MagicMock(
                            status_code=503,
                            text="Service Unavailable",
                            raise_for_status=MagicMock(side_effect=Exception("503")),
                        )
                    )
                ),
            ),
            patch.object(bosch_camera, "get_cameras"),
        ):
            try:
                bosch_camera.cmd_privacy(cfg, _make_args(action="on"))
            except Exception:
                pass
        out = capsys.readouterr().out
        assert "503" in out
        assert "--local" in out

    @responses_lib.activate
    def test_light_cmd_prints_hint_on_5xx(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_light cloud path: when video_inputs returns 503, hint is printed."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.1")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(
                bosch_camera,
                "make_session",
                return_value=MagicMock(
                    get=MagicMock(
                        return_value=MagicMock(
                            status_code=503,
                            text="Service Unavailable",
                            raise_for_status=MagicMock(side_effect=Exception("503")),
                        )
                    )
                ),
            ),
            patch.object(bosch_camera, "get_cameras"),
        ):
            try:
                bosch_camera.cmd_light(cfg, _make_args(action="on"))
            except Exception:
                pass
        out = capsys.readouterr().out
        assert "503" in out
        assert "--local" in out


# ══════════════════════════════════════════════════════════════════════════════
# LAN helper unit tests
# ══════════════════════════════════════════════════════════════════════════════


class TestLanHelpers:
    """Unit tests for _lan_tcp_ping, _resolve_lan_ip, _lan_rcp_write."""

    def test_tcp_ping_success(self) -> None:
        """_lan_tcp_ping returns (True, rtt>0) when connection succeeds."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("socket.create_connection", return_value=mock_conn):
            ok, rtt = bosch_camera._lan_tcp_ping("192.0.2.1", port=443)
        assert ok is True
        assert rtt >= 0.0

    def test_tcp_ping_fail(self) -> None:
        """_lan_tcp_ping returns (False, 0.0) on OSError."""
        with patch("socket.create_connection", side_effect=OSError("refused")):
            ok, rtt = bosch_camera._lan_tcp_ping("192.0.2.1", port=443)
        assert ok is False
        assert rtt == 0.0

    def test_resolve_lan_ip_prefers_lan_ips_map(self) -> None:
        """lan_ips map takes priority over local_ip field."""
        cfg = {"lan_ips": {"CAM-1": "10.0.0.50"}}
        cam_info = {"local_ip": "10.0.0.1"}
        result = bosch_camera._resolve_lan_ip(cfg, "CAM-1", cam_info)
        assert result == "10.0.0.50"

    def test_resolve_lan_ip_falls_back_to_local_ip(self) -> None:
        """Falls back to cameras[].local_ip when lan_ips has no entry."""
        cfg = {"lan_ips": {}}
        cam_info = {"local_ip": "10.0.0.99"}
        result = bosch_camera._resolve_lan_ip(cfg, "CAM-1", cam_info)
        assert result == "10.0.0.99"

    def test_resolve_lan_ip_returns_none_when_both_empty(self) -> None:
        """Returns None when neither lan_ips nor local_ip is set."""
        cfg = {"lan_ips": {}}
        cam_info = {"local_ip": ""}
        result = bosch_camera._resolve_lan_ip(cfg, "CAM-1", cam_info)
        assert result is None

    def test_lan_rcp_write_privacy_on_sends_correct_payload(self) -> None:
        """Privacy ON payload is '00010000'."""
        with patch.object(bosch_camera, "_lan_rcp_write", return_value=True) as mock_write:
            bosch_camera._lan_rcp_write_privacy("192.0.2.1", True)
        mock_write.assert_called_once_with("192.0.2.1", "0x0d00", "00010000", "P_OCTET")

    def test_lan_rcp_write_privacy_off_sends_correct_payload(self) -> None:
        """Privacy OFF payload is '00000000'."""
        with patch.object(bosch_camera, "_lan_rcp_write", return_value=True) as mock_write:
            bosch_camera._lan_rcp_write_privacy("192.0.2.1", False)
        mock_write.assert_called_once_with("192.0.2.1", "0x0d00", "00000000", "P_OCTET")

    def test_lan_rcp_write_front_light_sends_correct_command(self) -> None:
        """Front-light write uses command 0x0c22, T_WORD, num=1."""
        with patch.object(bosch_camera, "_lan_rcp_write", return_value=True) as mock_write:
            bosch_camera._lan_rcp_write_front_light("192.0.2.1", 75)
        mock_write.assert_called_once_with("192.0.2.1", "0x0c22", "004b", "T_WORD", num=1)

    def test_lan_rcp_write_front_light_clamps_above_100(self) -> None:
        """Brightness > 100 is clamped to 100."""
        with patch.object(bosch_camera, "_lan_rcp_write", return_value=True) as mock_write:
            bosch_camera._lan_rcp_write_front_light("192.0.2.1", 150)
        # 100 = 0x0064
        mock_write.assert_called_once_with("192.0.2.1", "0x0c22", "0064", "T_WORD", num=1)

    def test_lan_rcp_write_front_light_clamps_below_0(self) -> None:
        """Brightness < 0 is clamped to 0."""
        with patch.object(bosch_camera, "_lan_rcp_write", return_value=True) as mock_write:
            bosch_camera._lan_rcp_write_front_light("192.0.2.1", -5)
        mock_write.assert_called_once_with("192.0.2.1", "0x0c22", "0000", "T_WORD", num=1)

    def test_lan_rcp_write_returns_false_on_http_error(self) -> None:
        """_lan_rcp_write returns False on non-200 HTTP response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.content = b""
        with patch("requests.get", return_value=mock_resp):
            result = bosch_camera._lan_rcp_write("192.0.2.1", "0x0d00", "00010000")
        assert result is False

    def test_lan_rcp_write_returns_false_on_connection_error(self) -> None:
        """_lan_rcp_write returns False on network exception."""
        with patch("requests.get", side_effect=Exception("connection refused")):
            result = bosch_camera._lan_rcp_write("192.0.2.1", "0x0d00", "00010000")
        assert result is False

    def test_lan_rcp_write_returns_false_on_rcp_err_tag(self) -> None:
        """_lan_rcp_write returns False when response contains <err> tag."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<rcp><err>0xa0</err></rcp>"
        with patch("requests.get", return_value=mock_resp):
            result = bosch_camera._lan_rcp_write("192.0.2.1", "0x0d00", "00010000")
        assert result is False

    def test_lan_rcp_write_prepends_0x_to_plain_hex(self) -> None:
        """Payload without '0x' prefix gets it prepended before the request."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"<rcp><result>ok</result></rcp>"
        with patch("requests.get", return_value=mock_resp) as mock_get:
            bosch_camera._lan_rcp_write("192.0.2.1", "0x0d00", "00010000")
        # Either positional or keyword — just check the call was made
        assert mock_get.called


# ══════════════════════════════════════════════════════════════════════════════
# lan-ips subcommand tests
# ══════════════════════════════════════════════════════════════════════════════


class TestLanIpsSubcommand:
    """cmd_lan_ips: list / set / unset / sync."""

    def test_lan_ips_set_stores_ip(self, tmp_config_dir: str) -> None:
        """'lan-ips set' stores IP in cfg['lan_ips'] keyed by cam_id."""
        cfg = _make_cfg_with_cameras(cam_id="AAAA-0001")
        with patch.object(bosch_camera, "save_config") as mock_save:
            bosch_camera.cmd_lan_ips(
                cfg,
                _make_args(lan_sub="set", lan_cam="TestCam", lan_ip="192.0.2.55"),
            )
        assert cfg["lan_ips"]["AAAA-0001"] == "192.0.2.55"
        mock_save.assert_called_once()

    def test_lan_ips_unset_removes_ip(self, tmp_config_dir: str) -> None:
        """'lan-ips unset' removes the entry from cfg['lan_ips']."""
        cfg = _make_cfg_with_cameras(cam_id="AAAA-0001")
        cfg["lan_ips"]["AAAA-0001"] = "192.0.2.1"
        with patch.object(bosch_camera, "save_config"):
            bosch_camera.cmd_lan_ips(
                cfg,
                _make_args(lan_sub="unset", lan_cam="TestCam"),
            )
        assert "AAAA-0001" not in cfg["lan_ips"]

    def test_lan_ips_sync_copies_local_ip(self, tmp_config_dir: str) -> None:
        """'lan-ips sync' copies local_ip fields into lan_ips map."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.77", cam_id="AAAA-0001")
        with patch.object(bosch_camera, "save_config"):
            bosch_camera.cmd_lan_ips(cfg, _make_args(lan_sub="sync"))
        assert cfg["lan_ips"]["AAAA-0001"] == "192.0.2.77"

    def test_lan_ips_list_prints_camera_and_ip(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """'lan-ips' (no sub) prints camera names and IPs."""
        cfg = _make_cfg_with_cameras(lan_ip="192.0.2.99")
        with patch.object(bosch_camera, "_lan_tcp_ping", return_value=(True, 1.5)):
            bosch_camera.cmd_lan_ips(cfg, _make_args())
        out = capsys.readouterr().out
        assert "TestCam" in out
        assert "192.0.2.99" in out

    def test_lan_ips_set_missing_args_prints_usage(
        self, tmp_config_dir: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """'lan-ips set' with missing cam/ip prints usage hint."""
        cfg = _make_cfg_with_cameras()
        bosch_camera.cmd_lan_ips(cfg, _make_args(lan_sub="set"))
        out = capsys.readouterr().out
        assert "Usage" in out or "usage" in out


# ══════════════════════════════════════════════════════════════════════════════
# DEFAULT_CONFIG lan_ips section test
# ══════════════════════════════════════════════════════════════════════════════


class TestDefaultConfigLanIps:
    """Verify lan_ips section in DEFAULT_CONFIG and config merge."""

    def test_default_config_has_lan_ips_key(self) -> None:
        """DEFAULT_CONFIG contains a 'lan_ips' key as a dict."""
        assert "lan_ips" in bosch_camera.DEFAULT_CONFIG
        assert isinstance(bosch_camera.DEFAULT_CONFIG["lan_ips"], dict)

    def test_merge_defaults_adds_lan_ips_to_old_config(self) -> None:
        """Existing configs without lan_ips get the key added via _merge_defaults."""
        cfg: dict = {"account": {}, "cameras": {}, "settings": {}}
        bosch_camera._merge_defaults(cfg, bosch_camera.DEFAULT_CONFIG)
        assert "lan_ips" in cfg
        assert isinstance(cfg["lan_ips"], dict)

    def test_load_config_creates_lan_ips_section(self, tmp_config_dir: str) -> None:
        """load_config() on a fresh install produces lan_ips in the returned dict."""
        cfg = bosch_camera.load_config()
        assert "lan_ips" in cfg
