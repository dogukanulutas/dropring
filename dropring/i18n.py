import json
import locale
import os
from pathlib import Path

LANGUAGES = {"auto": "System / Sistem", "en": "English", "tr": "Türkçe"}

_table = {}


def _system_language():
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            return value.split(":")[0][:2].lower()
    try:
        return (locale.getlocale()[0] or "en")[:2].lower()
    except ValueError:
        return "en"


def set_language(code="auto"):
    global _table
    code = _system_language() if code in (None, "", "auto") else code
    path = Path(__file__).with_name("locale") / f"{code}.json"
    try:
        _table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _table = {}
    return code


def t(text):
    return _table.get(text, text) if text else text


def _initial():
    try:
        from .store import config_dir

        settings = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        return settings.get("language", "auto")
    except (OSError, ValueError, ImportError):
        return "auto"


set_language(os.environ.get("DROPRING_LANG") or _initial())
