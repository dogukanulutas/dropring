import shutil
import subprocess

from PySide6.QtCore import SLOT, QObject, Signal, Slot
from PySide6.QtGui import QKeySequence

from .. import APP_ID, APP_NAME
from ..i18n import t

SERVICE = "org.kde.kglobalaccel"
COMPONENT = APP_ID
ACTION = "open-ring"
DEFAULT = "Meta+Shift+C"
SET_PRESENT = 2
NO_AUTOLOADING = 4
IS_DEFAULT = 1


def _key_code(sequence):
    keys = QKeySequence(sequence)
    return keys[0].toCombined() if keys.count() else 0


def _key_text(code):
    return QKeySequence(code).toString(QKeySequence.PortableText) if code else ""


class GlobalHotkey(QObject):
    triggered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = ""
        self.available = bool(shutil.which("busctl")) and self._ping()

    def _action_id(self):
        return [COMPONENT, ACTION, APP_NAME, t("Open the ring for copied files")]

    def _call(self, path, interface, method, signature, *args):
        try:
            result = subprocess.run(
                ["busctl", "--user", "call", SERVICE, path, interface, method, signature, *[str(a) for a in args]],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.split() if result.returncode == 0 else None

    def _ping(self):
        return self._call("/kglobalaccel", "org.kde.KGlobalAccel", "allMainComponents", "") is not None

    def register(self, sequence=DEFAULT):
        if not self.available:
            return ""
        action = self._action_id()
        self._call("/kglobalaccel", "org.kde.KGlobalAccel", "doRegister", "as", 4, *action)
        code = _key_code(sequence)
        self._call("/kglobalaccel", "org.kde.KGlobalAccel", "setShortcut", "asaiu", 4, *action, 1, code, SET_PRESENT | IS_DEFAULT)
        reply = self._call("/kglobalaccel", "org.kde.KGlobalAccel", "setShortcut", "asaiu", 4, *action, 1, code, SET_PRESENT)
        self.active = _key_text(int(reply[2])) if reply and len(reply) >= 3 and reply[1] != "0" else ""
        path = self._call("/kglobalaccel", "org.kde.KGlobalAccel", "getComponent", "s", COMPONENT)
        if path and len(path) >= 2:
            from PySide6.QtDBus import QDBusConnection

            QDBusConnection.sessionBus().connect(
                SERVICE, path[1].strip('"'), "org.kde.kglobalaccel.Component", "globalShortcutPressed",
                self, SLOT("_pressed(QString,QString,qlonglong)"),
            )
        return self.active

    def change(self, sequence):
        if not self.available:
            return ""
        code = _key_code(sequence)
        reply = self._call("/kglobalaccel", "org.kde.KGlobalAccel", "setShortcut", "asaiu", 4, *self._action_id(), 1, code, SET_PRESENT | NO_AUTOLOADING)
        self.active = _key_text(int(reply[2])) if reply and len(reply) >= 3 and reply[1] != "0" else ""
        return self.active

    def release(self):
        if self.available:
            self._call("/kglobalaccel", "org.kde.KGlobalAccel", "setInactive", "as", 4, *self._action_id())

    @Slot(str, str, "qlonglong")
    def _pressed(self, component, action, timestamp):
        if component == COMPONENT and action == ACTION:
            self.triggered.emit()
