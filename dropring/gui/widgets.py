from pathlib import Path

from PySide6.QtCore import QMimeDatabase, QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QToolButton, QVBoxLayout

from .. import jobs
from ..i18n import t

_MIME = QMimeDatabase()


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal(path):
    import shutil
    import subprocess

    path = Path(path)
    if shutil.which("dolphin"):
        subprocess.Popen(["dolphin", "--select", str(path)], start_new_session=True)
    elif shutil.which("nautilus"):
        subprocess.Popen(["nautilus", "--select", str(path)], start_new_session=True)
    else:
        open_path(path.parent if path.is_file() else path)


def file_icon(path, size=48):
    mime = _MIME.mimeTypeForFile(str(path))
    icon = QIcon.fromTheme(mime.iconName())
    if icon.isNull():
        icon = QIcon.fromTheme(mime.genericIconName(), QIcon.fromTheme("text-x-generic"))
    return icon


class _ThumbSignals(QObject):
    ready = Signal(str, QImage)


class _ThumbTask(QRunnable):
    def __init__(self, path, size, signals):
        super().__init__()
        self.path = path
        self.size = size
        self.signals = signals

    def run(self):
        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid():
            reader.setScaledSize(original.scaled(self.size, self.size, Qt.KeepAspectRatio))
        image = reader.read()
        if not image.isNull():
            self.signals.ready.emit(self.path, image)


class Thumbnails(QObject):
    ready = Signal(str, QPixmap)

    def __init__(self, size=64, parent=None):
        super().__init__(parent)
        self.size = size
        self.cache = {}
        self.signals = _ThumbSignals()
        self.signals.ready.connect(self._done)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

    def request(self, path):
        path = str(path)
        try:
            key = (path, Path(path).stat().st_mtime)
        except OSError:
            return None
        if key in self.cache:
            return self.cache[key]
        if QImageReader.imageFormat(path):
            self.cache[key] = None
            self.pool.start(_ThumbTask(path, self.size, self.signals))
        return None

    def _done(self, path, image):
        try:
            key = (path, Path(path).stat().st_mtime)
        except OSError:
            return
        pixmap = QPixmap.fromImage(image)
        self.cache[key] = pixmap
        self.ready.emit(path, pixmap)


class DropZone(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setMinimumHeight(190)
        self.active = False
        self.accent = QColor("#7F77DD")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setPixmap(QIcon.fromTheme("document-import", QIcon.fromTheme("folder-download")).pixmap(48, 48))
        self.title = QLabel(t("Drop files here"))
        self.title.setAlignment(Qt.AlignCenter)
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.35)
        font.setBold(True)
        self.title.setFont(font)
        self.hint = QLabel(t("The ring of formats opens under the cursor. Drop on a format, or on the centre to choose with a click."))
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        self.browse = QPushButton(QIcon.fromTheme("document-open"), t("Choose files…"))
        self.browse.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.browse)
        row.addStretch()
        layout.addWidget(self.icon)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addSpacing(6)
        layout.addLayout(row)

    def set_active(self, active):
        self.active = active
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        surface = self.palette().base().color()
        fill = QColor(self.accent)
        fill.setAlpha(40 if self.active else 14)
        painter.fillPath(path, surface)
        painter.fillPath(path, fill)
        pen = QPen(self.accent if self.active else self.palette().mid().color(), 2, Qt.DashLine)
        pen.setDashPattern([6, 5])
        painter.setPen(pen)
        painter.drawPath(path)


class JobCard(QFrame):
    dismissed = Signal(object)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("Card")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 10)
        self.icon = QLabel()
        self.icon.setFixedSize(36, 36)
        layout.addWidget(self.icon)
        middle = QVBoxLayout()
        middle.setSpacing(4)
        self.title = QLabel()
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.detail = QLabel()
        self.detail.setStyleSheet("color: palette(placeholder-text);")
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setWordWrap(True)
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        middle.addWidget(self.title)
        middle.addWidget(self.bar)
        middle.addWidget(self.detail)
        layout.addLayout(middle, 1)
        self.open_button = QToolButton()
        self.open_button.setIcon(QIcon.fromTheme("document-open-folder", QIcon.fromTheme("folder-open")))
        self.open_button.setToolTip(t("Show in folder"))
        self.open_button.clicked.connect(self._reveal)
        self.cancel_button = QToolButton()
        self.cancel_button.setIcon(QIcon.fromTheme("process-stop", QIcon.fromTheme("dialog-cancel")))
        self.cancel_button.setToolTip(t("Cancel"))
        self.cancel_button.clicked.connect(job.cancel)
        self.close_button = QToolButton()
        self.close_button.setIcon(QIcon.fromTheme("window-close"))
        self.close_button.setToolTip(t("Dismiss"))
        self.close_button.clicked.connect(lambda: self.dismissed.emit(self))
        for button in (self.open_button, self.cancel_button, self.close_button):
            button.setAutoRaise(True)
            layout.addWidget(button)
        self.refresh()

    def _reveal(self):
        outputs = self.job.outputs
        if outputs:
            reveal(outputs[0])

    def refresh(self):
        job = self.job
        count = len(job.files)
        name = job.files[0].source.name if count == 1 else t("{0} files").format(count)
        self.title.setText(f"{name}  →  {t(job.preset.name)}")
        self.bar.setValue(int(job.progress * 1000))
        counts = job.counts()
        if not job.done:
            running = counts.get(jobs.RUNNING, 0)
            finished = sum(counts.get(s, 0) for s in (jobs.DONE, jobs.FAILED, jobs.SKIPPED))
            self.detail.setText(t("Converting… {0}/{1}").format(finished, count) if running or finished else t("Waiting…"))
            self.icon.setPixmap(QIcon.fromTheme("view-refresh").pixmap(32, 32))
        else:
            failed = [f for f in job.files if f.status == jobs.FAILED]
            skipped = [f for f in job.files if f.status == jobs.SKIPPED]
            cancelled = counts.get(jobs.CANCELLED, 0)
            parts = []
            if job.outputs:
                parts.append(t("Saved: {0}").format(", ".join(Path(o).name for o in job.outputs[:3]) + (" …" if len(job.outputs) > 3 else "")))
            if failed:
                parts.append(t("Failed: {0}").format("; ".join(f"{f.source.name}: {f.error}" for f in failed[:2])))
            if skipped:
                parts.append(t("Skipped: {0}").format(", ".join(f"{f.source.name} ({t(f.error)})" for f in skipped[:2])))
            if cancelled:
                parts.append(t("Cancelled"))
            self.detail.setText("\n".join(parts) or t("Nothing to do"))
            ok = not failed and not cancelled
            self.icon.setPixmap(QIcon.fromTheme("dialog-ok-apply" if ok else "dialog-warning").pixmap(32, 32))
            self.bar.setValue(1000)
        self.cancel_button.setVisible(not job.done)
        self.open_button.setVisible(bool(job.outputs))
        self.close_button.setVisible(job.done)


def section_title(text):
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.1)
    label.setFont(font)
    return label


def thumb_frame(pixmap, size=56):
    canvas = QPixmap(QSize(size, size))
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, 8, 8)
    painter.setClipPath(path)
    scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return canvas

