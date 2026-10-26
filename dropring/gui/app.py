import os
import signal
import sys
import traceback
from pathlib import Path

from .. import APP_ID, APP_NAME, ipc, presets
from ..i18n import set_language, t
from ..logs import log
from ..store import Store


def install_excepthook(controller):
    def hook(kind, value, tb):
        log("".join(traceback.format_exception(kind, value, tb)))
        try:
            controller.notify(t("Something went wrong"), f"{kind.__name__}: {value}", warning=True)
        except Exception:
            pass

    sys.excepthook = hook


def _prepare_environment():
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("DROPRING_X11") == "1":
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def run(background=False, menu_files=None, show=False, convert=None, clipboard=False):
    store = Store()
    set_language(store.settings.get("language", "auto"))
    request = {"cmd": "show"} if show or not background else {"cmd": "noop"}
    if menu_files:
        request = {"cmd": "menu", "files": menu_files}
    if convert:
        request = {"cmd": "convert", "preset": convert[0], "files": convert[1]}
    if clipboard:
        request = {"cmd": "clipboard"}
    if ipc.send(request):
        return 0
    log(f"start: background={background} menu={bool(menu_files)} convert={bool(convert)} clipboard={clipboard}")

    _prepare_environment()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .controller import Controller

    QApplication.setApplicationName(APP_ID)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    controller = Controller(app, store)
    install_excepthook(controller)
    app.setQuitOnLastWindowClosed(not controller.tray_available())
    signal.signal(signal.SIGINT, lambda *_: controller.quit())
    signal.signal(signal.SIGTERM, lambda *_: controller.quit())
    heartbeat = QTimer(interval=500, timeout=lambda: None)
    heartbeat.start()
    if menu_files:
        controller.open_ring([Path(f) for f in menu_files if Path(f).exists()])
    elif convert:
        preset = presets.find(convert[0])
        if preset:
            controller.start([Path(f) for f in convert[1] if Path(f).exists()], preset)
    elif clipboard:
        controller.ring_from_clipboard()
    elif not background or not controller.tray_available():
        controller.show_window()
    return app.exec()
