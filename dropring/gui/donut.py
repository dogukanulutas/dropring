import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from .. import presets
from ..i18n import t

OUTER = 150.0
INNER = 60.0
MARGIN = 26.0
GAP = 1.6


def slice_span(count):
    return 360.0 / max(1, count)


def slice_at(dx, dy, count, inner=INNER, outer=OUTER):
    distance = math.hypot(dx, dy)
    if count == 0 or distance < inner or distance > outer * 1.18:
        return -1
    angle = (math.degrees(math.atan2(dy, dx)) + 90.0) % 360.0
    return min(count - 1, int(angle // slice_span(count)))


def mix(a, b, amount):
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
        round(a.alpha() + (b.alpha() - a.alpha()) * amount),
    )


def ease(x):
    return 1 - (1 - x) ** 3


class Slice:
    def __init__(self, preset, ok, reason):
        self.preset = preset
        self.ok = ok
        self.reason = reason
        self.hover = 0.0


class DonutWidget(QWidget):
    activated = Signal(object)
    dismissed = Signal()

    def __init__(self, parent=None, accent="#7F77DD", opacity=0.96, motion=True):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        size = int(2 * (OUTER + MARGIN))
        self.setFixedSize(size, size)
        self.accent = QColor(accent)
        self.opacity = opacity
        self.motion = motion
        self.faces = [[], []]
        self.face = 0
        self.profile = None
        self.files = []
        self.hovered = -1
        self.keyboard = False
        self.drag_mode = False
        self._open_started = time.monotonic()
        self._flip_started = None
        self._flip_swapped = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    @property
    def slices(self):
        return self.faces[self.face]

    def center(self):
        return QPointF(self.width() / 2, self.height() / 2)

    def set_files(self, paths, planner):
        self.files = list(paths)
        self.profile = presets.select_profile(self.files)
        self.faces = []
        for ids in (self.profile.front, self.profile.back):
            face = []
            for pid in ids:
                preset = presets.BY_ID.get(pid)
                if preset is None:
                    continue
                ok, reason = presets.availability(preset, self.files, planner)
                face.append(Slice(preset, ok, reason))
            self.faces.append(face)
        self.face = 0
        if not self._has_enabled(0) and self._has_enabled(1):
            self.face = 1
        self.hovered = -1
        self.open_animation()

    def _has_enabled(self, face):
        return any(s.ok for s in self.faces[face])

    def can_flip(self):
        return self._has_enabled(1 - self.face)

    def open_animation(self):
        self._open_started = time.monotonic()
        self._start()

    def _start(self):
        if not self._timer.isActive():
            self._timer.start()

    def flip(self):
        if not self.can_flip() or self._flip_started is not None:
            return
        if not self.motion:
            self.face = 1 - self.face
            self.hovered = -1
            self.update()
            return
        self._flip_started = time.monotonic()
        self._flip_swapped = False
        self._start()

    def _flip_progress(self):
        if self._flip_started is None:
            return None
        return min(1.0, (time.monotonic() - self._flip_started) / 0.22)

    def _tick(self):
        moving = False
        for index, item in enumerate(self.slices):
            goal = 1.0 if index == self.hovered and item.ok else 0.0
            if self.motion:
                item.hover += (goal - item.hover) * 0.28
                if abs(goal - item.hover) < 0.01:
                    item.hover = goal
                else:
                    moving = True
            else:
                item.hover = goal
        flip = self._flip_progress()
        if flip is not None:
            moving = True
            if flip >= 0.5 and not self._flip_swapped:
                self._flip_swapped = True
                self.face = 1 - self.face
                self.hovered = -1
            if flip >= 1.0:
                self._flip_started = None
        if self.motion and time.monotonic() - self._open_started < 0.2:
            moving = True
        self.update()
        if not moving:
            self._timer.stop()

    def index_at(self, pos):
        c = self.center()
        return slice_at(pos.x() - c.x(), pos.y() - c.y(), len(self.slices))

    def in_hub(self, pos):
        c = self.center()
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) < INNER

    def hover_at(self, pos):
        index = self.index_at(pos)
        if index != self.hovered:
            self.hovered = index
            self.keyboard = False
            self._start()

    def preset_at(self, pos):
        index = self.index_at(pos)
        if index < 0:
            return None
        item = self.slices[index]
        return item.preset if item.ok else None

    def mouseMoveEvent(self, event):
        self.hover_at(event.position())

    def leaveEvent(self, event):
        if not self.keyboard:
            self.hovered = -1
            self._start()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            self.dismissed.emit()
            return
        pos = event.position()
        index = self.index_at(pos)
        if index >= 0:
            if self.slices[index].ok:
                self.activated.emit(self.slices[index].preset)
            return
        if self.in_hub(pos):
            self.flip()
            return
        self.dismissed.emit()

    def wheelEvent(self, event):
        if abs(event.angleDelta().y()) >= 60 or abs(event.angleDelta().x()) >= 60:
            self.flip()
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        count = len(self.slices)
        if key == Qt.Key_Escape:
            self.dismissed.emit()
        elif key in (Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Space):
            self.flip()
        elif key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Left, Qt.Key_Up) and count:
            step = 1 if key in (Qt.Key_Right, Qt.Key_Down) else -1
            start = self.hovered if self.hovered >= 0 else (-1 if step > 0 else 0)
            for offset in range(1, count + 1):
                candidate = (start + step * offset) % count
                if self.slices[candidate].ok:
                    self.hovered = candidate
                    break
            self.keyboard = True
            self._start()
        elif key in (Qt.Key_Return, Qt.Key_Enter) and 0 <= self.hovered < count and self.slices[self.hovered].ok:
            self.activated.emit(self.slices[self.hovered].preset)
        elif Qt.Key_1 <= key <= Qt.Key_9:
            index = key - Qt.Key_1
            if index < count and self.slices[index].ok:
                self.activated.emit(self.slices[index].preset)
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, forward):
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        surface = palette.window().color()
        text = palette.windowText().color()
        dark = surface.lightness() < 128
        c = self.center()

        opened = min(1.0, (time.monotonic() - self._open_started) / 0.18) if self.motion else 1.0
        scale = 0.72 + 0.28 * ease(opened)
        painter.setOpacity(self.opacity * ease(opened))
        painter.translate(c)
        flip = self._flip_progress()
        horizontal = abs(math.cos(math.pi * flip)) if flip is not None else 1.0
        painter.scale(scale * max(0.02, horizontal), scale)

        shadow = QRadialGradient(QPointF(0, 6), OUTER + MARGIN)
        shadow.setColorAt(0.75, QColor(0, 0, 0, 70 if dark else 45))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QPointF(0, 6), OUTER + MARGIN, OUTER + MARGIN)

        ring = QPainterPath()
        ring.addEllipse(QPointF(0, 0), OUTER + 4, OUTER + 4)
        hole = QPainterPath()
        hole.addEllipse(QPointF(0, 0), INNER - 4, INNER - 4)
        painter.setBrush(mix(surface, text, 0.06))
        painter.drawPath(ring.subtracted(hole))

        count = len(self.slices)
        span = slice_span(count)
        base = mix(surface, self.accent, 0.20 if dark else 0.14)
        label_font = QFont(self.font())
        label_font.setBold(True)
        label_font.setPointSizeF(max(8.0, self.font().pointSizeF() * (1.05 if count <= 8 else 0.95)))
        for index, item in enumerate(self.slices):
            start = -90.0 + index * span + GAP / 2
            end = start + span - GAP
            mid = math.radians((start + end) / 2)
            push = 7.0 * item.hover
            offset = QPointF(math.cos(mid) * push, math.sin(mid) * push)
            outer = OUTER + 4.0 * item.hover
            path = self._slice_path(start, end, INNER, outer)
            path.translate(offset)
            if item.ok:
                fill = mix(base, self.accent, item.hover)
            else:
                fill = mix(surface, text, 0.04)
            painter.setBrush(fill)
            painter.setPen(QPen(mix(fill, text, 0.12), 1))
            painter.drawPath(path)

            radius = (INNER + outer) / 2 + 2
            point = QPointF(math.cos(mid) * radius, math.sin(mid) * radius) + offset
            if item.ok:
                color = mix(text, QColor("white"), item.hover)
            else:
                color = QColor(text)
                color.setAlpha(90)
            painter.setPen(color)
            painter.setFont(label_font)
            label = t(item.preset.label)
            box = QRectF(point.x() - 42, point.y() - 12, 84, 24)
            painter.drawText(box, Qt.AlignCenter, painter.fontMetrics().elidedText(label, Qt.ElideRight, 78))

        hub = INNER - 8
        painter.setPen(Qt.NoPen)
        painter.setBrush(surface)
        painter.drawEllipse(QPointF(0, 0), hub, hub)

        title, subtitle = self._hub_text()
        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(text)
        metrics = painter.fontMetrics()
        painter.drawText(QRectF(-hub + 6, -hub + 14, 2 * hub - 12, hub - 12), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap,
                         metrics.elidedText(title, Qt.ElideRight, int(2 * hub - 12) * 2))
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() * 0.82))
        painter.setFont(small)
        muted = QColor(text)
        muted.setAlpha(170)
        painter.setPen(muted)
        painter.drawText(QRectF(-hub + 6, 2, 2 * hub - 12, hub - 6), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, subtitle)

    def _hub_text(self):
        if 0 <= self.hovered < len(self.slices):
            item = self.slices[self.hovered]
            return t(item.preset.name), t(item.reason) if not item.ok else self._files_text()
        title = t(self.profile.name) if self.profile else ""
        hint = self._files_text()
        if self.can_flip():
            hint += "\n" + (t("⟳ Tab: back") if self.face == 0 else t("⟳ Tab: front"))
        return title, hint

    def _files_text(self):
        count = len(self.files)
        if count == 1:
            name = self.files[0].name if hasattr(self.files[0], "name") else str(self.files[0])
            return self.fontMetrics().elidedText(name, Qt.ElideMiddle, int(2 * INNER - 24))
        return t("{0} files").format(count)

    def _slice_path(self, start, end, inner, outer):
        path = QPainterPath()
        steps = max(6, int((end - start) / 3))
        for i in range(steps + 1):
            a = math.radians(start + (end - start) * i / steps)
            point = QPointF(math.cos(a) * outer, math.sin(a) * outer)
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        for i in range(steps, -1, -1):
            a = math.radians(start + (end - start) * i / steps)
            path.lineTo(QPointF(math.cos(a) * inner, math.sin(a) * inner))
        path.closeSubpath()
        return path
