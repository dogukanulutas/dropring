import json
import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .. import APP_ID, APP_NAME, integration, ipc, presets
from .. import clipboard as copied
from ..i18n import t
from ..jobs import FAILED, JobQueue
from ..logs import log
from ..watch import WatchManager
from .hotkey import GlobalHotkey
from .main_window import MainWindow
from .overlay import RingOverlay
from .widgets import reveal


class Bridge(QObject):
    updated = Signal(object)
    log = Signal(str)

class Controller(QObject):
    def __init__(self, app, store):
        super().__init__()
        self.app = app
        self.store = store
        self.icon = self._icon()
        app.setWindowIcon(self.icon)
        self.bridge = Bridge()
        self.bridge.updated.connect(self._job_updated, Qt.QueuedConnection)
        self.bridge.log.connect(self._log, Qt.QueuedConnection)
        self.queue = JobQueue(store, on_update=self.bridge.updated.emit)
        self.watch = WatchManager(self.queue, on_log=self.bridge.log.emit)
        self.window = MainWindow(self)
        self.window.quit_requested.connect(self.quit)
        self.overlays = []
        self.last_job = None
        self.tray = None
        self._known = set()
        self._setup_tray()
        self._setup_server()
        self.hotkey = GlobalHotkey(self)
        self.hotkey.triggered.connect(self.ring_from_clipboard)
        self.apply_hotkey()
        self.apply_watch()

    def _icon(self):
        icon = QIcon.fromTheme(APP_ID)
        if icon.isNull():
            pixmap = QPixmap()
            pixmap.loadFromData(integration.ICON_SVG.encode(), "SVG")
            icon = QIcon(pixmap)
        return icon

    def tray_available(self):
        return self.tray is not None

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction(QIcon.fromTheme("window"), t("Open {0}").format(APP_NAME), self.show_window)
        menu.addAction(QIcon.fromTheme("document-open"), t("Convert files…"), self._browse)
        menu.addSeparator()
        self.pause_action = QAction(QIcon.fromTheme("media-playback-pause"), t("Pause watch folders"), menu, checkable=True)
        self.pause_action.toggled.connect(lambda paused: self.watch.stop() if paused else self.apply_watch())
        menu.addAction(self.pause_action)
        menu.addSeparator()
        menu.addAction(QIcon.fromTheme("application-exit"), t("Quit"), self.quit)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self._message_clicked)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.window.isVisible() and self.window.isActiveWindow():
                self.window.hide()
            else:
                self.show_window()

    def _message_clicked(self):
        if self.last_job and self.last_job.outputs:
            reveal(self.last_job.outputs[0])

    def _browse(self):
        self.show_window()
        self.window.browse()

    def _setup_server(self):
        path = str(ipc.socket_path())
        QLocalServer.removeServer(path)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(path):
            self._log(f"IPC: {self.server.errorString()}")
        self.server.newConnection.connect(self._connection)

    def _connection(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))

    def _read(self, socket):
        if not socket.canReadLine():
            return
        line = bytes(socket.readLine()).decode(errors="replace")
        socket.write(b"ok\n")
        socket.flush()
        socket.disconnectFromServer()
        try:
            self.handle(json.loads(line))
        except (ValueError, KeyError, TypeError) as error:
            self._log(f"IPC: {error}")

    def handle(self, message):
        command = message.get("cmd")
        files = [Path(f) for f in message.get("files", []) if Path(f).exists()]
        if command == "show":
            self.show_window()
        elif command == "quit":
            QTimer.singleShot(0, self.quit)
        elif command == "clipboard":
            self.ring_from_clipboard()
        elif command == "menu" and files:
            self.open_ring(files)
        elif command == "convert" and files:
            preset = presets.find(message.get("preset", ""))
            if preset:
                self.start(files, preset)

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def apply_hotkey(self):
        settings = self.store.settings
        if settings.get("hotkey_enabled", True):
            self.hotkey.register(settings.get("hotkey") or "Meta+Shift+C")
        else:
            self.hotkey.release()
            self.hotkey.active = ""
        return self.hotkey.active

    def change_hotkey(self, sequence):
        active = self.hotkey.change(sequence)
        if active:
            self.store.settings["hotkey"] = active
            self.store.save_settings()
        return active

    def ring_from_clipboard(self):
        files = copied.copied_files()
        if not files:
            urls = QGuiApplication.clipboard().mimeData().urls()
            files = [Path(u.toLocalFile()) for u in urls if u.isLocalFile() and Path(u.toLocalFile()).exists()]
        if files:
            self.open_ring(files)
            return
        log("clipboard: no copied files")
        shortcut = self.hotkey.active or t("the shortcut")
        self.notify(t("No copied files"), t("Select files in the file manager, press Ctrl+C, then press {0}.").format(shortcut), warning=True)
        self.show_window()

    def open_ring(self, files):
        log(f"ring for {len(files)} file(s): {', '.join(f.name for f in files[:3])}")
        overlay = RingOverlay(files, self.queue.planner, self.store.settings)
        overlay.chosen.connect(self.start)
        overlay.closed.connect(lambda o=overlay: self.overlays.remove(o) if o in self.overlays else None)
        self.overlays.append(overlay)
        overlay.open()

    def start(self, files, preset):
        files = [Path(f) for f in files]
        log(f"start {preset.id} for {len(files)} file(s)")
        ok, reason = presets.availability(preset, files, self.queue.planner)
        if not ok:
            self.notify(t(preset.name), t(reason), warning=True)
            return None
        job = self.queue.submit(files, preset)
        self._known.add(job.id)
        self.window.add_job(job)
        return job

    def _job_updated(self, job):
        if job.id not in self._known:
            self._known.add(job.id)
            self.window.add_job(job)
        self.window.update_job(job)
        if job.done and not getattr(job, "_announced", False):
            job._announced = True
            log(f"done {job.preset.id}: " + "; ".join(f"{f.source.name} {f.status} {f.error}".strip() for f in job.files)
                + (f" -> {', '.join(Path(o).name for o in job.outputs)}" if job.outputs else ""))
            self.last_job = job
            self._announce(job)

    def _announce(self, job):
        if not self.store.settings.get("notify", True):
            return
        failed = [f for f in job.files if f.status == FAILED]
        outputs = job.outputs
        if failed and not outputs:
            self.notify(t("Conversion failed"), f"{failed[0].source.name}: {failed[0].error}", warning=True)
        elif outputs:
            title = t("{0} → {1}").format(
                job.files[0].source.name if len(job.files) == 1 else t("{0} files").format(len(job.files)), t(job.preset.name)
            )
            body = Path(outputs[0]).name + (t(" and {0} more").format(len(outputs) - 1) if len(outputs) > 1 else "")
            if failed:
                body += "\n" + t("{0} failed").format(len(failed))
            self.notify(title, body)

    def notify(self, title, body, warning=False):
        if self.tray:
            self.tray.showMessage(title, body, QSystemTrayIcon.Warning if warning else QSystemTrayIcon.Information, 5000)
        elif shutil.which("notify-send"):
            threading.Thread(
                target=subprocess.run, args=(["notify-send", "-a", APP_NAME, "-i", APP_ID, title, body],),
                kwargs={"timeout": 10, "check": False}, daemon=True,
            ).start()

    def _log(self, message):
        log(message)

    def apply_watch(self):
        if self.tray and self.pause_action.isChecked():
            return
        self.watch.apply(self.store.settings.get("watch", []))

    def settings_changed(self):
        workers = self.store.settings.get("parallel", 2)
        self.queue.pool._max_workers = workers
        self.window.apply_style()

    def quit(self):
        self.hotkey.release()
        self.watch.stop()
        self.queue.shutdown()
        self.server.close()
        QLocalServer.removeServer(str(ipc.socket_path()))
        self.app.quit()
