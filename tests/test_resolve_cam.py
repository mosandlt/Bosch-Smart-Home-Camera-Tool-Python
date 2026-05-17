"""
Tests for resolve_cam() — camera name resolution logic.

PIN_EVERY_MODE: each resolution path gets its own test.
"""

from __future__ import annotations

import pytest

import bosch_camera


# ── fixtures / helpers ────────────────────────────────────────────────────────

def _cfg_with_cams(**cameras: dict) -> dict:
    """Build a minimal config dict with the given camera entries."""
    return {"cameras": cameras}


CAM_GARTEN: dict = {"id": "aaa", "name": "Garten", "model": "OUTDOOR"}
CAM_KAMERA: dict = {"id": "bbb", "name": "Kamera", "model": "OUTDOOR"}
CAM_INNEN:  dict = {"id": "ccc", "name": "Innenbereich", "model": "INDOOR"}


# ── key=None paths ────────────────────────────────────────────────────────────

class TestResolveCamNoKey:
    def test_none_key_single_cam_returns_all(self) -> None:
        """key=None with 1 camera returns the full cameras dict."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN)
        result = bosch_camera.resolve_cam(cfg, None)
        assert result == {"Garten": CAM_GARTEN}

    def test_none_key_multi_cam_returns_all(self) -> None:
        """key=None with multiple cameras returns ALL cameras (no filtering)."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN, Kamera=CAM_KAMERA)
        result = bosch_camera.resolve_cam(cfg, None)
        assert set(result.keys()) == {"Garten", "Kamera"}

    def test_none_key_empty_cameras_returns_empty(self) -> None:
        """key=None with no cameras configured returns empty dict."""
        cfg: dict = {"cameras": {}}
        result = bosch_camera.resolve_cam(cfg, None)
        assert result == {}


# ── exact match ───────────────────────────────────────────────────────────────

class TestResolveCamExactMatch:
    def test_exact_match_returns_single_entry(self) -> None:
        """Exact name match returns a dict with exactly one entry."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN, Kamera=CAM_KAMERA)
        result = bosch_camera.resolve_cam(cfg, "Garten")
        assert result == {"Garten": CAM_GARTEN}

    def test_exact_match_is_case_sensitive(self) -> None:
        """Exact match is case-sensitive: 'garten' != 'Garten'."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN)
        # 'garten' is NOT an exact match → falls through to case-insensitive partial
        result = bosch_camera.resolve_cam(cfg, "garten")
        # case-insensitive partial: "garten" in "Garten".lower() → matches
        assert "Garten" in result


# ── case-insensitive partial match ────────────────────────────────────────────

class TestResolveCamCaseInsensitive:
    def test_lowercase_key_matches_title_case_name(self) -> None:
        """'garten' (lower) matches 'Garten' via case-insensitive substring search."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN)
        result = bosch_camera.resolve_cam(cfg, "garten")
        assert "Garten" in result

    def test_partial_key_matches_single_cam(self) -> None:
        """Partial key 'innen' matches 'Innenbereich' (substring match)."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN, Innenbereich=CAM_INNEN)
        result = bosch_camera.resolve_cam(cfg, "innen")
        assert "Innenbereich" in result
        assert len(result) == 1

    def test_ambiguous_partial_key_calls_sys_exit(self) -> None:
        """Partial key matching >1 camera triggers sys.exit(1)."""
        # Both 'Garten' and 'Kamera' contain 'a' — use a prefix shared by exactly 2
        cfg = _cfg_with_cams(GartenNord=CAM_GARTEN, GartenSued=CAM_KAMERA)
        with pytest.raises(SystemExit) as exc_info:
            bosch_camera.resolve_cam(cfg, "Garten")
        assert exc_info.value.code == 1


# ── no match ──────────────────────────────────────────────────────────────────

class TestResolveCamNoMatch:
    def test_unknown_name_calls_sys_exit(self) -> None:
        """Unknown camera name triggers sys.exit(1)."""
        cfg = _cfg_with_cams(Garten=CAM_GARTEN)
        with pytest.raises(SystemExit) as exc_info:
            bosch_camera.resolve_cam(cfg, "Dachkamera")
        assert exc_info.value.code == 1

    def test_empty_cameras_dict_calls_sys_exit(self) -> None:
        """Any key against an empty cameras dict triggers sys.exit(1)."""
        cfg: dict = {"cameras": {}}
        with pytest.raises(SystemExit) as exc_info:
            bosch_camera.resolve_cam(cfg, "anything")
        assert exc_info.value.code == 1

    def test_missing_cameras_key_calls_sys_exit(self) -> None:
        """Config without 'cameras' key: resolve_cam falls back to {} → sys.exit."""
        cfg: dict = {}
        with pytest.raises(SystemExit) as exc_info:
            bosch_camera.resolve_cam(cfg, "Garten")
        assert exc_info.value.code == 1
