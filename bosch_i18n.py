"""
bosch_i18n — Lightweight i18n for Bosch Smart Home Camera CLI Tool
=================================================================
Usage:
    from bosch_i18n import t, set_lang, detect_lang
    set_lang(detect_lang(cfg))  # call once after load_config()
    print(t("cmd.status.online", cam_name="Garten"))
"""

from __future__ import annotations

import json
import logging
import os
import warnings

_logger = logging.getLogger(__name__)

# Module-level state
_current_lang: str = "en"
_translations: dict[str, str] = {}
_CACHE: dict[str, dict[str, str]] = {}

AVAILABLE_LANGS: tuple[str, ...] = (
    "en", "de", "fr", "es", "it", "nl", "pl", "pt", "ru", "uk", "zh-Hans",
)

_TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations")


def load_translations(lang: str = "en") -> dict[str, str]:
    """Load translations for *lang* from translations/{lang}.json.

    Falls back to ``en`` if the requested language file does not exist.
    Results are cached in ``_CACHE`` to avoid repeated disk reads.

    Args:
        lang: BCP-47 language tag, e.g. ``"de"`` or ``"zh-Hans"``.

    Returns:
        Flat mapping of dot-separated keys to template strings.
    """
    if lang in _CACHE:
        return _CACHE[lang]

    path = os.path.join(_TRANSLATIONS_DIR, f"{lang}.json")
    if not os.path.exists(path):
        if lang != "en":
            _logger.debug("bosch_i18n: '%s' not found, falling back to 'en'", lang)
        path = os.path.join(_TRANSLATIONS_DIR, "en.json")

    try:
        with open(path, encoding="utf-8") as fh:
            data: dict[str, str] = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("bosch_i18n: could not load translations from %s: %s", path, exc)
        data = {}

    _CACHE[lang] = data
    return data


def detect_lang(cfg: dict) -> str:  # type: ignore[type-arg]
    """Determine the UI language from multiple sources (highest priority first).

    Priority:
        1. ``cfg["language"]`` — explicit config setting
        2. ``$BOSCH_CAMERA_LANG`` environment variable
        3. ``$LANG`` env var (first two chars, e.g. ``"de_DE.UTF-8"`` → ``"de"``)
        4. ``"en"`` — hard fallback

    The resolved language is validated against ``AVAILABLE_LANGS``.  If the
    resolved value is not recognised, ``"en"`` is returned.

    Args:
        cfg: Loaded configuration dict (from ``load_config()``).

    Returns:
        A language tag from ``AVAILABLE_LANGS``.
    """
    candidate: str | None = None

    # 1) Explicit config key
    cfg_lang = cfg.get("language") if isinstance(cfg, dict) else None
    if cfg_lang and isinstance(cfg_lang, str) and cfg_lang.strip():
        candidate = cfg_lang.strip()

    # 2) Environment variable BOSCH_CAMERA_LANG
    if candidate is None:
        env_lang = os.environ.get("BOSCH_CAMERA_LANG", "").strip()
        if env_lang:
            candidate = env_lang

    # 3) System $LANG
    if candidate is None:
        sys_lang = os.environ.get("LANG", "").strip()
        if sys_lang:
            candidate = sys_lang[:2]  # "de_DE.UTF-8" → "de"

    # 4) Hard fallback
    if candidate is None:
        return "en"

    # Validate
    if candidate in AVAILABLE_LANGS:
        return candidate

    # Try case-insensitive match (e.g. "ZH-HANS" → "zh-Hans")
    for avail in AVAILABLE_LANGS:
        if avail.lower() == candidate.lower():
            return avail

    _logger.debug("bosch_i18n: unknown language '%s', falling back to 'en'", candidate)
    return "en"


def set_lang(lang: str) -> None:
    """Set the active UI language and pre-load its translations.

    Silently falls back to ``"en"`` if *lang* is not in ``AVAILABLE_LANGS`` or
    if the corresponding JSON file does not exist on disk.

    Args:
        lang: Language tag to activate, e.g. ``"de"``.
    """
    global _current_lang, _translations
    # Validate against known languages
    resolved = lang if lang in AVAILABLE_LANGS else "en"
    # Check if the file actually exists; if not, fall back to "en"
    lang_path = os.path.join(_TRANSLATIONS_DIR, f"{resolved}.json")
    if resolved != "en" and not os.path.exists(lang_path):
        resolved = "en"
    _current_lang = resolved
    _translations = load_translations(_current_lang)


def t(msg_key: str, **kwargs: object) -> str:
    """Look up a translation key and format it with *kwargs*.

    The first positional parameter is intentionally named ``msg_key`` (not
    ``key``) so that callers can safely pass ``key=...`` as a format argument
    without triggering a "multiple values for argument" error.

    Behaviour on error:
        - Unknown key: returns ``msg_key`` itself and emits a ``warnings.warn``.
        - Missing format argument: returns the raw template string and emits a warning.
        - Never raises an exception (safe for production use).

    Args:
        msg_key: Dot-separated translation key, e.g. ``"cmd.status.online"``.
        **kwargs: Named placeholders matching ``{name}`` in the template.

    Returns:
        Formatted translation string, or ``msg_key`` on lookup failure.
    """
    # Lazily load if set_lang() was never called
    global _translations
    if not _translations:
        _translations = load_translations(_current_lang)

    template = _translations.get(msg_key)
    if template is None:
        warnings.warn(
            f"bosch_i18n: missing translation key '{msg_key}'",
            stacklevel=2,
        )
        return msg_key

    if not kwargs:
        return template

    try:
        return template.format(**kwargs)
    except (KeyError, IndexError) as exc:
        warnings.warn(
            f"bosch_i18n: format error for key '{msg_key}' ({exc})",
            stacklevel=2,
        )
        return template
