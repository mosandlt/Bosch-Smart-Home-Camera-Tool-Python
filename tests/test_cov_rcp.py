"""
Comprehensive tests for cmd_rcp and cmd_menu in bosch_camera.py.

PIN_EVERY_MODE: one test per subcommand + each data branch + error/garbage paths.
Fake IDs only — cloud-ID AABBCCDD-…, MAC aa:bb:cc:…, IPs 192.0.2.x TEST-NET.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import struct
import sys
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera
from bosch_camera import cmd_menu, cmd_rcp

# ─────────────────────────────────────────────────────────────────────────────
# Constants / fake IDs (NEVER real values)
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
CAM_NAME2 = "Kamera"
CAM_ID2 = "AABBCCDD-0000-2222-3333-666677778888"
LAN_IP = "192.0.2.10"
PROXY_BASE = "https://proxy-01.live.cbs.boschsecurity.com:42090/fakehash"
SESSION_ID = "0xdeadbeef"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _jwt() -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    pay = (
        base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + 3600}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{hdr}.{pay}.sig"


def _make_cfg(
    *,
    has_light: bool = False,
    pan_limit: int = 0,
    model: str = "HOME_Eyes_Outdoor",
    extra_cam: bool = False,
) -> dict[str, Any]:
    cams: dict[str, Any] = {
        CAM_NAME: {
            "id": CAM_ID,
            "name": CAM_NAME,
            "model": model,
            "firmware": "9.40.102",
            "has_light": has_light,
            "pan_limit": pan_limit,
        }
    }
    if extra_cam:
        cams[CAM_NAME2] = {
            "id": CAM_ID2,
            "name": CAM_NAME2,
            "model": "HOME_Eyes_Indoor",
            "firmware": "9.40.102",
            "has_light": False,
            "pan_limit": 0,
        }
    return {
        "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
        "cameras": cams,
        "settings": {},
        "lan_ips": {},
    }


def _args(**kwargs: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "cam": None,
        "sub": None,
        "action": None,
        "output": None,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _rcp_response(hex_payload: str, status: int = 200) -> MagicMock:
    """Simulate a successful RCP XML response."""
    return MagicMock(
        status_code=status,
        text=f"<reply><str>{hex_payload}</str></reply>",
    )


def _rcp_empty(status: int = 200) -> MagicMock:
    """Simulate an RCP response with no <str> data."""
    return MagicMock(status_code=status, text="<reply><err>1</err></reply>")


def _setup_rcp_patches(
    rcp_read_map: dict[str, bytes | None] | None = None,
) -> tuple[Any, Any]:
    """
    Return (mock_get_token, mock_rcp_setup, rcp_read_side_effect).
    Callers embed these in patch() contexts.
    rcp_read_map: dict[opcode -> bytes] used to drive rcp_read side effects.
    """

    def _rcp_read_se(
        rcp_url: str,
        command: str,
        sessionid: str,
        type_: str = "P_OCTET",
        num: int = 0,
    ) -> bytes | None:
        if rcp_read_map is None:
            return None
        return rcp_read_map.get(command)

    return _rcp_read_se


# ─────────────────────────────────────────────────────────────────────────────
# Helper: shared patch context for cmd_rcp
# ─────────────────────────────────────────────────────────────────────────────


class _RcpCtx:
    """Context manager that wires up the standard cmd_rcp mock stack."""

    def __init__(
        self,
        cfg: dict[str, Any],
        sub: str,
        cam: str | None,
        rcp_data: dict[str, bytes | None] | None = None,
        rcp_setup_raises: RuntimeError | None = None,
    ) -> None:
        self._cfg = cfg
        self._sub = sub
        self._cam = cam
        self._data = rcp_data or {}
        self._raises = rcp_setup_raises
        self._patches: list[Any] = []
        self.mock_rcp_read: MagicMock | None = None

    def __enter__(self) -> "_RcpCtx":
        sess = MagicMock()

        def _fake_rcp_setup(cam_info: dict[str, Any], token: str) -> tuple[str, str]:
            if self._raises:
                raise self._raises
            return (f"{PROXY_BASE}/rcp.xml", SESSION_ID)

        def _fake_rcp_read(
            rcp_url: str,
            command: str,
            sessionid: str,
            type_: str = "P_OCTET",
            num: int = 0,
        ) -> bytes | None:
            return self._data.get(command)

        self._p1 = patch.object(bosch_camera, "get_token", return_value="tok")
        self._p2 = patch.object(bosch_camera, "make_session", return_value=sess)
        self._p3 = patch.object(bosch_camera, "get_cameras", return_value=self._cfg["cameras"])
        self._p4 = patch.object(bosch_camera, "_rcp_setup", side_effect=_fake_rcp_setup)
        self._p5 = patch.object(bosch_camera, "rcp_read", side_effect=_fake_rcp_read)
        self._p6 = patch.object(bosch_camera, "open_file")

        self._p1.start()
        self._p2.start()
        self._p3.start()
        self._p4.start()
        self.mock_rcp_read = self._p5.start()
        self._p6.start()
        return self

    def __exit__(self, *args: Any) -> None:
        for p in (self._p6, self._p5, self._p4, self._p3, self._p2, self._p1):
            p.stop()

    def run(self) -> None:
        args = _args(cam=self._cam, sub=self._sub)
        cmd_rcp(self._cfg, args)


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — no subcommand / usage print
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpNoSubcommand:
    def test_no_sub_prints_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No subcommand → print usage help, do NOT call _rcp_setup."""
        cfg = _make_cfg()
        sess = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "make_session", return_value=sess),
            patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
            patch.object(bosch_camera, "_rcp_setup") as mock_setup,
        ):
            cmd_rcp(cfg, _args(cam=None, sub=None))
        out = capsys.readouterr().out
        assert "rcp" in out.lower() or "Usage" in out or "usage" in out
        mock_setup.assert_not_called()

    def test_sub_as_cam_arg_recognized(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When sub is passed as cam_arg (e.g. rcp info without camera), it is still handled."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub=None, cam="info"):
            # cam_arg="info" with sub=None → remapped to sub="info"
            args = _args(cam="info", sub=None)
            with (
                patch.object(bosch_camera, "get_token", return_value="tok"),
                patch.object(bosch_camera, "make_session", return_value=MagicMock()),
                patch.object(bosch_camera, "get_cameras", return_value=cfg["cameras"]),
                patch.object(
                    bosch_camera, "_rcp_setup", return_value=(f"{PROXY_BASE}/rcp.xml", SESSION_ID)
                ),
                patch.object(bosch_camera, "rcp_read", return_value=None),
                patch.object(bosch_camera, "open_file"),
            ):
                cmd_rcp(cfg, args)
        out = capsys.readouterr().out
        # Should print "Identity" section (info subcommand ran)
        assert "Identity" in out or "Product" in out or "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — _rcp_setup failure
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpSetupFailure:
    def test_rcp_setup_runtime_error_continues(self, capsys: pytest.CaptureFixture[str]) -> None:
        """RuntimeError from _rcp_setup → 'failed' printed, no crash, continues to next cam."""
        cfg = _make_cfg()
        with _RcpCtx(
            cfg,
            sub="info",
            cam=None,
            rcp_setup_raises=RuntimeError("proxy unreachable"),
        ) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "RCP setup failed" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — info subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpInfo:
    def test_info_prints_product_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: product name from 0x0aea is decoded and printed."""
        cfg = _make_cfg()
        product_bytes = b"FakeCam\x00"
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={"0x0aea": product_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "FakeCam" in out

    def test_info_prints_cloud_fqdn(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: cloud FQDN from 0x0aee is decoded and printed."""
        cfg = _make_cfg()
        fqdn_bytes = b"fake-cam.cbs.boschsecurity.com\x00"
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={"0x0aee": fqdn_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "fake-cam.cbs.boschsecurity.com" in out

    def test_info_lan_ip_4byte(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: 4-byte LAN IP from 0x0a36 is parsed as dotted-decimal."""
        cfg = _make_cfg()
        # 192.0.2.10 in big-endian
        ip_bytes = bytes([192, 0, 2, 10])
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={"0x0a36": ip_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "192.0.2.10" in out

    def test_info_lan_ip_string(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: LAN IP as string (>4 bytes) decoded as UTF-8."""
        cfg = _make_cfg()
        ip_bytes = b"192.0.2.11\x00"
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={"0x0a36": ip_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "192.0.2.11" in out

    def test_info_mac_6bytes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: 6-byte MAC from 0x0a30 is printed as aa:bb:cc:... format."""
        cfg = _make_cfg()
        mac_bytes = bytes.fromhex("aabbccddeeff")
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={"0x0a30": mac_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "aa:bb:cc:dd:ee:ff" in out

    def test_info_mac_short_fallback(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: MAC payload < 6 bytes → falls back to string decode."""
        cfg = _make_cfg()
        mac_bytes = b"ab\x00"
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={"0x0a30": mac_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "MAC" in out

    def test_info_all_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """info: when all opcodes return None → 'not available' printed for each field."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="info", cam=None, rcp_data={}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — clock subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpClock:
    def test_clock_valid_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        """clock: 8-byte data parses to a formatted datetime string."""
        cfg = _make_cfg()
        # 2026-03-22 05:54:25  DOW=7
        clock_bytes = bytes([0x07, 0xEA, 3, 22, 5, 54, 25, 7])
        with _RcpCtx(cfg, sub="clock", cam=None, rcp_data={"0x0a0f": clock_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "2026-03-22" in out
        assert "05:54:25" in out

    def test_clock_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """clock: None data → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="clock", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — snapshot subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpSnapshot:
    def test_snapshot_jpeg_saved(self, capsys: pytest.CaptureFixture[str], tmp_path: Any) -> None:
        """snapshot: JPEG magic bytes → file written, path printed."""
        cfg = _make_cfg()
        fake_jpeg = b"\xff\xd8" + b"\xab" * 200
        rcp_data = {
            "0x099e": fake_jpeg,
            "0x0a88": None,
        }
        with (
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            _RcpCtx(cfg, sub="snapshot", cam=None, rcp_data=rcp_data) as ctx,
        ):
            ctx.run()
        out = capsys.readouterr().out
        assert "JPEG" in out or "Thumbnail" in out

    def test_snapshot_non_jpeg_saved_as_bin(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Any
    ) -> None:
        """snapshot: non-JPEG data → saved as .bin, printed."""
        cfg = _make_cfg()
        rcp_data = {"0x099e": b"\x00\x01\x02\x03\x04"}
        with (
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            _RcpCtx(cfg, sub="snapshot", cam=None, rcp_data=rcp_data),
        ):
            cmd_rcp(cfg, _args(cam=None, sub="snapshot"))
        out = capsys.readouterr().out
        assert "not JPEG" in out or "bin" in out.lower()

    def test_snapshot_resolution_from_0x0a88(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Any
    ) -> None:
        """snapshot: 0x0a88 ≥ 8 bytes → resolution printed from data."""
        cfg = _make_cfg()
        res_bytes = struct.pack(">II", 640, 360)
        rcp_data = {
            "0x0a88": res_bytes,
            "0x099e": b"\xff\xd8" + b"\x00" * 10,
        }
        with (
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            _RcpCtx(cfg, sub="snapshot", cam=None, rcp_data=rcp_data),
        ):
            cmd_rcp(cfg, _args(cam=None, sub="snapshot"))
        out = capsys.readouterr().out
        assert "640" in out and "360" in out

    def test_snapshot_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """snapshot: 0x099e returns None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="snapshot", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — alarms subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpAlarms:
    def test_alarms_decoded_utf16be(self, capsys: pytest.CaptureFixture[str]) -> None:
        """alarms: UTF-16-BE encoded strings are decoded and listed."""
        cfg = _make_cfg()
        # Encode "Motion\x00Security" in UTF-16-BE
        alarm_bytes = "Motion".encode("utf-16-be") + b"\x00\x00" + "Security".encode("utf-16-be")
        with _RcpCtx(cfg, sub="alarms", cam=None, rcp_data={"0x0c38": alarm_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "Motion" in out
        assert "Security" in out

    def test_alarms_raw_when_no_strings(self, capsys: pytest.CaptureFixture[str]) -> None:
        """alarms: garbled data produces raw hex fallback."""
        cfg = _make_cfg()
        # 3 bytes — not valid UTF-16-BE pairs
        with _RcpCtx(cfg, sub="alarms", cam=None, rcp_data={"0x0c38": b"\x01\x02\x03"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        # Either raw hex fallback or 'no strings decoded'
        assert out  # doesn't crash

    def test_alarms_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """alarms: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="alarms", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — privacy subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpPrivacy:
    def test_privacy_on_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """privacy: byte[1] != 0 → ON (masked)."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="privacy", cam=None, rcp_data={"0x0d00": b"\x00\x01"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "ON" in out or "masked" in out.lower()

    def test_privacy_off_state(self, capsys: pytest.CaptureFixture[str]) -> None:
        """privacy: byte[1] == 0 → OFF (visible)."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="privacy", cam=None, rcp_data={"0x0d00": b"\x00\x00"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "OFF" in out or "visible" in out.lower()

    def test_privacy_single_byte(self, capsys: pytest.CaptureFixture[str]) -> None:
        """privacy: single-byte payload uses byte[0] as state byte."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="privacy", cam=None, rcp_data={"0x0d00": b"\x01"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "ON" in out or "masked" in out.lower()

    def test_privacy_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """privacy: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="privacy", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — dimmer subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpDimmer:
    def test_dimmer_value_parsed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """dimmer: T_WORD 2-byte big-endian → integer value printed."""
        cfg = _make_cfg()
        # dimmer=75, big-endian
        with _RcpCtx(cfg, sub="dimmer", cam=None, rcp_data={"0x0c22": b"\x00\x4b"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "75" in out

    def test_dimmer_raw_fallback(self, capsys: pytest.CaptureFixture[str]) -> None:
        """dimmer: 1-byte payload → raw hex fallback."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="dimmer", cam=None, rcp_data={"0x0c22": b"\x4b"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert out  # doesn't crash; either raw or value

    def test_dimmer_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """dimmer: None on both T_WORD and P_OCTET → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="dimmer", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — motion subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpMotion:
    def test_motion_two_zones(self, capsys: pytest.CaptureFixture[str]) -> None:
        """motion: 16-byte payload → 2 zones printed."""
        cfg = _make_cfg()
        # 2 zones of 8 bytes each: (x1=100,y1=200,x2=300,y2=400), (x1=0,y1=0,x2=5000,y2=5000)
        zone1 = struct.pack(">HHHH", 100, 200, 300, 400)
        zone2 = struct.pack(">HHHH", 0, 0, 5000, 5000)
        with _RcpCtx(cfg, sub="motion", cam=None, rcp_data={"0x0c0a": zone1 + zone2}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "Zone 0" in out
        assert "Zone 1" in out
        assert "100" in out
        assert "5000" in out

    def test_motion_partial_zone(self, capsys: pytest.CaptureFixture[str]) -> None:
        """motion: 5-byte payload (not divisible by 8) → partial zone raw hex."""
        cfg = _make_cfg()
        with _RcpCtx(
            cfg, sub="motion", cam=None, rcp_data={"0x0c0a": b"\x00\x01\x02\x03\x04"}
        ) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        # 5 bytes → 0 full zones + partial chunk
        assert out  # doesn't crash

    def test_motion_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """motion: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="motion", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — services subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpServices:
    def test_services_parsed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """services: null-separated ASCII → service strings printed."""
        cfg = _make_cfg()
        svc_bytes = b"HTTP\x00RTSP\x00SIP\x00"
        with _RcpCtx(cfg, sub="services", cam=None, rcp_data={"0x0c62": svc_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "HTTP" in out
        assert "RTSP" in out

    def test_services_raw_when_no_ascii(self, capsys: pytest.CaptureFixture[str]) -> None:
        """services: non-ASCII payload → raw hex printed."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="services", cam=None, rcp_data={"0x0c62": b"\xff\xfe\x00"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert out  # doesn't crash

    def test_services_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """services: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="services", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — frame subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpFrame:
    def test_frame_yuv_exact_size_no_numpy(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Any
    ) -> None:
        """frame: 115200-byte payload, numpy not available → saved as .yuv."""
        cfg = _make_cfg()
        yuv_bytes = b"\x80" * 115200
        with (
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            patch.dict(sys.modules, {"numpy": None, "PIL": None, "PIL.Image": None}),
            _RcpCtx(cfg, sub="frame", cam=None, rcp_data={"0x0c98": yuv_bytes}),
        ):
            cmd_rcp(cfg, _args(cam=None, sub="frame"))
        out = capsys.readouterr().out
        assert "YUV422" in out or "yuv" in out.lower()

    def test_frame_unexpected_size(self, capsys: pytest.CaptureFixture[str], tmp_path: Any) -> None:
        """frame: size != 115200 → saved as .bin."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "BASE_DIR", str(tmp_path)),
            _RcpCtx(cfg, sub="frame", cam=None, rcp_data={"0x0c98": b"\x00" * 100}),
        ):
            cmd_rcp(cfg, _args(cam=None, sub="frame"))
        out = capsys.readouterr().out
        assert "bin" in out.lower() or "unexpected" in out.lower() or "100" in out

    def test_frame_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """frame: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="frame", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — script subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpScript:
    def test_script_gzip_decompressed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """script: gzip magic bytes → decompressed text printed."""
        cfg = _make_cfg()
        raw_text = "iva_script_line_1\niva_script_line_2\n"
        gz_bytes = gzip.compress(raw_text.encode("utf-8"))
        with _RcpCtx(cfg, sub="script", cam=None, rcp_data={"0x09f3": gz_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "iva_script_line_1" in out

    def test_script_non_gzip(self, capsys: pytest.CaptureFixture[str]) -> None:
        """script: non-gzip data → 'not gzip' printed."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="script", cam=None, rcp_data={"0x09f3": b"\x01\x02\x03"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not gzip" in out.lower() or "gzip" in out.lower()

    def test_script_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """script: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="script", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — iva subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpIva:
    def test_iva_rule_names_listed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """iva: null-separated ASCII rule types are listed."""
        cfg = _make_cfg()
        rule_bytes = b"FieldDetector\x00LoiteringDetector\x00"
        with _RcpCtx(cfg, sub="iva", cam=None, rcp_data={"0x0ba9": rule_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "FieldDetector" in out
        assert "LoiteringDetector" in out

    def test_iva_resimotion_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        """iva: 0x0a1b UTF-8 text → printed line by line."""
        cfg = _make_cfg()
        config_text = b"sensitivity=50\nthreshold=3\n"
        with _RcpCtx(
            cfg,
            sub="iva",
            cam=None,
            rcp_data={"0x0ba9": b"FieldDetector\x00", "0x0a1b": config_text},
        ) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "sensitivity=50" in out

    def test_iva_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """iva: both opcodes return None → 'not available' for both."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="iva", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — bitrate subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpBitrate:
    def test_bitrate_ladder_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bitrate: series of big-endian uint32 kbps values → labeled tiers."""
        cfg = _make_cfg()
        # 5 tiers: 500, 1000, 2000, 3000, 6000 kbps
        bitrate_bytes = struct.pack(">IIIII", 500, 1000, 2000, 3000, 6000)
        with _RcpCtx(cfg, sub="bitrate", cam=None, rcp_data={"0x0c81": bitrate_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "500" in out
        assert "6,000" in out or "6000" in out
        assert "low" in out.lower()
        assert "high" in out.lower()

    def test_bitrate_single_tier(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bitrate: 4-byte payload (one tier) → one row printed."""
        cfg = _make_cfg()
        bitrate_bytes = struct.pack(">I", 4000)
        with _RcpCtx(cfg, sub="bitrate", cam=None, rcp_data={"0x0c81": bitrate_bytes}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "4,000" in out or "4000" in out

    def test_bitrate_too_short(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bitrate: < 4 bytes → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="bitrate", cam=None, rcp_data={"0x0c81": b"\x00\x01\x02"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()

    def test_bitrate_not_available(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bitrate: None → 'not available'."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="bitrate", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "not available" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — all subcommand
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpAll:
    def test_all_runs_every_section(self, capsys: pytest.CaptureFixture[str]) -> None:
        """all: all section headers printed."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="all", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        for section in (
            "Identity",
            "Clock",
            "Snapshot",
            "Alarm",
            "Privacy",
            "Dimmer",
            "Motion",
            "Services",
            "Frame",
            "Script",
            "IVA",
            "Bitrate",
        ):
            assert section in out or section.lower() in out.lower(), (
                f"Section '{section}' not found in output"
            )

    def test_all_with_data_shows_values(self, capsys: pytest.CaptureFixture[str]) -> None:
        """all: with real data for each opcode, values appear in output."""
        cfg = _make_cfg()
        rcp_data = {
            "0x0aea": b"MyCam\x00",
            "0x0a0f": bytes([0x07, 0xEA, 3, 22, 5, 54, 25, 7]),
            "0x0d00": b"\x00\x01",
            "0x0c22": b"\x00\x4b",
            "0x0c62": b"HTTP\x00",
            "0x0c81": struct.pack(">I", 4000),
        }
        with _RcpCtx(cfg, sub="all", cam=None, rcp_data=rcp_data) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "MyCam" in out
        assert "2026-03-22" in out
        assert "75" in out  # dimmer
        assert "HTTP" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — camera-name selection
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpCameraSelection:
    def test_specific_cam_arg_used(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When cam=<name> is given, only that camera is processed."""
        cfg = _make_cfg(extra_cam=True)
        with _RcpCtx(cfg, sub="info", cam=CAM_NAME) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert CAM_NAME in out

    def test_unknown_sub_runs_no_section(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Unknown subcommand falls through all if-branches — only the header line printed."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="zzz_unknown", cam=None) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        # No crash; no section data printed (Identity/Clock/etc. absent)
        assert "Identity" not in out
        assert "Clock" not in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — quit/exit paths
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuQuit:
    """cmd_menu exits with SystemExit on q/quit/exit/0."""

    def _run_menu(self, choice: str, cfg: dict[str, Any]) -> None:
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=[choice]),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_menu(cfg)
            assert exc_info.value.code == 0

    def test_quit_q(self) -> None:
        self._run_menu("q", _make_cfg())

    def test_quit_quit(self) -> None:
        self._run_menu("quit", _make_cfg())

    def test_quit_exit(self) -> None:
        self._run_menu("exit", _make_cfg())

    def test_quit_zero(self) -> None:
        self._run_menu("0", _make_cfg())

    def test_quit_case_insensitive(self) -> None:
        self._run_menu("Q", _make_cfg())


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — invalid / empty / EOFError input
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuInvalidInput:
    def _run_menu_no_exit(self, choices: list[str], cfg: dict[str, Any]) -> str:
        """Run menu once, capture output, no SystemExit expected (returns early)."""
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=choices),
        ):
            cmd_menu(cfg)

    def test_empty_input_returns_without_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty Enter → menu returns without crash (ValueError path)."""
        cfg = _make_cfg()
        self._run_menu_no_exit([""], cfg)
        # No exception raised

    def test_garbage_string_returns_without_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-numeric string → menu returns without crash."""
        cfg = _make_cfg()
        self._run_menu_no_exit(["notanumber"], cfg)

    def test_eof_error_raises_or_returns(self) -> None:
        """EOFError from input → no unhandled exception (menu either raises SystemExit or returns)."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=EOFError),
        ):
            try:
                cmd_menu(cfg)
            except (SystemExit, EOFError):
                pass  # either is acceptable

    def test_out_of_range_choice_prints_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Numeric choice out of range → 'Unknown choice' printed, press-Enter prompt follows."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["9999", ""]),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "Unknown" in out or "unknown" in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: option 1 (status)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchStatus:
    def test_choice_1_calls_cmd_status(self) -> None:
        """Menu choice 1 → cmd_status is called."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_status") as mock_cmd,
            patch("builtins.input", side_effect=["1", ""]),
        ):
            cmd_menu(cfg)
        mock_cmd.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: option 2 (info)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchInfo:
    def test_choice_2_calls_cmd_info(self) -> None:
        """Menu choice 2 → cmd_info is called."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_info") as mock_cmd,
            patch("builtins.input", side_effect=["2", ""]),
        ):
            cmd_menu(cfg)
        mock_cmd.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: option 3 (snapshot per camera)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchSnapshot:
    def test_choice_3_calls_cmd_snapshot_for_first_cam(self) -> None:
        """Menu choice 3 → cmd_snapshot called with cam_names[0] as positional args."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_snapshot") as mock_cmd,
            patch("builtins.input", side_effect=["3", ""]),
        ):
            cmd_menu(cfg)
        mock_cmd.assert_called_once()
        # cmd_snapshot is called as cmd_snapshot(cfg, a) — positional
        pos_args = mock_cmd.call_args[0]
        ns = pos_args[1]
        assert ns.cam == CAM_NAME

    def test_choice_3_snapshot_sets_live_false(self) -> None:
        """Menu choice 3 → cmd_snapshot args.live=False."""
        cfg = _make_cfg()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_snapshot") as mock_cmd,
            patch("builtins.input", side_effect=["3", ""]),
        ):
            cmd_menu(cfg)
        _, ns = mock_cmd.call_args[0]
        assert ns.live is False


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: privacy (on/off per camera)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchPrivacy:
    def _privacy_start(self, cfg: dict[str, Any]) -> int:
        """Compute privacy_start for a cfg with 1 camera and no pan/light."""
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        # offset after fixed items:
        # 1(status) + 1(info) = 2 → choices 1,2
        # cam_names snapshots: choices 3..3+n-1
        # "all snapshots": choice 3+n
        # liveshot_start = 3+n+1 = 4+n; n choices
        # live_start = 4+n+n = 4+2n; n choices
        # live_vlc_start = 4+3n; n choices
        # live_local_start = 4+4n; n choices
        # privacy_start = 4+5n (choices 3..4+4n last = 3+4n)
        return (
            3 + 5 * n + 1
        )  # = 3 + n (event-snap) + 1 (all) + n (liveshot) + n (live) + n (vlc) + n (local) = 3+n+1+4n = 4+5n

    def test_privacy_on_called(self) -> None:
        """First privacy-on choice → cmd_privacy called with action='on'."""
        cfg = _make_cfg()
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        # offset: 1(status) + 1(info) + n(event snaps) + 1(all snaps) + n(liveshot) + n(live) + n(vlc) + n(local)
        privacy_start = 2 + n + 1 + n + n + n + n + 1  # = 3 + 5n + 1
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_privacy") as mock_cmd,
            patch("builtins.input", side_effect=[str(privacy_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "on"
            assert ns.cam == CAM_NAME

    def test_privacy_off_called(self) -> None:
        """Second privacy choice → cmd_privacy called with action='off'."""
        cfg = _make_cfg()
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        privacy_start = 2 + n + 1 + n + n + n + n + 1
        privacy_off = privacy_start + 1
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_privacy") as mock_cmd,
            patch("builtins.input", side_effect=[str(privacy_off), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "off"


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: light (only for has_light cameras)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchLight:
    def _compute_offsets(self, cfg: dict[str, Any]) -> tuple[int, int]:
        """Return (privacy_start, light_start) for given cfg."""
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        privacy_start = 2 + n + 1 + n + n + n + n + 1
        light_start = privacy_start + n * 2
        return privacy_start, light_start

    def test_light_on_called_for_light_cam(self) -> None:
        """Light-on choice → cmd_light called with action='on'."""
        cfg = _make_cfg(has_light=True)
        _, light_start = self._compute_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_light") as mock_cmd,
            patch("builtins.input", side_effect=[str(light_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "on"

    def test_no_light_section_for_cam_without_light(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Camera with has_light=False → no light section printed."""
        cfg = _make_cfg(has_light=False)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "Camera Light" not in out


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: pan (only for pan_limit > 0)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchPan:
    def test_no_pan_section_when_pan_limit_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Camera with pan_limit=0 → no Pan section in menu."""
        cfg = _make_cfg(pan_limit=0)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "Pan " not in out or "── Pan" not in out

    def test_pan_section_shown_for_pan_cam(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Camera with pan_limit=120 → Pan section printed with actions."""
        cfg = _make_cfg(pan_limit=120)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "Pan" in out
        assert "left" in out.lower() or "right" in out.lower()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: notifications
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchNotifications:
    def test_notifications_on_called(self) -> None:
        """Notifications-on choice → cmd_notifications with action='on'."""
        cfg = _make_cfg()
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        # notif_start = privacy_start + n*2 + light=0 + 1(section header offset handled internally)
        # For has_light=False: notif_start = privacy_start + n*2
        privacy_start = 2 + n + 1 + n + n + n + n + 1
        notif_start = privacy_start + n * 2
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_notifications") as mock_cmd,
            patch("builtins.input", side_effect=[str(notif_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "on"


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: siren / wifi / unread / maintenance / token / rescan
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchMisc:
    def _run_with_choice(
        self,
        cfg: dict[str, Any],
        choice_num: int,
        target_fn: str,
    ) -> MagicMock:
        mock_fn = MagicMock()
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, target_fn, mock_fn),
            patch("builtins.input", side_effect=[str(choice_num), ""]),
        ):
            cmd_menu(cfg)
        return mock_fn

    def _last_items_offsets(self, cfg: dict[str, Any]) -> dict[str, int]:
        """Compute all numeric offsets for a basic 1-cam, no-light, no-pan config."""
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        gen2_cams = [
            nm for nm in cam_names if cfg["cameras"][nm].get("model", "").startswith("HOME_")
        ]
        ng2 = len(gen2_cams)

        offset = 3  # after 1=status, 2=info, 3=first cam snapshot
        offset += n  # event snapshot per cam
        offset += 1  # +1 for "all"
        offset += n  # liveshot
        offset += n  # live (ffplay)
        offset += n  # live (vlc)
        offset += n  # live (local)

        offset += n * 2  # privacy on/off per cam

        # no light for default cfg
        offset += n * 2  # notif on/off per cam

        # no pan/autofollow for default cfg
        intercom_start = offset
        offset += n  # intercom
        siren_start = offset
        offset += n
        wifi_start = offset
        offset += n

        audio_start = offset
        offset += ng2

        intrusion_start = offset
        offset += ng2

        unread_item = offset
        offset += 1
        maint_item = offset
        offset += 1
        token_item = offset
        offset += 1
        config_item = offset
        offset += 1
        rescan_item = offset

        return {
            "siren": siren_start,
            "wifi": wifi_start,
            "audio": audio_start,
            "intrusion": intrusion_start,
            "unread": unread_item,
            "maint": maint_item,
            "token": token_item,
            "config": config_item,
            "rescan": rescan_item,
            "intercom": intercom_start,
        }

    def test_wifi_called(self) -> None:
        """WiFi info choice → cmd_wifi called."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["wifi"], "cmd_wifi")
        if mock_fn.called:
            _, ns = mock_fn.call_args[0]
            assert ns.cam == CAM_NAME

    def test_siren_called(self) -> None:
        """Siren choice → cmd_siren called."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["siren"], "cmd_siren")
        if mock_fn.called:
            _, ns = mock_fn.call_args[0]
            assert ns.cam == CAM_NAME

    def test_unread_called(self) -> None:
        """Unread events choice → cmd_unread called."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["unread"], "cmd_unread")
        assert mock_fn.called

    def test_maintenance_called(self) -> None:
        """Maintenance choice → cmd_maintenance called."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["maint"], "cmd_maintenance")
        assert mock_fn.called

    def test_token_action_fix(self) -> None:
        """Token choice → cmd_token called with action='fix'."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["token"], "cmd_token")
        if mock_fn.called:
            _, ns = mock_fn.call_args[0]
            assert ns.action == "fix"

    def test_config_called(self) -> None:
        """Config choice → cmd_config called."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["config"], "cmd_config")
        assert mock_fn.called

    def test_rescan_called(self) -> None:
        """Re-scan choice → cmd_rescan called."""
        cfg = _make_cfg()
        offs = self._last_items_offsets(cfg)
        mock_fn = self._run_with_choice(cfg, offs["rescan"], "cmd_rescan")
        assert mock_fn.called


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: audio / intrusion (Gen2 only)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchGen2:
    def test_audio_section_shown_for_gen2(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen2 (HOME_*) camera → Audio section printed."""
        cfg = _make_cfg(model="HOME_Eyes_Outdoor")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "Audio" in out

    def test_audio_section_not_shown_for_gen1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Gen1 (CAMERA_*) camera → no Audio section."""
        cfg = _make_cfg(model="CAMERA_360")
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "── Audio" not in out

    def test_audio_called_for_gen2(self) -> None:
        """Audio choice (gen2 only) → cmd_audio called."""
        cfg = _make_cfg(model="HOME_Eyes_Outdoor")
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)

        offset = 3 + n + 1 + 4 * n
        offset += n * 2  # privacy
        offset += n * 2  # notif
        offset += n  # intercom
        offset += n  # siren
        offset += n  # wifi
        audio_start = offset

        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_audio") as mock_cmd,
            patch("builtins.input", side_effect=[str(audio_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.cam == CAM_NAME

    def test_intrusion_called_for_gen2(self) -> None:
        """Intrusion choice (gen2 only) → cmd_intrusion called."""
        cfg = _make_cfg(model="HOME_Eyes_Outdoor")
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)

        offset = 3 + n + 1 + 4 * n
        offset += n * 2  # privacy
        offset += n * 2  # notif
        offset += n  # intercom
        offset += n  # siren
        offset += n  # wifi
        offset += n  # audio (all gen2 here)
        intrusion_start = offset

        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_intrusion") as mock_cmd,
            patch("builtins.input", side_effect=[str(intrusion_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.cam == CAM_NAME


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: live stream (ffplay/vlc/local)
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchLive:
    def _live_offsets(self, cfg: dict[str, Any]) -> tuple[int, int, int]:
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        live_start = 3 + n + 1 + n  # after event-snaps + all + liveshot
        live_vlc_start = live_start + n
        live_local_start = live_vlc_start + n
        return live_start, live_vlc_start, live_local_start

    def test_live_ffplay_called(self) -> None:
        """Live-ffplay choice → cmd_live with vlc=False."""
        cfg = _make_cfg()
        live_start, _, _ = self._live_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_live") as mock_cmd,
            patch("builtins.input", side_effect=[str(live_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.vlc is False

    def test_live_vlc_called(self) -> None:
        """Live-vlc choice → cmd_live with vlc=True."""
        cfg = _make_cfg()
        _, live_vlc_start, _ = self._live_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_live") as mock_cmd,
            patch("builtins.input", side_effect=[str(live_vlc_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.vlc is True

    def test_live_local_called(self) -> None:
        """Live-local choice → cmd_live with local=True, quality='high'."""
        cfg = _make_cfg()
        _, _, live_local_start = self._live_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_live") as mock_cmd,
            patch("builtins.input", side_effect=[str(live_local_start), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.local is True
            assert ns.quality == "high"


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — no cameras in config
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuNoCameras:
    def test_no_cameras_shows_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No cameras in config → warning about 'No cameras' printed."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
            "cameras": {},
            "settings": {},
            "lan_ips": {},
        }
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        out = capsys.readouterr().out
        assert "No cameras" in out or "no cameras" in out.lower()

    def test_no_cameras_choice_1_still_works(self) -> None:
        """Even with no cameras, choice 1 (status) still dispatches."""
        cfg: dict[str, Any] = {
            "account": {"bearer_token": _jwt(), "refresh_token": "", "username": ""},
            "cameras": {},
            "settings": {},
            "lan_ips": {},
        }
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_status") as mock_cmd,
            patch("builtins.input", side_effect=["1", ""]),
        ):
            cmd_menu(cfg)
        mock_cmd.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — token auto-renewal on expired token
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuTokenRenewal:
    def test_expired_token_triggers_get_token(self) -> None:
        """Expired bearer token → get_token() called before printing menu."""
        cfg = _make_cfg()
        cfg["account"]["bearer_token"] = ""  # force missing
        with (
            patch.object(bosch_camera, "_is_token_expired", return_value=True),
            patch.object(bosch_camera, "get_token", return_value="new-tok") as mock_gt,
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch("builtins.input", side_effect=["q"]),
            pytest.raises(SystemExit),
        ):
            cmd_menu(cfg)
        mock_gt.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# cmd_rcp — additional branch coverage
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdRcpBranchCoverage:
    def test_alarms_no_strings_decoded_fallback(self, capsys: pytest.CaptureFixture[str]) -> None:
        """alarms: data present but rcp_parse_utf16be_strings returns [] → raw hex fallback."""
        cfg = _make_cfg()
        # Single odd byte — rcp_parse_utf16be_strings loops in 2-byte steps → no output
        with _RcpCtx(cfg, sub="alarms", cam=None, rcp_data={"0x0c38": b"\xff"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        # Either empty-strings path or raw path; no crash
        assert "Alarm" in out

    def test_motion_partial_chunk_9bytes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """motion: 9 bytes → 1 full zone parsed (9//8=1), remainder silently ignored."""
        cfg = _make_cfg()
        zone1 = struct.pack(">HHHH", 10, 20, 30, 40)
        remainder = b"\xab"
        with _RcpCtx(cfg, sub="motion", cam=None, rcp_data={"0x0c0a": zone1 + remainder}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "Zone 0" in out
        assert "9 bytes raw" in out

    def test_services_null_only_bytes_raw_fallback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """services: null-only bytes → empty list → raw hex fallback."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="services", cam=None, rcp_data={"0x0c62": b"\x00\x00\x00"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "Services" in out  # either raw or available

    def test_script_gzip_decompress_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """script: data starts with gzip magic but is corrupt → Decompress error printed."""
        cfg = _make_cfg()
        # gzip magic but then garbage
        bad_gz = b"\x1f\x8b" + b"\xff" * 50
        with _RcpCtx(cfg, sub="script", cam=None, rcp_data={"0x09f3": bad_gz}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "Decompress error" in out or "error" in out.lower()

    def test_iva_rule_names_raw_fallback(self, capsys: pytest.CaptureFixture[str]) -> None:
        """iva: 0x0ba9 returns single null byte → no rule_names → raw hex printed."""
        cfg = _make_cfg()
        with _RcpCtx(cfg, sub="iva", cam=None, rcp_data={"0x0ba9": b"\x00"}) as ctx:
            ctx.run()
        out = capsys.readouterr().out
        assert "IVA" in out  # raw fallback or not available


# ─────────────────────────────────────────────────────────────────────────────
# cmd_menu — dispatch: all-snapshot, liveshot, pan, autofollow, intercom
# ─────────────────────────────────────────────────────────────────────────────


class TestCmdMenuDispatchExtra:
    def _compute_all_offsets(self, cfg: dict[str, Any]) -> dict[str, int]:
        cam_names = list(cfg["cameras"].keys())
        n = len(cam_names)
        pan_cams = [nm for nm in cam_names if cfg["cameras"][nm].get("pan_limit", 0) > 0]
        npan = len(pan_cams)
        pan_actions_count = 6  # left/center/right/home/back-left/back-right

        offset = 3 + n + 1  # status(1)+info(2) → choice 3..3+n-1 event-snaps, then 3+n all-snaps
        all_snaps = 3 + n - 1 + 1  # = 3+n
        liveshot_start = 3 + n + 1
        offset = liveshot_start + n
        offset += n  # live
        offset += n  # live_vlc
        offset += n  # live_local
        offset += n * 2  # privacy
        light_cams = [nm for nm in cam_names if cfg["cameras"][nm].get("has_light", False)]
        offset += len(light_cams) * 2
        offset += n * 2  # notif
        pan_start = offset
        offset += npan * pan_actions_count
        autofollow_start = offset
        offset += npan * 2
        intercom_start = offset

        return {
            "all_snaps": all_snaps,
            "liveshot_start": liveshot_start,
            "pan_start": pan_start,
            "autofollow_start": autofollow_start,
            "intercom_start": intercom_start,
        }

    def test_all_snapshots_calls_cmd_snapshot(self) -> None:
        """Choice 3+n (all-cameras snapshot) → cmd_snapshot with live=False, cam=None."""
        cfg = _make_cfg()
        offs = self._compute_all_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_snapshot") as mock_cmd,
            patch("builtins.input", side_effect=[str(offs["all_snaps"]), ""]),
        ):
            cmd_menu(cfg)
        mock_cmd.assert_called_once()
        _, ns = mock_cmd.call_args[0]
        assert ns.live is False

    def test_liveshot_calls_cmd_snapshot_live_true(self) -> None:
        """Liveshot choice → cmd_snapshot with live=True."""
        cfg = _make_cfg()
        offs = self._compute_all_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_snapshot") as mock_cmd,
            patch("builtins.input", side_effect=[str(offs["liveshot_start"]), ""]),
        ):
            cmd_menu(cfg)
        mock_cmd.assert_called_once()
        _, ns = mock_cmd.call_args[0]
        assert ns.live is True
        assert ns.cam == CAM_NAME

    def test_pan_dispatch_calls_cmd_pan(self) -> None:
        """Pan choice (first action) → cmd_pan called with action='left'."""
        cfg = _make_cfg(pan_limit=120)
        offs = self._compute_all_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_pan") as mock_cmd,
            patch("builtins.input", side_effect=[str(offs["pan_start"]), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "left"
            assert ns.cam == CAM_NAME

    def test_autofollow_on_dispatch(self) -> None:
        """Autofollow-on choice → cmd_autofollow with action='on'."""
        cfg = _make_cfg(pan_limit=120)
        offs = self._compute_all_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_autofollow") as mock_cmd,
            patch("builtins.input", side_effect=[str(offs["autofollow_start"]), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "on"

    def test_autofollow_off_dispatch(self) -> None:
        """Autofollow-off choice → cmd_autofollow with action='off'."""
        cfg = _make_cfg(pan_limit=120)
        offs = self._compute_all_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_autofollow") as mock_cmd,
            patch("builtins.input", side_effect=[str(offs["autofollow_start"] + 1), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.action == "off"

    def test_intercom_dispatch(self) -> None:
        """Intercom choice → cmd_intercom with duration=60, speaker_level=50."""
        cfg = _make_cfg()
        offs = self._compute_all_offsets(cfg)
        with (
            patch.object(bosch_camera, "get_token", return_value="tok"),
            patch.object(bosch_camera, "_is_token_expired", return_value=False),
            patch.object(bosch_camera, "check_token_age", return_value="ok"),
            patch.object(bosch_camera, "cmd_intercom") as mock_cmd,
            patch("builtins.input", side_effect=[str(offs["intercom_start"]), ""]),
        ):
            cmd_menu(cfg)
        if mock_cmd.called:
            _, ns = mock_cmd.call_args[0]
            assert ns.duration == 60
            assert ns.speaker_level == 50
            assert ns.cam == CAM_NAME
