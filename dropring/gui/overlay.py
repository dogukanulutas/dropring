from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from ..logs import log
from .donut import DonutWidget


class ScreenLayer(QWidget):
    pointer = Signal(object, QPoint)
    dismissed = Signal()

    def __init__(self, session, screen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.session = session
        self.setWindowTitle("Dropring Ring")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        settings = session.settings
        self.donut = DonutWidget(self, settings.get("accent", "#7F77DD"), settings.get("donut_opacity", 0.96), settings.get("motion", True))
        self.donut.set_files(session.files, session.planner)
        self.donut.hide()
        self.donut.activated.connect(session.choose)
        self.donut.dismissed.connect(session.close)
        self.placed = False

    def place(self, pos):
        self.placed = True
        size = self.donut.size()
        x = min(max(0, pos.x() - size.width() // 2), max(0, self.width() - size.width()))
        y = min(max(0, pos.y() - size.height() // 2), max(0, self.height() - size.height()))
        self.donut.move(x, y)
        self.donut.show()
        self.donut.open_animation()
        self.raise_()
        self.activateWindow()
        self.donut.setFocus()
        self.update()
        log(f"ring placed at {pos.x()},{pos.y()} on {self.screen().name()} ({QGuiApplication.platformName()})")

    def enterEvent(self, event):
        self.pointer.emit(self, event.position().toPoint())

    def mouseMoveEvent(self, event):
        self.pointer.emit(self, event.position().toPoint())

    def mousePressEvent(self, event):
        if not self.placed:
            self.pointer.emit(self, event.position().toPoint())
            return
        self.dismissed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.dismissed.emit()
        elif self.placed:
            self.donut.keyPressEvent(event)

    def wheelEvent(self, event):
        if self.placed:
            self.donut.wheelEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange and self.placed and not self.isActiveWindow():
            QTimer.singleShot(200, self.session.close_if_inactive)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 28 if self.placed else 1))


class RingOverlay(QObject):
    chosen = Signal(list, object)
    closed = Signal()
    fallback_ms = 1500
    trust_cursor = None

    def __init__(self, files, planner, settings):
        super().__init__()
        self.files = list(files)
        self.planner = planner
        self.settings = settings
        self.layers = []
        self.active = None
        self._closed = False
        self._opened_at = None
        self._fallback = QTimer(self, singleShot=True, interval=self.fallback_ms, timeout=self._place_fallback)

    def open(self):
        screens = QGuiApplication.screens()
        cursor_known = self.trust_cursor if self.trust_cursor is not None else QGuiApplication.platformName() != "wayland"
        if cursor_known:
            screens = [QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()]
        for screen in screens:
            layer = ScreenLayer(self, screen)
            layer.pointer.connect(self._pointer)
            layer.dismissed.connect(self.close)
            self.layers.append(layer)
            layer.showFullScreen()
        log(f"ring opened on {', '.join(s.name() for s in screens)}")
        if cursor_known:
            layer = self.layers[0]
            local = layer.mapFromGlobal(QCursor.pos())
            if layer.rect().contains(local):
                self._pointer(layer, local)
                return
        self._fallback.start()

    def _pointer(self, layer, pos):
        if self.active is not None or self._closed:
            return
        self._fallback.stop()
        self.active = layer
        for other in self.layers:
            if other is not layer:
                other.close()
                other.deleteLater()
        self.layers = [layer]
        layer.place(pos)

    def _place_fallback(self):
        if self.active is not None or not self.layers:
            return
        cursor_screen = QGuiApplication.screenAt(QCursor.pos())
        layer = next((l for l in self.layers if l.screen() is cursor_screen), None)
        layer = layer or next((l for l in self.layers if l.screen() is QGuiApplication.primaryScreen()), self.layers[0])
        log("ring: no pointer event, using the screen centre")
        self._pointer(layer, QPoint(layer.width() // 2, layer.height() // 2))

    def choose(self, preset):
        files = self.files
        self.close()
        self.chosen.emit(files, preset)

    def close_if_inactive(self):
        if self.active is not None and not self.active.isActiveWindow():
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._fallback.stop()
        for layer in self.layers:
            layer.close()
            layer.deleteLater()
        self.layers = []
        self.closed.emit()

    @property
    def donut(self):
        return self.active.donut if self.active else None
