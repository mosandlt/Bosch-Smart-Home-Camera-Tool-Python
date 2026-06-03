"""
Tests for main() CLI dispatcher in bosch_camera.py.

Covers: argparse setup, subcommand routing, alias handling, global --lang flag,
no-args (menu), --help/SystemExit(0), unknown command/SystemExit(1).

Fake IDs only — NEVER real device values, IPs, tokens, or secrets.
PIN_EVERY_MODE: one explicit dispatch test per registered subcommand.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import bosch_camera

# ─────────────────────────────────────────────────────────────────────────────
# Shared fake config / helpers
# ─────────────────────────────────────────────────────────────────────────────

CAM_ID = "AABBCCDD-0000-1111-2222-333344445555"
CAM_NAME = "Terrasse"
FAKE_IP = "192.0.2.1"


def _make_cfg() -> dict[str, Any]:
    return {
        "account": {"bearer_token": "tok", "refresh_token": "", "username": ""},
        "cameras": {
            CAM_NAME: {
                "id": CAM_ID,
                "name": CAM_NAME,
                "model": "HOME_Eyes_Outdoor",
                "mac": "aa:bb:cc:dd:ee:ff",
                "local_ip": FAKE_IP,
            }
        },
        "lang": "en",
    }


def _run(argv: list[str]) -> None:
    """Set sys.argv and call main()."""
    with patch.object(sys, "argv", ["bosch-camera"] + argv):
        bosch_camera.main()


def _patch_cmd(name: str) -> MagicMock:
    """Return a context-manager that patches cmd_<name> on bosch_camera."""
    return patch.object(bosch_camera, name, MagicMock())


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_cfg() -> dict[str, Any]:
    return _make_cfg()


@pytest.fixture(autouse=True)
def patch_load_config(fake_cfg: dict[str, Any]):  # type: ignore[return]
    """Prevent any disk I/O during config load."""
    with patch.object(bosch_camera, "load_config", return_value=fake_cfg):
        yield


@pytest.fixture(autouse=True)
def patch_set_lang():  # type: ignore[return]
    """Prevent i18n side-effects."""
    with patch.object(bosch_camera, "set_lang", MagicMock()):
        yield


@pytest.fixture(autouse=True)
def patch_detect_lang():  # type: ignore[return]
    with patch.object(bosch_camera, "detect_lang", return_value="en"):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# --help / SystemExit(0)
# ─────────────────────────────────────────────────────────────────────────────


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["--help"])
    assert exc.value.code == 0


def test_subcommand_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        _run(["status", "--help"])
    assert exc.value.code == 0


# ─────────────────────────────────────────────────────────────────────────────
# Unknown subcommand → argparse rejects with SystemExit(2)
# ─────────────────────────────────────────────────────────────────────────────


def test_unknown_command_exits_nonzero() -> None:
    """Argparse rejects unknown subcommands with exit code 2."""
    with pytest.raises(SystemExit) as exc:
        _run(["this-does-not-exist"])
    assert exc.value.code != 0


# ─────────────────────────────────────────────────────────────────────────────
# No-args path → cmd_menu (interactive menu)
# ─────────────────────────────────────────────────────────────────────────────


def test_no_args_calls_menu(fake_cfg: dict[str, Any]) -> None:
    """Without subcommand: main() enters the while-True menu loop once then we break it."""
    call_count = 0

    def _menu_side_effect(cfg: dict[str, Any]) -> None:
        nonlocal call_count
        call_count += 1
        raise KeyboardInterrupt  # break the infinite while-loop

    with patch.object(bosch_camera, "cmd_menu", side_effect=_menu_side_effect):
        with pytest.raises(KeyboardInterrupt):
            _run([])
    assert call_count == 1


def test_no_args_first_run_discovers(fake_cfg: dict[str, Any]) -> None:
    """Without cameras in cfg: triggers discovery before the menu."""
    fake_cfg["cameras"] = {}
    cam_mock = {"Terrasse": {"id": CAM_ID}}

    def _menu_raise(cfg: dict[str, Any]) -> None:
        raise KeyboardInterrupt

    with patch.object(bosch_camera, "get_token", return_value="tok") as m_tok, \
         patch.object(bosch_camera, "make_session", return_value=MagicMock()) as m_sess, \
         patch.object(bosch_camera, "discover_cameras", return_value=cam_mock) as m_disc, \
         patch.object(bosch_camera, "cmd_menu", side_effect=_menu_raise):
        with pytest.raises(KeyboardInterrupt):
            _run([])
    m_tok.assert_called_once()
    m_sess.assert_called_once()
    m_disc.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Global --lang flag
# ─────────────────────────────────────────────────────────────────────────────


def test_global_lang_flag_calls_set_lang() -> None:
    """--lang de must be parsed and set_lang('de') called."""
    with patch.object(bosch_camera, "set_lang") as m_lang, \
         _patch_cmd("cmd_status") as m_cmd:
        _run(["--lang", "de", "status"])
    m_lang.assert_called_once_with("de")
    m_cmd.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch tests — one per registered subcommand
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_status() -> None:
    with _patch_cmd("cmd_status") as m:
        _run(["status"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.command == "status"


def test_dispatch_info() -> None:
    with _patch_cmd("cmd_info") as m:
        _run(["info"])
    m.assert_called_once()


def test_dispatch_info_full_flag() -> None:
    with _patch_cmd("cmd_info") as m:
        _run(["info", "--full"])
    _, args = m.call_args[0]
    assert args.full is True


def test_dispatch_snapshot_no_args() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["snapshot"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.cam is None
    assert args.live is False


def test_dispatch_snapshot_with_cam() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["snapshot", "Terrasse"])
    _, args = m.call_args[0]
    assert args.cam == "Terrasse"


def test_dispatch_snapshot_live_flag() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["snapshot", "Terrasse", "--live"])
    _, args = m.call_args[0]
    assert args.live is True


def test_dispatch_snapshot_hq_flag() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["snapshot", "--hq"])
    _, args = m.call_args[0]
    assert args.hq is True


def test_dispatch_snapshot_quality_high() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["snapshot", "--quality", "high"])
    _, args = m.call_args[0]
    assert args.quality == "high"


def test_dispatch_liveshot_alias() -> None:
    """liveshot is an alias for snapshot --live."""
    with _patch_cmd("cmd_snapshot") as m:
        _run(["liveshot", "Kamera"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.live is True


def test_dispatch_livesnap_alias() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["livesnap"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.live is True


def test_dispatch_live_snapshot_alias() -> None:
    with _patch_cmd("cmd_snapshot") as m:
        _run(["live-snapshot"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.live is True


def test_dispatch_live() -> None:
    with _patch_cmd("cmd_live") as m:
        _run(["live"])
    m.assert_called_once()


def test_dispatch_live_vlc() -> None:
    with _patch_cmd("cmd_live") as m:
        _run(["live", "--vlc"])
    _, args = m.call_args[0]
    assert args.vlc is True


def test_dispatch_live_hq() -> None:
    with _patch_cmd("cmd_live") as m:
        _run(["live", "--hq"])
    _, args = m.call_args[0]
    assert args.hq is True


def test_dispatch_live_inst() -> None:
    with _patch_cmd("cmd_live") as m:
        _run(["live", "--inst", "1"])
    _, args = m.call_args[0]
    assert args.inst == 1


def test_dispatch_live_local_flag() -> None:
    with _patch_cmd("cmd_live") as m:
        _run(["live", "--local"])
    _, args = m.call_args[0]
    assert args.local is True


def test_dispatch_stream_alias() -> None:
    """stream is an alias for live."""
    with _patch_cmd("cmd_live") as m:
        _run(["stream"])
    m.assert_called_once()


def test_dispatch_test_local() -> None:
    with _patch_cmd("cmd_test_local") as m:
        _run(["test-local"])
    m.assert_called_once()


def test_dispatch_test_local_play() -> None:
    with _patch_cmd("cmd_test_local") as m:
        _run(["test-local", "--play"])
    _, args = m.call_args[0]
    assert args.play is True


def test_dispatch_ping() -> None:
    with _patch_cmd("cmd_ping") as m:
        _run(["ping"])
    m.assert_called_once()


def test_dispatch_ping_json() -> None:
    with _patch_cmd("cmd_ping") as m:
        _run(["ping", "--json"])
    _, args = m.call_args[0]
    assert args.json is True


def test_dispatch_lan_ips() -> None:
    with _patch_cmd("cmd_lan_ips") as m:
        _run(["lan-ips"])
    m.assert_called_once()


def test_dispatch_privacy() -> None:
    with _patch_cmd("cmd_privacy") as m:
        _run(["privacy"])
    m.assert_called_once()


def test_dispatch_privacy_on() -> None:
    with _patch_cmd("cmd_privacy") as m:
        _run(["privacy", "Terrasse", "on"])
    _, args = m.call_args[0]
    assert args.action == "on"


def test_dispatch_privacy_off() -> None:
    with _patch_cmd("cmd_privacy") as m:
        _run(["privacy", "Terrasse", "off"])
    _, args = m.call_args[0]
    assert args.action == "off"


def test_dispatch_privacy_minutes() -> None:
    with _patch_cmd("cmd_privacy") as m:
        _run(["privacy", "Terrasse", "on", "--minutes", "30"])
    _, args = m.call_args[0]
    assert args.minutes == 30


def test_dispatch_privacy_local_flag() -> None:
    with _patch_cmd("cmd_privacy") as m:
        _run(["privacy", "Terrasse", "on", "--local"])
    _, args = m.call_args[0]
    assert args.local is True


def test_dispatch_light() -> None:
    with _patch_cmd("cmd_light") as m:
        _run(["light"])
    m.assert_called_once()


def test_dispatch_light_on() -> None:
    with _patch_cmd("cmd_light") as m:
        _run(["light", "Terrasse", "on"])
    _, args = m.call_args[0]
    assert args.action == "on"


def test_dispatch_light_off() -> None:
    with _patch_cmd("cmd_light") as m:
        _run(["light", "Terrasse", "off"])
    _, args = m.call_args[0]
    assert args.action == "off"


def test_dispatch_light_local() -> None:
    with _patch_cmd("cmd_light") as m:
        _run(["light", "--local"])
    _, args = m.call_args[0]
    assert args.local is True


def test_dispatch_notifications() -> None:
    with _patch_cmd("cmd_notifications") as m:
        _run(["notifications"])
    m.assert_called_once()


def test_dispatch_notifications_on() -> None:
    with _patch_cmd("cmd_notifications") as m:
        _run(["notifications", "Terrasse", "on"])
    _, args = m.call_args[0]
    assert args.action == "on"


def test_dispatch_notifications_off() -> None:
    with _patch_cmd("cmd_notifications") as m:
        _run(["notifications", "Terrasse", "off"])
    _, args = m.call_args[0]
    assert args.action == "off"


def test_dispatch_pan_no_args() -> None:
    with _patch_cmd("cmd_pan") as m:
        _run(["pan"])
    m.assert_called_once()


def test_dispatch_pan_with_cam() -> None:
    with _patch_cmd("cmd_pan") as m:
        _run(["pan", "Kamera"])
    _, args = m.call_args[0]
    assert args.cam == "Kamera"


def test_dispatch_pan_preset_home() -> None:
    with _patch_cmd("cmd_pan") as m:
        _run(["pan", "Kamera", "--preset", "home"])
    _, args = m.call_args[0]
    assert args.preset == "home"


def test_dispatch_pan_preset_left() -> None:
    with _patch_cmd("cmd_pan") as m:
        _run(["pan", "Kamera", "--preset", "left"])
    _, args = m.call_args[0]
    assert args.preset == "left"


def test_dispatch_pan_preset_right() -> None:
    with _patch_cmd("cmd_pan") as m:
        _run(["pan", "Kamera", "--preset", "right"])
    _, args = m.call_args[0]
    assert args.preset == "right"


def test_dispatch_token() -> None:
    with _patch_cmd("cmd_token") as m:
        _run(["token"])
    m.assert_called_once()


def test_dispatch_token_fix() -> None:
    with _patch_cmd("cmd_token") as m:
        _run(["token", "fix"])
    _, args = m.call_args[0]
    assert args.cam == "fix"


def test_dispatch_token_browser() -> None:
    with _patch_cmd("cmd_token") as m:
        _run(["token", "browser"])
    _, args = m.call_args[0]
    assert args.cam == "browser"


def test_dispatch_config() -> None:
    with _patch_cmd("cmd_config") as m:
        _run(["config"])
    m.assert_called_once()


def test_dispatch_rcp() -> None:
    with _patch_cmd("cmd_rcp") as m:
        _run(["rcp"])
    m.assert_called_once()


def test_dispatch_rcp_info() -> None:
    with _patch_cmd("cmd_rcp") as m:
        _run(["rcp", "info"])
    _, args = m.call_args[0]
    # first positional = cam (treated as cam since cam comes first), sub=None or info
    assert args.command == "rcp"


def test_dispatch_rescan() -> None:
    with _patch_cmd("cmd_rescan") as m:
        _run(["rescan"])
    m.assert_called_once()


def test_dispatch_watch() -> None:
    with _patch_cmd("cmd_watch") as m:
        _run(["watch"])
    m.assert_called_once()


def test_dispatch_watch_interval() -> None:
    with _patch_cmd("cmd_watch") as m:
        _run(["watch", "--interval", "15"])
    _, args = m.call_args[0]
    assert args.interval == 15


def test_dispatch_watch_duration() -> None:
    with _patch_cmd("cmd_watch") as m:
        _run(["watch", "--duration", "600"])
    _, args = m.call_args[0]
    assert args.duration == 600


def test_dispatch_watch_snapshot_flag() -> None:
    with _patch_cmd("cmd_watch") as m:
        _run(["watch", "--snapshot"])
    _, args = m.call_args[0]
    assert args.snapshot is True


def test_dispatch_watch_webhook() -> None:
    with _patch_cmd("cmd_watch") as m:
        _run(["watch", "--webhook", "http://192.0.2.1/hook"])
    _, args = m.call_args[0]
    assert args.webhook == "http://192.0.2.1/hook"


def test_dispatch_nvr() -> None:
    with _patch_cmd("cmd_nvr") as m:
        _run(["nvr"])
    m.assert_called_once()


def test_dispatch_nvr_status() -> None:
    with _patch_cmd("cmd_nvr") as m:
        _run(["nvr", "status"])
    _, args = m.call_args[0]
    assert args.nvr_sub == "status"


def test_dispatch_nvr_list() -> None:
    with _patch_cmd("cmd_nvr") as m:
        _run(["nvr", "list", "--limit", "5"])
    _, args = m.call_args[0]
    assert args.nvr_sub == "list"
    assert args.limit == 5


def test_dispatch_nvr_prune() -> None:
    with _patch_cmd("cmd_nvr") as m:
        _run(["nvr", "prune", "--keep", "10"])
    _, args = m.call_args[0]
    assert args.nvr_sub == "prune"
    assert args.keep == 10


def test_dispatch_nvr_upload() -> None:
    with _patch_cmd("cmd_nvr") as m:
        _run(["nvr", "upload"])
    _, args = m.call_args[0]
    assert args.nvr_sub == "upload"


def test_dispatch_intercom() -> None:
    with _patch_cmd("cmd_intercom") as m:
        _run(["intercom"])
    m.assert_called_once()


def test_dispatch_intercom_duration() -> None:
    with _patch_cmd("cmd_intercom") as m:
        _run(["intercom", "--duration", "120"])
    _, args = m.call_args[0]
    assert args.duration == 120


def test_dispatch_maintenance() -> None:
    with _patch_cmd("cmd_maintenance") as m:
        _run(["maintenance"])
    m.assert_called_once()


def test_dispatch_maintenance_json() -> None:
    with _patch_cmd("cmd_maintenance") as m:
        _run(["maintenance", "--json"])
    _, args = m.call_args[0]
    assert args.json is True


def test_dispatch_motion() -> None:
    with _patch_cmd("cmd_motion") as m:
        _run(["motion"])
    m.assert_called_once()


def test_dispatch_motion_enable() -> None:
    with _patch_cmd("cmd_motion") as m:
        _run(["motion", "--enable"])
    _, args = m.call_args[0]
    assert args.enable is True


def test_dispatch_motion_disable() -> None:
    with _patch_cmd("cmd_motion") as m:
        _run(["motion", "--disable"])
    _, args = m.call_args[0]
    assert args.disable is True


def test_dispatch_motion_sensitivity() -> None:
    with _patch_cmd("cmd_motion") as m:
        _run(["motion", "--sensitivity", "HIGH"])
    _, args = m.call_args[0]
    assert args.sensitivity == "HIGH"


def test_dispatch_recording() -> None:
    with _patch_cmd("cmd_recording") as m:
        _run(["recording"])
    m.assert_called_once()


def test_dispatch_recording_sound_on() -> None:
    with _patch_cmd("cmd_recording") as m:
        _run(["recording", "--sound-on"])
    _, args = m.call_args[0]
    assert args.sound_on is True


def test_dispatch_recording_sound_off() -> None:
    with _patch_cmd("cmd_recording") as m:
        _run(["recording", "--sound-off"])
    _, args = m.call_args[0]
    assert args.sound_off is True


def test_dispatch_audio() -> None:
    with _patch_cmd("cmd_audio") as m:
        _run(["audio"])
    m.assert_called_once()


def test_dispatch_audio_mic() -> None:
    with _patch_cmd("cmd_audio") as m:
        _run(["audio", "--mic", "60"])
    _, args = m.call_args[0]
    assert args.mic == 60


def test_dispatch_audio_speaker() -> None:
    with _patch_cmd("cmd_audio") as m:
        _run(["audio", "--speaker", "80"])
    _, args = m.call_args[0]
    assert args.speaker == 80


def test_dispatch_audio_json() -> None:
    with _patch_cmd("cmd_audio") as m:
        _run(["audio", "--json"])
    _, args = m.call_args[0]
    assert args.json is True


def test_dispatch_intrusion() -> None:
    with _patch_cmd("cmd_intrusion") as m:
        _run(["intrusion"])
    m.assert_called_once()


def test_dispatch_intrusion_mode() -> None:
    with _patch_cmd("cmd_intrusion") as m:
        _run(["intrusion", "--mode", "outdoor"])
    _, args = m.call_args[0]
    assert args.mode == "outdoor"


def test_dispatch_intrusion_sensitivity() -> None:
    with _patch_cmd("cmd_intrusion") as m:
        _run(["intrusion", "--sensitivity", "4"])
    _, args = m.call_args[0]
    assert args.sensitivity == 4


def test_dispatch_intrusion_distance() -> None:
    with _patch_cmd("cmd_intrusion") as m:
        _run(["intrusion", "--distance", "6"])
    _, args = m.call_args[0]
    assert args.distance == 6


def test_dispatch_wifi() -> None:
    with _patch_cmd("cmd_wifi") as m:
        _run(["wifi"])
    m.assert_called_once()


def test_dispatch_wifi_json() -> None:
    with _patch_cmd("cmd_wifi") as m:
        _run(["wifi", "--json"])
    _, args = m.call_args[0]
    assert args.json is True


def test_dispatch_autofollow() -> None:
    with _patch_cmd("cmd_autofollow") as m:
        _run(["autofollow"])
    m.assert_called_once()


def test_dispatch_autofollow_on() -> None:
    # autofollow has two positionals (cam, action); "on" fills cam first
    with _patch_cmd("cmd_autofollow") as m:
        _run(["autofollow", "Kamera", "on"])
    _, args = m.call_args[0]
    assert args.action == "on"
    assert args.cam == "Kamera"


def test_dispatch_autofollow_off() -> None:
    with _patch_cmd("cmd_autofollow") as m:
        _run(["autofollow", "Kamera", "off"])
    _, args = m.call_args[0]
    assert args.action == "off"


def test_dispatch_siren() -> None:
    with _patch_cmd("cmd_siren") as m:
        _run(["siren"])
    m.assert_called_once()


def test_dispatch_siren_stop() -> None:
    with _patch_cmd("cmd_siren") as m:
        _run(["siren", "--stop"])
    _, args = m.call_args[0]
    assert args.stop is True


def test_dispatch_siren_set_duration() -> None:
    with _patch_cmd("cmd_siren") as m:
        _run(["siren", "--set-duration", "30"])
    _, args = m.call_args[0]
    assert args.set_duration == 30


def test_dispatch_unread() -> None:
    with _patch_cmd("cmd_unread") as m:
        _run(["unread"])
    m.assert_called_once()


def test_dispatch_privacy_sound() -> None:
    with _patch_cmd("cmd_privacy_sound") as m:
        _run(["privacy-sound"])
    m.assert_called_once()


def test_dispatch_privacy_sound_on() -> None:
    # privacy-sound has two positionals (cam, action); need cam+action to set action
    with _patch_cmd("cmd_privacy_sound") as m:
        _run(["privacy-sound", "Terrasse", "on"])
    _, args = m.call_args[0]
    assert args.action == "on"
    assert args.cam == "Terrasse"


def test_dispatch_privacy_sound_off() -> None:
    with _patch_cmd("cmd_privacy_sound") as m:
        _run(["privacy-sound", "Terrasse", "off"])
    _, args = m.call_args[0]
    assert args.action == "off"


def test_dispatch_rules() -> None:
    with _patch_cmd("cmd_rules") as m:
        _run(["rules"])
    m.assert_called_once()


def test_dispatch_rules_with_cam() -> None:
    with _patch_cmd("cmd_rules") as m:
        _run(["rules", "Terrasse"])
    _, args = m.call_args[0]
    assert args.cam == "Terrasse"


def test_dispatch_friends() -> None:
    with _patch_cmd("cmd_friends") as m:
        _run(["friends"])
    m.assert_called_once()


def test_dispatch_friends_invite() -> None:
    with _patch_cmd("cmd_friends") as m:
        _run(["friends", "invite", "user@example.com"])
    _, args = m.call_args[0]
    assert args.sub == "invite"
    assert args.sub_arg == "user@example.com"


def test_dispatch_accept_invite() -> None:
    with _patch_cmd("cmd_accept_invite") as m:
        _run(["accept-invite", "TOKENVALUE"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.token == "TOKENVALUE"


def test_dispatch_shared() -> None:
    with _patch_cmd("cmd_shared_with_friends") as m:
        _run(["shared"])
    m.assert_called_once()


def test_dispatch_shared_with_cam() -> None:
    with _patch_cmd("cmd_shared_with_friends") as m:
        _run(["shared", "Terrasse"])
    _, args = m.call_args[0]
    assert args.cam == "Terrasse"


# NOTE: "zones", "privacy-masks", "lighting-schedule" are present in the dispatch
# dict (bosch_camera.py lines 8961-8963) but have NO add_parser() call in main().
# Argparse rejects them with SystemExit(2) before dispatch is reached.
# REAL BUG: bosch_camera.py:8934 dispatch dict — zones/privacy-masks/lighting-schedule
# are unreachable: no subparsers.add_parser() registered for these three commands.


def test_zones_not_in_argparse() -> None:
    """zones is in dispatch dict but not registered as argparse subcommand — exits 2."""
    with pytest.raises(SystemExit) as exc:
        _run(["zones"])
    assert exc.value.code == 2


def test_privacy_masks_not_in_argparse() -> None:
    """privacy-masks is in dispatch dict but not registered — exits 2."""
    with pytest.raises(SystemExit) as exc:
        _run(["privacy-masks"])
    assert exc.value.code == 2


def test_lighting_schedule_not_in_argparse() -> None:
    """lighting-schedule is in dispatch dict but not registered — exits 2."""
    with pytest.raises(SystemExit) as exc:
        _run(["lighting-schedule"])
    assert exc.value.code == 2


def test_dispatch_rename() -> None:
    with _patch_cmd("cmd_rename") as m:
        _run(["rename", "Terrasse", "Garden Camera"])
    m.assert_called_once()
    _, args = m.call_args[0]
    assert args.cam == "Terrasse"
    assert args.new_name == "Garden Camera"


def test_dispatch_profile() -> None:
    with _patch_cmd("cmd_profile") as m:
        _run(["profile"])
    m.assert_called_once()


def test_dispatch_profile_edit_display_name() -> None:
    with _patch_cmd("cmd_profile") as m:
        _run(["profile", "edit", "--display-name", "John"])
    _, args = m.call_args[0]
    assert args.sub == "edit"
    assert args.display_name == "John"


def test_dispatch_account() -> None:
    with _patch_cmd("cmd_account") as m:
        _run(["account"])
    m.assert_called_once()


def test_dispatch_intercom_speaker_level() -> None:
    with _patch_cmd("cmd_intercom") as m:
        _run(["intercom", "--speaker-level", "80"])
    _, args = m.call_args[0]
    assert args.speaker_level == 80


def test_dispatch_timestamp() -> None:
    with _patch_cmd("cmd_timestamp") as m:
        _run(["timestamp"])
    m.assert_called_once()


def test_dispatch_timestamp_on() -> None:
    with _patch_cmd("cmd_timestamp") as m:
        _run(["timestamp", "kamera", "on"])
    _, args = m.call_args[0]
    assert args.action == "on"


def test_dispatch_timestamp_off() -> None:
    with _patch_cmd("cmd_timestamp") as m:
        _run(["timestamp", "garten", "off"])
    _, args = m.call_args[0]
    assert args.action == "off"


def test_dispatch_notification_types() -> None:
    with _patch_cmd("cmd_notification_types") as m:
        _run(["notification-types"])
    m.assert_called_once()


def test_dispatch_notification_types_set() -> None:
    with _patch_cmd("cmd_notification_types") as m:
        _run(["notification-types", "--set", "movement=on", "person=off"])
    _, args = m.call_args[0]
    assert args.set == ["movement=on", "person=off"]


def test_dispatch_snapshot_mjpeg() -> None:
    with _patch_cmd("cmd_snapshot_mjpeg") as m:
        _run(["snapshot-mjpeg"])
    m.assert_called_once()


def test_dispatch_snapshot_mjpeg_output() -> None:
    with _patch_cmd("cmd_snapshot_mjpeg") as m:
        _run(["snapshot-mjpeg", "-o", "/tmp/snap.jpg"])
    _, args = m.call_args[0]
    assert args.output == "/tmp/snap.jpg"


def test_dispatch_onvif_scopes() -> None:
    with _patch_cmd("cmd_onvif_scopes") as m:
        _run(["onvif-scopes"])
    m.assert_called_once()


def test_dispatch_onvif_scopes_json() -> None:
    with _patch_cmd("cmd_onvif_scopes") as m:
        _run(["onvif-scopes", "--json"])
    _, args = m.call_args[0]
    assert args.json is True


def test_dispatch_rcp_version() -> None:
    with _patch_cmd("cmd_rcp_version") as m:
        _run(["rcp-version"])
    m.assert_called_once()


def test_dispatch_feature_flags() -> None:
    with _patch_cmd("cmd_feature_flags") as m:
        _run(["feature-flags"])
    m.assert_called_once()


def test_dispatch_feature_flags_json() -> None:
    with _patch_cmd("cmd_feature_flags") as m:
        _run(["feature-flags", "--json"])
    _, args = m.call_args[0]
    assert args.json is True


# ─────────────────────────────────────────────────────────────────────────────
# if __name__ == "__main__" guard
# ─────────────────────────────────────────────────────────────────────────────


def test_dunder_main_guard_exists() -> None:
    """Verify the if __name__ == '__main__' guard calls main() in the module source."""
    import inspect

    src = inspect.getsource(bosch_camera)
    assert 'if __name__ == "__main__":\n    main()' in src
