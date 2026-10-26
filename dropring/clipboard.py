import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse


def parse_uri_list(text):
    files = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("file://"):
            parsed = urlparse(line)
            if parsed.netloc not in ("", "localhost"):
                continue
            path = Path(unquote(parsed.path))
        elif line.startswith("/"):
            path = Path(line)
        else:
            continue
        if path.exists() and path not in files:
            files.append(path)
    return files


def _run(args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.decode(errors="replace") if result.returncode == 0 else None


def copied_files():
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        types = _run(["wl-paste", "--no-newline", "--list-types"]) or ""
        for kind in ("text/uri-list", "x-special/gnome-copied-files"):
            if kind in types.split():
                text = _run(["wl-paste", "--no-newline", "--type", kind]) or ""
                files = parse_uri_list(text)
                if files:
                    return files
        if "text/plain" in types or "UTF8_STRING" in types:
            files = parse_uri_list(_run(["wl-paste", "--no-newline", "--type", "text/plain"]) or "")
            if files:
                return files
    if os.environ.get("DISPLAY"):
        for tool in (["xclip", "-selection", "clipboard", "-o", "-t", "text/uri-list"], ["xsel", "--clipboard", "--output"]):
            if shutil.which(tool[0]):
                files = parse_uri_list(_run(tool) or "")
                if files:
                    return files
    return []
