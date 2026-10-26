import os
import shutil
import stat
from pathlib import Path

from . import APP_ID, APP_NAME
from .i18n import t

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9F98F0"/><stop offset="1" stop-color="#534AB7"/></linearGradient></defs>
<circle cx="64" cy="64" r="44" fill="none" stroke="url(#g)" stroke-width="28"/>
<g fill="none" stroke="#fff" stroke-width="3" opacity=".9">
<path d="M64 20v28M108 64H80M64 108V80M20 64h28"/>
</g>
<circle cx="64" cy="64" r="14" fill="#1D9E75"/>
<path d="M57 64l5 5 9-10" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

QUICK = {
    "image": ("image/*;", ["png", "jpg", "webp", "pdf", "avif", "half-size"]),
    "video": ("video/*;", ["mp4", "webm", "mkv", "gif", "mp3", "compress-video"]),
    "audio": ("audio/*;", ["mp3", "wav", "flac", "ogg", "m4a"]),
    "pdf": ("application/pdf;", ["png", "jpg", "docx", "txt", "compress-pdf"]),
    "document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/msword;"
        "application/vnd.oasis.opendocument.text;application/rtf;text/rtf;text/plain;text/html;text/markdown;",
        ["pdf", "docx", "odt", "txt", "epub"],
    ),
    "sheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;application/vnd.ms-excel;"
        "application/vnd.oasis.opendocument.spreadsheet;text/csv;",
        ["pdf", "xlsx", "ods", "csv"],
    ),
    "slides": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation;application/vnd.ms-powerpoint;"
        "application/vnd.oasis.opendocument.presentation;",
        ["pdf", "pptx", "odp", "png"],
    ),
    "archive": (
        "application/zip;application/x-7z-compressed;application/x-tar;application/x-compressed-tar;"
        "application/x-xz-compressed-tar;application/x-bzip-compressed-tar;application/vnd.rar;application/x-rar;",
        ["extract", "zip", "7z", "tgz"],
    ),
}


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def launcher():
    explicit = os.environ.get("DROPRING_LAUNCHER")
    if explicit:
        return explicit
    local = Path.home() / ".local" / "bin" / "dropring"
    if local.exists():
        return str(local)
    return shutil.which("dropring") or str(local)


def _quote(path):
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _label(target):
    from . import presets

    preset = presets.find(target)
    return t(preset.name) if preset else target.upper()


def app_desktop(exe):
    return f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
GenericName={t("File converter")}
Comment={t("Drag files onto a ring of formats to convert them")}
Exec={_quote(exe)} gui --show %F
Icon={APP_ID}
Terminal=false
Categories=Utility;Graphics;AudioVideo;Office;
Keywords=convert;converter;format;image;video;pdf;
StartupNotify=false
StartupWMClass={APP_ID}
MimeType=image/png;image/jpeg;video/mp4;application/pdf;
Actions=Ring;

[Desktop Action Ring]
Name={t("Open the drop window")}
Exec={_quote(exe)} gui --show
"""


def service_ring(exe):
    return f"""[Desktop Entry]
Type=Service
MimeType=all/allfiles;
Actions=dropringRing;
X-KDE-Priority=TopLevel
X-KDE-StartupNotify=false

[Desktop Action dropringRing]
Name={t("Convert with {0}…").format(APP_NAME)}
Icon={APP_ID}
Exec={_quote(exe)} menu %F
"""


def service_quick(exe, mime, targets):
    names = ";".join(f"dropring_{x.replace('-', '_')}" for x in targets)
    lines = [
        "[Desktop Entry]",
        "Type=Service",
        f"MimeType={mime}",
        f"Actions={names};",
        f"X-KDE-Submenu={t('Convert to')}",
        "X-KDE-StartupNotify=false",
        "",
    ]
    for target in targets:
        lines += [
            f"[Desktop Action dropring_{target.replace('-', '_')}]",
            f"Name={_label(target)}",
            f"Icon={APP_ID}",
            f"Exec={_quote(exe)} menu --run {target} %F",
            "",
        ]
    return "\n".join(lines)


def nautilus_script(exe):
    return f"""#!/bin/sh
IFS='
'
exec {_quote(exe)} menu $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
"""


def targets():
    servicemenus = data_home() / "kio" / "servicemenus"
    return {
        "app": data_home() / "applications" / f"{APP_ID}.desktop",
        "icon": data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
        "ring": servicemenus / f"{APP_ID}-ring.desktop",
        "quick": [servicemenus / f"{APP_ID}-{name}.desktop" for name in QUICK],
        "nautilus": data_home() / "nautilus" / "scripts" / f"{APP_NAME}",
        "autostart": config_home() / "autostart" / f"{APP_ID}.desktop",
    }


def install(autostart=None):
    exe = launcher()
    paths = targets()
    written = [
        _write(paths["app"], app_desktop(exe)),
        _write(paths["icon"], ICON_SVG),
        _write(paths["ring"], service_ring(exe), executable=True),
    ]
    for (name, (mime, quick)), path in zip(QUICK.items(), paths["quick"]):
        written.append(_write(path, service_quick(exe, mime, quick), executable=True))
    if (data_home() / "nautilus").exists() or shutil.which("nautilus"):
        written.append(_write(paths["nautilus"], nautilus_script(exe), executable=True))
    if autostart is not None:
        set_autostart(autostart)
    _refresh()
    return written


def set_autostart(enabled):
    path = targets()["autostart"]
    if enabled:
        _write(path, app_desktop(launcher()).replace("gui --show %F", "gui --background").split("Actions=")[0] + "X-GNOME-Autostart-enabled=true\n")
    elif path.exists():
        path.unlink()


def autostart_enabled():
    return targets()["autostart"].exists()


def remove():
    paths = targets()
    for key in ("app", "icon", "ring", "nautilus", "autostart"):
        if paths[key].exists():
            paths[key].unlink()
    for path in paths["quick"]:
        if path.exists():
            path.unlink()
    _refresh()


def _refresh():
    import subprocess

    for command in (["update-desktop-database", str(data_home() / "applications")], ["kbuildsycoca6"], ["gtk-update-icon-cache", "-q", str(data_home() / "icons" / "hicolor")]):
        if shutil.which(command[0]):
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
