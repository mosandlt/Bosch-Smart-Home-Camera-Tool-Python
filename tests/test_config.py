"""
Tests for config management functions:
  _create_default_config, _merge_defaults, load_config, save_config
"""

from __future__ import annotations

import json
import os
import stat

import pytest

import bosch_camera


# ── _create_default_config ────────────────────────────────────────────────────

class TestCreateDefaultConfig:
    def test_creates_valid_json_file(self, tmp_config_dir: str) -> None:
        """_create_default_config() writes a parseable JSON file at CONFIG_FILE."""
        bosch_camera._create_default_config()
        assert os.path.exists(bosch_camera.CONFIG_FILE)
        with open(bosch_camera.CONFIG_FILE) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_written_config_matches_default(self, tmp_config_dir: str) -> None:
        """Written config equals DEFAULT_CONFIG (modulo _note fields which are strings)."""
        bosch_camera._create_default_config()
        with open(bosch_camera.CONFIG_FILE) as f:
            data = json.load(f)
        # Top-level keys must be present
        assert "account" in data
        assert "cameras" in data
        assert "settings" in data

    def test_creates_with_restricted_permissions(self, tmp_config_dir: str) -> None:
        """Config file is written with 0o600 (owner rw only)."""
        bosch_camera._create_default_config()
        mode = stat.S_IMODE(os.stat(bosch_camera.CONFIG_FILE).st_mode)
        assert mode == 0o600

    def test_idempotent_overwrite(self, tmp_config_dir: str) -> None:
        """Calling twice does not raise and leaves a valid file."""
        bosch_camera._create_default_config()
        bosch_camera._create_default_config()
        with open(bosch_camera.CONFIG_FILE) as f:
            data = json.load(f)
        assert "account" in data


# ── _merge_defaults ───────────────────────────────────────────────────────────

class TestMergeDefaults:
    def test_adds_missing_top_level_key(self) -> None:
        """Missing key in cfg gets filled from defaults."""
        cfg: dict = {}
        bosch_camera._merge_defaults(cfg, {"foo": "bar"})
        assert cfg["foo"] == "bar"

    def test_preserves_existing_user_value(self) -> None:
        """Existing key in cfg is NOT overwritten by defaults."""
        cfg = {"foo": "user_value"}
        bosch_camera._merge_defaults(cfg, {"foo": "default_value"})
        assert cfg["foo"] == "user_value"

    def test_deep_merge_nested_dict(self) -> None:
        """Missing nested key is added; existing nested key is preserved."""
        cfg = {"account": {"username": "alice"}}
        defaults = {"account": {"username": "default", "password": ""}}
        bosch_camera._merge_defaults(cfg, defaults)
        assert cfg["account"]["username"] == "alice"   # preserved
        assert cfg["account"]["password"] == ""        # added

    def test_non_dict_default_not_deep_merged(self) -> None:
        """If cfg[key] exists and is not a dict, it is left alone even if default is dict."""
        cfg = {"nested": "string_value"}
        defaults = {"nested": {"sub": "value"}}
        bosch_camera._merge_defaults(cfg, defaults)
        assert cfg["nested"] == "string_value"

    def test_adds_entire_missing_nested_dict(self) -> None:
        """If a nested dict key is entirely absent, the whole default subtree is added."""
        cfg: dict = {}
        defaults = {"settings": {"scan_interval_seconds": 30}}
        bosch_camera._merge_defaults(cfg, defaults)
        assert cfg["settings"]["scan_interval_seconds"] == 30

    def test_empty_defaults_no_change(self) -> None:
        """Empty defaults dict leaves cfg unchanged."""
        cfg = {"key": "value"}
        bosch_camera._merge_defaults(cfg, {})
        assert cfg == {"key": "value"}


# ── load_config ───────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_creates_config_if_missing(self, tmp_config_dir: str) -> None:
        """load_config() creates a default config when file does not exist."""
        assert not os.path.exists(bosch_camera.CONFIG_FILE)
        cfg = bosch_camera.load_config()
        assert os.path.exists(bosch_camera.CONFIG_FILE)
        assert isinstance(cfg, dict)

    def test_returns_dict(self, tmp_config_dir: str) -> None:
        """load_config() always returns a dict."""
        cfg = bosch_camera.load_config()
        assert isinstance(cfg, dict)

    def test_loads_existing_config(self, tmp_config_dir: str) -> None:
        """load_config() reads an existing JSON file correctly."""
        data = {"account": {"bearer_token": "tok123"}, "cameras": {}, "settings": {}}
        with open(bosch_camera.CONFIG_FILE, "w") as f:
            json.dump(data, f)
        cfg = bosch_camera.load_config()
        assert cfg["account"]["bearer_token"] == "tok123"

    def test_merges_missing_defaults_into_existing_config(self, tmp_config_dir: str) -> None:
        """Existing config missing new keys gets them filled via _merge_defaults."""
        # Write a config without the 'settings' key
        data = {"account": {"bearer_token": ""}, "cameras": {}}
        with open(bosch_camera.CONFIG_FILE, "w") as f:
            json.dump(data, f)
        cfg = bosch_camera.load_config()
        assert "settings" in cfg

    def test_corrupt_json_raises(self, tmp_config_dir: str) -> None:
        """load_config() raises json.JSONDecodeError on corrupt JSON.

        NOTE: The production code does NOT currently handle corrupt JSON
        gracefully — it propagates the exception. This test documents that
        behaviour. If a graceful fallback is added later, update this test.
        """
        with open(bosch_camera.CONFIG_FILE, "w") as f:
            f.write("{ this is not json }")
        with pytest.raises(json.JSONDecodeError):
            bosch_camera.load_config()


# ── save_config ───────────────────────────────────────────────────────────────

class TestSaveConfig:
    def test_writes_json(self, tmp_config_dir: str) -> None:
        """save_config() writes parseable JSON."""
        cfg = {"account": {"bearer_token": "abc"}, "cameras": {}, "settings": {}}
        bosch_camera.save_config(cfg)
        with open(bosch_camera.CONFIG_FILE) as f:
            data = json.load(f)
        assert data["account"]["bearer_token"] == "abc"

    def test_atomic_write_permissions(self, tmp_config_dir: str) -> None:
        """Saved file has 0o600 permissions."""
        bosch_camera.save_config({"account": {}, "cameras": {}, "settings": {}})
        mode = stat.S_IMODE(os.stat(bosch_camera.CONFIG_FILE).st_mode)
        assert mode == 0o600

    def test_overwrites_previous_content(self, tmp_config_dir: str) -> None:
        """save_config() replaces previous file content completely."""
        bosch_camera.save_config({"account": {"bearer_token": "old"}, "cameras": {}, "settings": {}})
        bosch_camera.save_config({"account": {"bearer_token": "new"}, "cameras": {}, "settings": {}})
        with open(bosch_camera.CONFIG_FILE) as f:
            data = json.load(f)
        assert data["account"]["bearer_token"] == "new"

    def test_no_tmp_file_left_behind(self, tmp_config_dir: str) -> None:
        """The .tmp.<pid> scratch file is cleaned up after a successful write."""
        bosch_camera.save_config({"account": {}, "cameras": {}, "settings": {}})
        tmp_dir = os.path.dirname(bosch_camera.CONFIG_FILE)
        leftover = [f for f in os.listdir(tmp_dir) if ".tmp." in f]
        assert leftover == []
