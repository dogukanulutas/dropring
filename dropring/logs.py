import os
import sys
import threading
import time
from pathlib import Path

from . import APP_ID

_lock = threading.Lock()


def log_path():
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_ID / f"{APP_ID}.log"


def log(message):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    if os.environ.get("DROPRING_DEBUG") or sys.stderr.isatty():
        print(line, file=sys.stderr)
    path = log_path()
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(path.with_suffix(".log.1"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
