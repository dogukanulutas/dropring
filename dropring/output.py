import os
import re
from datetime import datetime
from pathlib import Path

from . import formats

SAME = "same"
SUBFOLDER = "subfolder"
CUSTOM = "custom"

RENAME = "rename"
OVERWRITE = "overwrite"
SKIP = "skip"

_INVALID = set('<>:"/\\|?*')
_TOKEN = re.compile(r"\{(name|ext|preset|date|time|index)\}", re.I)


def sanitize(name, fallback):
    cleaned = "".join("_" if c in _INVALID or ord(c) < 32 else c for c in name).strip().rstrip(".")
    return cleaned or fallback


def expand(pattern, source, preset_name="", index=1, now=None):
    now = now or datetime.now()
    pattern = pattern or "{name}"
    values = {
        "name": formats.name_without_extension(source),
        "ext": formats.extension_of(source),
        "preset": preset_name,
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
        "index": str(index),
    }
    return _TOKEN.sub(lambda m: values[m.group(1).lower()], pattern)


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def output_dir(rule, source):
    source_dir = Path(rule.get("source_folder") or Path(source).resolve().parent)
    location = rule.get("location", SAME)
    if location == SUBFOLDER:
        return source_dir / sanitize(expand(rule.get("subfolder", "converted"), source), "converted")
    if location == CUSTOM and rule.get("custom_folder"):
        return Path(os.path.expanduser(rule["custom_folder"]))
    return source_dir


def resolve(rule, source, extension, exists, suffix="", preset_name="", index=1, folder=None):
    directory = Path(folder) if folder else output_dir(rule, source)
    base = sanitize(expand(rule.get("pattern", "{name}"), source, preset_name, index), formats.name_without_extension(source)) + suffix
    ext = extension.lstrip(".")

    def path_for(name):
        return directory / (f"{name}.{ext}" if ext else name)

    candidate = path_for(base)
    is_source = same_path(candidate, source)
    if not is_source and not exists(candidate):
        return candidate
    conflict = rule.get("conflict", RENAME)
    if not is_source and conflict == OVERWRITE:
        return candidate
    if not is_source and conflict == SKIP:
        return None
    n = 2
    while True:
        candidate = path_for(f"{base} ({n})")
        if not exists(candidate) and not same_path(candidate, source):
            return candidate
        n += 1
