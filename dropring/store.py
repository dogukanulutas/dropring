import json
import os
import threading
import time
from pathlib import Path

from . import APP_ID

DEFAULT_SETTINGS = {
    "output": {"location": "same", "subfolder": "converted", "custom_folder": "", "pattern": "{name}", "conflict": "rename"},
    "parallel": max(1, min(4, (os.cpu_count() or 2) // 2)),
    "notify": True,
    "close_to_tray": True,
    "language": "auto",
    "accent": "#7F77DD",
    "donut_opacity": 0.96,
    "engine_priority": [],
    "watch": [],
    "start_minimized": False,
    "hotkey": "Meta+Shift+C",
    "hotkey_enabled": True,
}


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_ID


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{APP_ID}-{os.getuid()}"
    path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        try:
            path.replace(path.with_suffix(path.suffix + ".broken"))
        except OSError:
            pass
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _merge(defaults, values):
    result = dict(defaults)
    for key, value in (values or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class Store:
    history_limit = 300

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else config_dir()
        self._lock = threading.Lock()
        self.settings = _merge(DEFAULT_SETTINGS, _read(self.directory / "settings.json", {}))
        self.history = _read(self.directory / "history.json", [])
        if not isinstance(self.history, list):
            self.history = []

    def save_settings(self):
        with self._lock:
            _write(self.directory / "settings.json", self.settings)

    def add_history(self, entry):
        with self._lock:
            entry.setdefault("time", time.time())
            self.history.insert(0, entry)
            del self.history[self.history_limit:]
            _write(self.directory / "history.json", self.history)

    def clear_history(self):
        with self._lock:
            self.history = []
            _write(self.directory / "history.json", self.history)
