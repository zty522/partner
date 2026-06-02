"""
Internationalization (i18n) module for Partner.

Loads translations from locales/ JSON files based on the language
configured in ~/.partner/config.json (key: "language", values: "en"/"zh").
Provides lang() and t(key) functions for easy translation.
"""

import json
import os
from pathlib import Path

_CACHE = None
_LANG = None

_LOCALES_DIR = Path(__file__).parent / "locales"
_CONFIG_PATH = Path.home() / ".partner" / "config.json"

_SUPPORTED_LANGUAGES = {"en", "zh"}
_DEFAULT_LANGUAGE = "en"


def _load_config() -> str:
    """Read language from config file. Returns default if file missing or invalid."""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            lang = config.get("language", _DEFAULT_LANGUAGE)
            if lang in _SUPPORTED_LANGUAGES:
                return lang
    except (json.JSONDecodeError, OSError):
        pass
    return _DEFAULT_LANGUAGE


def _load_translations(language: str) -> dict:
    """Load translations JSON for the given language code."""
    locale_file = _LOCALES_DIR / f"{language}.json"
    try:
        if locale_file.exists():
            with open(locale_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _init():
    """Initialize the i18n module: detect language and load translations."""
    global _LANG, _CACHE
    if _LANG is None:
        _LANG = _load_config()
        _CACHE = _load_translations(_LANG)


def reload():
    """Force re-initialization: re-read language from config and reload translations."""
    global _LANG, _CACHE
    _LANG = None
    _CACHE = None
    _init()


def set_lang(language: str):
    """Persist language choice and reload translations."""
    if language not in _SUPPORTED_LANGUAGES:
        language = _DEFAULT_LANGUAGE
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if _CONFIG_PATH.exists():
            try:
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, OSError):
                data = {}
        data["language"] = language
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass
    reload()


def lang() -> str:
    """Return the current language code ('en' or 'zh').

    Detects language from ~/.partner/config.json on first call.
    Falls back to 'en' if config is missing or invalid.
    """
    _init()
    return _LANG


def t(key: str, **kwargs) -> str:
    """Translate a key using the loaded locale JSON.

    Args:
        key: Dot-separated translation key (e.g. 'startup.welcome').
        **kwargs: Format arguments for string interpolation.

    Returns:
        Translated string, or the key itself if no translation is found.
    """
    _init()
    template = _CACHE.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template
