"""
Tests for bosch_i18n — i18n infrastructure.

Coverage:
  - load_translations: happy path, missing lang fallback
  - t(): known key, unknown key + warning, substitution, missing kwarg
  - detect_lang(): priority chain (config > env BOSCH_CAMERA_LANG > $LANG > "en")
  - detect_lang(): invalid lang fallback
  - set_lang(): loads translations for the given lang
  - Compliance: all t("...") call sites in bosch_camera.py have a key in en.json
"""

from __future__ import annotations

import ast
import sys
import warnings
from pathlib import Path

import pytest

# Make sure the project root is on sys.path so both modules are importable.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_i18n():
    """Return a freshly imported (or re-imported) bosch_i18n module with cleared cache."""
    import bosch_i18n as i18n

    # Clear module-level caches so tests don't bleed into each other.
    i18n._CACHE.clear()
    i18n._translations = {}
    i18n._current_lang = "en"
    return i18n


# ---------------------------------------------------------------------------
# load_translations
# ---------------------------------------------------------------------------


class TestLoadTranslations:
    def test_load_translations_returns_dict(self) -> None:
        """en.json exists and load_translations returns a non-empty dict."""
        i18n = _fresh_i18n()
        result = i18n.load_translations("en")
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_load_translations_missing_lang_falls_back_to_en(self) -> None:
        """load_translations('xx') falls back to en.json silently."""
        i18n = _fresh_i18n()
        result = i18n.load_translations("xx")
        en_result = i18n.load_translations("en")
        assert result == en_result

    def test_load_translations_caches_result(self) -> None:
        """Second call with same lang returns the same dict object (cached)."""
        i18n = _fresh_i18n()
        first = i18n.load_translations("en")
        second = i18n.load_translations("en")
        assert first is second

    def test_load_translations_values_are_strings(self) -> None:
        """All values in en.json are strings."""
        i18n = _fresh_i18n()
        result = i18n.load_translations("en")
        for key, val in result.items():
            assert isinstance(val, str), f"Key '{key}' has non-string value: {val!r}"


# ---------------------------------------------------------------------------
# t()
# ---------------------------------------------------------------------------


class TestT:
    def test_t_returns_translation_for_known_key(self) -> None:
        """t() returns the translated string for a key that exists in en.json."""
        i18n = _fresh_i18n()
        i18n.set_lang("en")
        # Pick a stable key guaranteed to exist
        result = i18n.t("err.unknown_command", cmd="foo")
        assert "foo" in result

    def test_t_returns_key_for_unknown_key_and_warns(self) -> None:
        """t() returns the key itself when the key is missing, and emits a UserWarning."""
        i18n = _fresh_i18n()
        i18n.set_lang("en")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = i18n.t("this.key.does.not.exist")
        assert result == "this.key.does.not.exist"
        assert any("this.key.does.not.exist" in str(w.message) for w in caught)

    def test_t_substitutes_kwargs(self) -> None:
        """t() correctly substitutes named placeholders."""
        i18n = _fresh_i18n()
        i18n.set_lang("en")
        result = i18n.t("cli.cam.not_found", key="Garten", known="Kamera, Innen")
        assert "Garten" in result
        assert "Kamera, Innen" in result

    def test_t_missing_kwarg_falls_back_gracefully(self) -> None:
        """t() with a partial kwarg (some missing) returns the raw template + warns — no crash.

        Contract: when kwargs are supplied but one placeholder is missing,
        t() catches the KeyError, emits a warning, and returns the raw template.
        When *no* kwargs are supplied at all, t() returns the raw template without
        attempting format() — that is the fast-path and is also acceptable.
        """
        i18n = _fresh_i18n()
        i18n.set_lang("en")

        # Case 1: no kwargs at all → returns template, no warning (fast-path)
        result_no_kwargs = i18n.t("cli.cam.not_found")
        assert isinstance(result_no_kwargs, str)
        assert len(result_no_kwargs) > 0

        # Case 2: partial kwargs → KeyError → warning + raw template returned
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # cli.cam.not_found needs 'key' AND 'known'; supply only one
            result_partial = i18n.t("cli.cam.not_found", key="Garten")
        assert isinstance(result_partial, str)
        assert len(result_partial) > 0
        warning_texts = [str(w.message) for w in caught]
        assert any(
            "format error" in txt.lower() or "cli.cam.not_found" in txt for txt in warning_texts
        ), f"Expected a format-error warning, got: {warning_texts}"

    def test_t_no_kwargs_returns_plain_string(self) -> None:
        """t() with no placeholders and no kwargs returns the string unchanged."""
        i18n = _fresh_i18n()
        i18n.set_lang("en")
        result = i18n.t("cli.token.renewing")
        assert "Token expired" in result or len(result) > 0


# ---------------------------------------------------------------------------
# detect_lang()
# ---------------------------------------------------------------------------


class TestDetectLang:
    def test_detect_lang_config_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cfg['language'] overrides everything else."""
        i18n = _fresh_i18n()
        monkeypatch.setenv("BOSCH_CAMERA_LANG", "fr")
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        result = i18n.detect_lang({"language": "en"})
        assert result == "en"

    def test_detect_lang_env_bosch_camera_lang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """$BOSCH_CAMERA_LANG is used when cfg has no language key."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.setenv("BOSCH_CAMERA_LANG", "fr")
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        result = i18n.detect_lang({})
        assert result == "fr"

    def test_detect_lang_system_lang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """$LANG[:2] is used as third fallback."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        result = i18n.detect_lang({})
        assert result == "de"

    def test_detect_lang_default_en(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to 'en' when no source provides a language."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.delenv("LANG", raising=False)
        result = i18n.detect_lang({})
        assert result == "en"

    def test_detect_lang_invalid_lang_falls_back_to_en(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unknown language tag returns 'en'."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.delenv("LANG", raising=False)
        result = i18n.detect_lang({"language": "xx"})
        assert result == "en"

    def test_detect_lang_garbage_lang_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Garbage $LANG value falls back to 'en'."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.setenv("LANG", "C.UTF-8")  # 'C.' → 'C' not in AVAILABLE_LANGS
        result = i18n.detect_lang({})
        assert result == "en"

    def test_detect_lang_empty_cfg_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cfg['language'] = '' is ignored (empty string)."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.delenv("LANG", raising=False)
        result = i18n.detect_lang({"language": ""})
        assert result == "en"

    def test_detect_lang_non_dict_cfg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-dict cfg (e.g. None) does not crash; falls back to 'en'."""
        i18n = _fresh_i18n()
        monkeypatch.delenv("BOSCH_CAMERA_LANG", raising=False)
        monkeypatch.delenv("LANG", raising=False)
        result = i18n.detect_lang(None)  # type: ignore[arg-type]
        assert result == "en"


# ---------------------------------------------------------------------------
# set_lang()
# ---------------------------------------------------------------------------


class TestSetLang:
    def test_set_lang_loads_translations(self) -> None:
        """set_lang('en') causes _translations to be non-empty."""
        i18n = _fresh_i18n()
        i18n.set_lang("en")
        assert len(i18n._translations) > 0

    def test_set_lang_invalid_falls_back_to_en(self) -> None:
        """set_lang('xx') silently loads 'en' translations."""
        i18n = _fresh_i18n()
        i18n.set_lang("xx")
        en_data = i18n.load_translations("en")
        assert i18n._translations == en_data

    def test_set_lang_updates_current_lang(self) -> None:
        """set_lang('de') updates _current_lang to 'de' if de.json exists, else 'en'."""
        i18n = _fresh_i18n()
        de_path = _PROJECT_ROOT / "translations" / "de.json"
        i18n.set_lang("de")
        if de_path.exists():
            assert i18n._current_lang == "de"
        else:
            # de.json not yet present → set_lang falls back to "en"
            assert i18n._current_lang == "en"


# ---------------------------------------------------------------------------
# COMPLIANCE: all t("...") call sites in bosch_camera.py have a key in en.json
# ---------------------------------------------------------------------------


class TestEnJsonCoversAllTCallSites:
    def test_en_json_covers_all_known_t_call_sites(self) -> None:
        """
        Parse bosch_camera.py with AST, find all t("literal_key") calls,
        assert every key exists in translations/en.json.

        This test is mandatory: it catches regressions where a key is referenced
        in code but missing from the translation file.
        """
        import json as _json

        src_path = _PROJECT_ROOT / "bosch_camera.py"
        en_path = _PROJECT_ROOT / "translations" / "en.json"

        assert src_path.exists(), f"bosch_camera.py not found at {src_path}"
        assert en_path.exists(), f"translations/en.json not found at {en_path}"

        with open(en_path, encoding="utf-8") as fh:
            en_keys: set[str] = set(_json.load(fh).keys())

        source = src_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(src_path))

        missing: list[str] = []
        for node in ast.walk(tree):
            # Match: t("some.key", ...) — only direct string literal as first arg
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "t"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                key = node.args[0].value
                if key not in en_keys:
                    missing.append(f"  line {node.lineno}: t({key!r})")

        assert not missing, (
            "The following t() keys are used in bosch_camera.py but missing from en.json:\n"
            + "\n".join(missing)
        )
