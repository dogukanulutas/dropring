import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, engines, formats, integration, presets
from ..i18n import LANGUAGES, t
from .donut import DonutWidget
from .widgets import DropZone, JobCard, Thumbnails, file_icon, open_path, reveal, section_title, thumb_frame


def local_files(mime):
    files = []
    for url in mime.urls():
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            if path.exists():
                files.append(path)
    return files


class DonutLayer(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.donut = None

    def clear(self):
        if self.donut:
            self.donut.deleteLater()
            self.donut = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def show_donut(self, files, planner, settings, pos):
        self.clear()
        self.setGeometry(self.parent().rect())
        self.donut = DonutWidget(self, settings.get("accent"), settings.get("donut_opacity", 0.96))
        self.donut.set_files(files, planner)
        self.place(pos)
        self.donut.show()
        self.show()
        self.raise_()
        return self.donut

    def place(self, pos):
        size = self.donut.size()
        x = min(max(0, pos.x() - size.width() // 2), max(0, self.width() - size.width()))
        y = min(max(0, pos.y() - size.height() // 2), max(0, self.height() - size.height()))
        self.donut.move(x, y)

    def interactive(self):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.donut.setFocus()

    def mousePressEvent(self, event):
        if self.donut and not self.donut.geometry().contains(event.position().toPoint()):
            self.clear()


class HomePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        self.drop = DropZone()
        self.drop.browse.clicked.connect(window.browse)
        layout.addWidget(self.drop)

        self.jobs_title = section_title(t("Conversions"))
        self.jobs_box = QVBoxLayout()
        self.jobs_box.setSpacing(8)
        layout.addWidget(self.jobs_title)
        layout.addLayout(self.jobs_box)
        self.jobs_title.hide()

        header = QHBoxLayout()
        header.addWidget(section_title(t("Recent conversions")))
        header.addStretch()
        clear = QPushButton(QIcon.fromTheme("edit-clear-history"), t("Clear"))
        clear.setFlat(True)
        clear.clicked.connect(window.clear_history)
        header.addWidget(clear)
        layout.addLayout(header)

        self.recent = QListWidget()
        self.recent.setIconSize(QSize(48, 48))
        self.recent.setSpacing(2)
        self.recent.setUniformItemSizes(True)
        self.recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._recent_menu)
        self.recent.itemActivated.connect(lambda item: open_path(item.data(Qt.UserRole)) if item.data(Qt.UserRole) else None)
        self.recent.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self.recent, 1)
        self.empty = QLabel(t("Converted files appear here."))
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.empty)

    def _recent_menu(self, pos):
        item = self.recent.itemAt(pos)
        if not item or not item.data(Qt.UserRole):
            return
        path = Path(item.data(Qt.UserRole))
        menu = QMenu(self)
        menu.addAction(QIcon.fromTheme("document-open"), t("Open"), lambda: open_path(path))
        menu.addAction(QIcon.fromTheme("document-open-folder"), t("Show in folder"), lambda: reveal(path))
        menu.addAction(QIcon.fromTheme("edit-copy"), t("Copy path"), lambda: QGuiApplication.clipboard().setText(str(path)))
        if path.exists():
            menu.addSeparator()
            menu.addAction(QIcon.fromTheme("view-refresh"), t("Convert again…"), lambda: self.window.controller.open_ring([path]))
        menu.exec(self.recent.mapToGlobal(pos))


class WatchPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.store = window.store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(section_title(t("Watch folders")))
        info = QLabel(t("Every new file that lands in a watched folder is converted with the chosen preset. Results go to a subfolder so they are never converted again."))
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(info)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([t("Folder"), t("Preset"), t("Subfolders"), t("Into subfolder"), t("On")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton(QIcon.fromTheme("list-add"), t("Add folder…"))
        add.clicked.connect(self.add)
        remove = QPushButton(QIcon.fromTheme("list-remove"), t("Remove"))
        remove.clicked.connect(self.remove)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout.addLayout(row)
        self.load()

    def load(self):
        self.table.setRowCount(0)
        for rule in self.store.settings["watch"]:
            self._add_row(rule)

    def _add_row(self, rule):
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(QIcon.fromTheme("folder"), rule["folder"])
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, 0, item)
        combo = QComboBox()
        for preset in presets.PRESETS:
            if preset.kind in (presets.MERGE_PDF, presets.ZIP_ALL):
                continue
            combo.addItem(t(preset.name), preset.id)
        combo.setCurrentIndex(max(0, combo.findData(rule["preset"])))
        combo.currentIndexChanged.connect(self.save)
        self.table.setCellWidget(row, 1, combo)
        for column, key, default in ((2, "recursive", False), (3, "into_subfolder", True), (4, "enabled", True)):
            box = QCheckBox()
            box.setChecked(rule.get(key, default))
            box.toggled.connect(self.save)
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setAlignment(Qt.AlignCenter)
            holder_layout.addWidget(box)
            self.table.setCellWidget(row, column, holder)

    def add(self):
        folder = QFileDialog.getExistingDirectory(self, t("Choose a folder to watch"), str(Path.home()))
        if not folder:
            return
        self._add_row({"folder": folder, "preset": "to-pdf", "recursive": False, "into_subfolder": True, "enabled": True})
        self.save()

    def remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        self.save()

    def save(self):
        rules = []
        for row in range(self.table.rowCount()):
            def box(column):
                return self.table.cellWidget(row, column).findChild(QCheckBox).isChecked()
            rules.append({
                "folder": self.table.item(row, 0).text(),
                "preset": self.table.cellWidget(row, 1).currentData(),
                "recursive": box(2),
                "into_subfolder": box(3),
                "enabled": box(4),
            })
        self.store.settings["watch"] = rules
        self.store.save_settings()
        self.window.controller.apply_watch()


class SettingsPage(QScrollArea):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.store = window.store
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        settings = self.store.settings
        out = settings["output"]

        where = QGroupBox(t("Where results go"))
        where_layout = QFormLayout(where)
        self.same = QRadioButton(t("Next to the original"))
        self.sub = QRadioButton(t("In a subfolder next to the original"))
        self.custom = QRadioButton(t("In a fixed folder"))
        {"same": self.same, "subfolder": self.sub, "custom": self.custom}.get(out.get("location"), self.same).setChecked(True)
        self.sub_name = QLineEdit(out.get("subfolder", "converted"))
        self.folder = QLineEdit(out.get("custom_folder", ""))
        pick = QPushButton(QIcon.fromTheme("document-open-folder"), "")
        pick.clicked.connect(self._pick_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder)
        folder_row.addWidget(pick)
        where_layout.addRow(self.same)
        where_layout.addRow(self.sub, self.sub_name)
        where_layout.addRow(self.custom, folder_row)
        self.pattern = QLineEdit(out.get("pattern", "{name}"))
        self.pattern.setPlaceholderText("{name}")
        self.pattern.setToolTip("{name} {ext} {preset} {date} {time} {index}")
        where_layout.addRow(t("File name"), self.pattern)
        self.conflict = QComboBox()
        for key, label in (("rename", t("Add a number: name (2)")), ("overwrite", t("Replace the existing file")), ("skip", t("Skip the file"))):
            self.conflict.addItem(label, key)
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(out.get("conflict", "rename"))))
        where_layout.addRow(t("When the file exists"), self.conflict)
        note = QLabel(t("The original file is never overwritten."))
        note.setStyleSheet("color: palette(placeholder-text);")
        where_layout.addRow(note)
        layout.addWidget(where)

        behaviour = QGroupBox(t("Behaviour"))
        form = QFormLayout(behaviour)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(settings.get("parallel", 2))
        form.addRow(t("Files at once"), self.parallel)
        self.notify = QCheckBox(t("Show a notification when a conversion ends"))
        self.notify.setChecked(settings.get("notify", True))
        form.addRow(self.notify)
        self.tray = QCheckBox(t("Keep running in the tray when the window closes"))
        self.tray.setChecked(settings.get("close_to_tray", True))
        form.addRow(self.tray)
        self.autostart = QCheckBox(t("Start at sign-in (in the tray)"))
        self.autostart.setChecked(integration.autostart_enabled())
        form.addRow(self.autostart)
        self.language = QComboBox()
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.get("language", "auto"))))
        form.addRow(t("Language"), self.language)
        layout.addWidget(behaviour)

        look = QGroupBox(t("Ring"))
        look_form = QFormLayout(look)
        self.accent = QPushButton()
        self.accent.setFixedWidth(140)
        self.accent.clicked.connect(self._pick_color)
        self._accent = settings.get("accent", "#7F77DD")
        self._paint_accent()
        look_form.addRow(t("Accent colour"), self.accent)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(50, 100)
        self.opacity.setValue(int(settings.get("donut_opacity", 0.96) * 100))
        look_form.addRow(t("Opacity"), self.opacity)
        self.motion = QCheckBox(t("Animations"))
        self.motion.setChecked(settings.get("motion", True))
        look_form.addRow(self.motion)
        layout.addWidget(look)

        layout.addWidget(self._shortcut_group())

        desktop = QGroupBox(t("Desktop integration"))
        desktop_layout = QVBoxLayout(desktop)
        text = QLabel(t("Adds “Convert with Dropring…” and a “Convert to” submenu to Dolphin's right-click menu (and a Nautilus script), plus the app launcher."))
        text.setWordWrap(True)
        desktop_layout.addWidget(text)
        buttons = QHBoxLayout()
        install = QPushButton(QIcon.fromTheme("list-add"), t("Install menu entries"))
        install.clicked.connect(self._install)
        remove = QPushButton(QIcon.fromTheme("list-remove"), t("Remove"))
        remove.clicked.connect(self._remove)
        buttons.addWidget(install)
        buttons.addWidget(remove)
        buttons.addStretch()
        desktop_layout.addLayout(buttons)
        layout.addWidget(desktop)
        layout.addStretch()

        save_row = QHBoxLayout()
        save_row.addStretch()
        save = QPushButton(QIcon.fromTheme("document-save"), t("Save"))
        save.setDefault(True)
        save.clicked.connect(self.save)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        self.setWidget(body)

    def _shortcut_group(self):
        group = QGroupBox(t("Keyboard shortcut"))
        form = QFormLayout(group)
        hotkey = getattr(self.window.controller, "hotkey", None)
        explain = QLabel(t("Select files in Dolphin, press Ctrl+C, then press the shortcut: the ring opens for those files."))
        explain.setWordWrap(True)
        form.addRow(explain)
        if hotkey is None or not hotkey.available:
            manual = QLabel(t("Your desktop has no KDE global shortcut service. Add a custom shortcut in your desktop settings that runs:") + "<br><code>dropring menu --clipboard</code>")
            manual.setWordWrap(True)
            manual.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(manual)
            return group
        self.hotkey_on = QCheckBox(t("Enable the shortcut"))
        self.hotkey_on.setChecked(self.store.settings.get("hotkey_enabled", True))
        self.hotkey_on.toggled.connect(self._toggle_hotkey)
        form.addRow(self.hotkey_on)
        self.hotkey_edit = QKeySequenceEdit(QKeySequence(hotkey.active or self.store.settings.get("hotkey", "Meta+Shift+C")))
        self.hotkey_edit.setMaximumSequenceLength(1)
        self.hotkey_edit.setClearButtonEnabled(True)
        self.hotkey_edit.editingFinished.connect(self._apply_hotkey)
        form.addRow(t("Shortcut"), self.hotkey_edit)
        self.hotkey_status = QLabel()
        self.hotkey_status.setWordWrap(True)
        form.addRow(self.hotkey_status)
        self._show_hotkey_status()
        return group

    def _show_hotkey_status(self):
        active = self.window.controller.hotkey.active
        if not self.hotkey_on.isChecked():
            self.hotkey_status.setText(t("The shortcut is off."))
        elif active:
            self.hotkey_status.setText(t("Active: {0}. You can also change it in System Settings → Shortcuts → Dropring.").format(active))
        else:
            self.hotkey_status.setText(t("This shortcut is used by something else. Choose another one."))

    def _toggle_hotkey(self, enabled):
        self.store.settings["hotkey_enabled"] = enabled
        self.store.save_settings()
        self.window.controller.apply_hotkey()
        self._show_hotkey_status()

    def _apply_hotkey(self):
        sequence = self.hotkey_edit.keySequence().toString(QKeySequence.PortableText)
        if not sequence:
            return
        self.window.controller.change_hotkey(sequence)
        self._show_hotkey_status()

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, t("Output folder"), self.folder.text() or str(Path.home()))
        if folder:
            self.folder.setText(folder)
            self.custom.setChecked(True)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self._accent), self, t("Accent colour"))
        if color.isValid():
            self._accent = color.name()
            self._paint_accent()

    def _paint_accent(self):
        self.accent.setText(self._accent)
        self.accent.setStyleSheet(f"background:{self._accent}; color:white; padding:4px 14px; border-radius:6px;")

    def _install(self):
        written = integration.install()
        QMessageBox.information(self, APP_NAME, t("Menu entries installed:") + "\n" + "\n".join(str(p) for p in written))

    def _remove(self):
        integration.remove()
        QMessageBox.information(self, APP_NAME, t("Desktop integration removed."))

    def save(self):
        settings = self.store.settings
        location = "subfolder" if self.sub.isChecked() else "custom" if self.custom.isChecked() else "same"
        if location == "custom" and not self.folder.text().strip():
            QMessageBox.warning(self, APP_NAME, t("Choose the output folder first."))
            return
        settings["output"] = {
            "location": location,
            "subfolder": self.sub_name.text().strip() or "converted",
            "custom_folder": self.folder.text().strip(),
            "pattern": self.pattern.text().strip() or "{name}",
            "conflict": self.conflict.currentData(),
        }
        old_language = settings.get("language")
        settings.update({
            "parallel": self.parallel.value(),
            "notify": self.notify.isChecked(),
            "close_to_tray": self.tray.isChecked(),
            "language": self.language.currentData(),
            "accent": self._accent,
            "donut_opacity": self.opacity.value() / 100,
            "motion": self.motion.isChecked(),
        })
        self.store.save_settings()
        integration.set_autostart(self.autostart.isChecked())
        self.window.controller.settings_changed()
        if old_language != settings["language"]:
            QMessageBox.information(self, APP_NAME, t("The language changes after a restart."))
        else:
            self.window.statusBar().showMessage(t("Settings saved."), 3000)


class EnginesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(section_title(t("Conversion engines")))
        info = QLabel(t("Dropring uses the tools installed on your system. Missing tools only turn off the formats they handle."))
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(info)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels([t("Engine"), t("Status"), t("Conversions")])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for engine in engines.all_engines():
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(engine.name))
            ok = engine.available()
            status = QTableWidgetItem(QIcon.fromTheme("dialog-ok-apply" if ok else "dialog-error"), t("Found") if ok else t("Missing"))
            table.setItem(row, 1, status)
            sources = sorted({formats.by_id(e.source).name for e in engine.edges})
            table.setItem(row, 2, QTableWidgetItem(t("{0} routes from {1}").format(len(engine.edges), ", ".join(sources[:12]) + (" …" if len(sources) > 12 else "")) if ok else ", ".join(engine.binaries)))
        layout.addWidget(table, 1)
        hint = QLabel(t("Install everything on Fedora:") + "\n<code>sudo dnf install ImageMagick ffmpeg libreoffice poppler-utils ghostscript p7zip p7zip-plugins</code>"
                      + "<br>" + t("Ubuntu/Debian:") + "<br><code>sudo apt install imagemagick ffmpeg libreoffice poppler-utils ghostscript 7zip</code>")
        hint.setTextFormat(Qt.RichText)
        hint.setText(hint.text().replace("\n", "<br>"))
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hint.setWordWrap(True)
        layout.addWidget(hint)


class AboutPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 20)
        layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        logo = QLabel()
        logo.setPixmap(window.windowIcon().pixmap(96, 96))
        logo.setAlignment(Qt.AlignCenter)
        title = QLabel(f"<h1>{APP_NAME}</h1>")
        title.setAlignment(Qt.AlignCenter)
        text = QLabel(
            f"{t('Version')} {__version__}<br><br>"
            + t("Drag files, drop them on a format in the ring. Everything stays on your computer.")
            + "<br><br>" + t("Inspired by <a href='https://github.com/KnifeLemon/Filee'>Filee</a>. MIT licence.")
        )
        text.setOpenExternalLinks(True)
        text.setAlignment(Qt.AlignCenter)
        text.setWordWrap(True)
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addWidget(text)
        tips = QLabel(
            "<b>" + t("Tips") + "</b><ul>"
            + "<li>" + t("Tab, Space or the mouse wheel flips the ring to more formats and tools.") + "</li>"
            + "<li>" + t("Arrow keys pick a slice, Enter converts, 1–9 picks directly, Esc closes.") + "</li>"
            + "<li>" + t("In Dolphin: right-click files → Convert with Dropring…") + "</li>"
            + "<li>" + t("Or select files, press Ctrl+C, then Meta+Shift+C.") + "</li>"
            + "<li>" + t("Command line: dropring convert *.png --to jpg") + "</li></ul>"
        )
        tips.setWordWrap(True)
        layout.addSpacing(20)
        layout.addWidget(tips)


class MainWindow(QMainWindow):
    quit_requested = Signal()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.store = controller.store
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(controller.icon)
        self.resize(900, 680)
        self.setAcceptDrops(True)
        self.cards = []
        self.thumbs = Thumbnails(96, self)
        self.thumbs.ready.connect(self._thumb_ready)
        self._recent_items = {}

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(190)
        self.nav.setIconSize(QSize(22, 22))
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pages = QStackedWidget()
        self.home = HomePage(self)
        self.watch_page = WatchPage(self)
        self.settings_page = SettingsPage(self)
        entries = [
            ("go-home", t("Home"), self.home),
            ("folder-sync", t("Watch folders"), self.watch_page),
            ("configure", t("Settings"), self.settings_page),
            ("run-build", t("Engines"), EnginesPage(self)),
            ("help-about", t("About"), AboutPage(self)),
        ]
        for icon, label, page in entries:
            item = QListWidgetItem(QIcon.fromTheme(icon), label)
            item.setSizeHint(QSize(180, 40))
            self.nav.addItem(item)
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        root.addWidget(self.nav)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.layer = DonutLayer(central)
        self.layer.hide()
        self.nav.currentRowChanged.connect(lambda _: self.layer.clear() if not (self.layer.donut and self.layer.donut.drag_mode) else None)
        self._drag_files = []

        menu = self.menuBar().addMenu(t("&File"))
        open_action = QAction(QIcon.fromTheme("document-open"), t("Convert files…"), self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.browse)
        menu.addAction(open_action)
        menu.addSeparator()
        quit_action = QAction(QIcon.fromTheme("application-exit"), t("Quit"), self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)
        self.menuBar().hide()
        self.addAction(open_action)
        self.addAction(quit_action)

        self.apply_style()
        self.refresh_recent()

    def apply_style(self):
        accent = QColor(self.store.settings.get("accent", "#7F77DD"))
        self.home.drop.accent = accent
        self.setStyleSheet(f"""
            QListWidget#Nav {{ background: palette(alternate-base); padding: 10px 6px; outline: 0; }}
            QListWidget#Nav::item {{ border-radius: 8px; padding: 4px 8px; margin: 1px 4px; }}
            QListWidget#Nav::item:selected {{ background: {accent.name()}; color: white; }}
            QFrame#Card {{ border-radius: 10px; }}
            QProgressBar {{ border: 0; border-radius: 3px; background: palette(midlight); }}
            QProgressBar::chunk {{ border-radius: 3px; background: {accent.name()}; }}
        """)
        self.home.drop.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.layer.isVisible():
            self.layer.setGeometry(self.centralWidget().rect())

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, t("Choose files to convert"), str(Path.home()))
        if files:
            pos = self.layer.parent().mapFromGlobal(self.home.drop.mapToGlobal(self.home.drop.rect().center()))
            self.show_ring([Path(f) for f in files], pos, interactive=True)

    def show_ring(self, files, pos, interactive):
        donut = self.layer.show_donut(files, self.controller.queue.planner, self.store.settings, pos)
        donut.motion = self.store.settings.get("motion", True)
        donut.activated.connect(lambda preset: self._chosen(preset, files))
        donut.dismissed.connect(self.layer.clear)
        if interactive:
            self.layer.interactive()
        return donut

    def _chosen(self, preset, files):
        self.layer.clear()
        self.controller.start(files, preset)

    def _layer_pos(self, event):
        return self.layer.parent().mapFrom(self, event.position().toPoint())

    def dragEnterEvent(self, event):
        files = local_files(event.mimeData())
        if not files:
            event.ignore()
            return
        self._drag_files = files
        self.nav.setCurrentRow(0)
        self.home.drop.set_active(True)
        donut = self.show_ring(files, self._layer_pos(event), interactive=False)
        donut.drag_mode = True
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        donut = self.layer.donut
        if donut is None:
            event.ignore()
            return
        pos = donut.mapFrom(self, event.position().toPoint())
        donut.hover_at(pos)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.home.drop.set_active(False)
        QTimer.singleShot(80, self._maybe_clear_after_leave)

    def _maybe_clear_after_leave(self):
        if not self.rect().contains(self.mapFromGlobal(self.cursor().pos())) and self.layer.donut and self.layer.donut.drag_mode:
            self.layer.clear()

    def dropEvent(self, event):
        self.home.drop.set_active(False)
        donut = self.layer.donut
        if donut is None:
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        pos = donut.mapFrom(self, event.position().toPoint())
        preset = donut.preset_at(pos)
        donut.drag_mode = False
        if preset:
            self._chosen(preset, self._drag_files)
        else:
            self.layer.interactive()

    def add_job(self, job):
        self.home.jobs_title.show()
        card = JobCard(job)
        card.dismissed.connect(self._dismiss)
        self.home.jobs_box.insertWidget(0, card)
        self.cards.insert(0, card)
        while len(self.cards) > 6:
            old = next((c for c in reversed(self.cards) if c.job.done), None)
            if old is None:
                break
            self._dismiss(old)

    def update_job(self, job):
        for card in self.cards:
            if card.job is job:
                card.refresh()
        if job.done:
            self.refresh_recent()

    def _dismiss(self, card):
        if card in self.cards:
            self.cards.remove(card)
        card.deleteLater()
        if not self.cards:
            self.home.jobs_title.hide()

    def clear_history(self):
        self.store.clear_history()
        self.refresh_recent()

    def refresh_recent(self):
        recent = self.home.recent
        recent.clear()
        self._recent_items = {}
        rows = 0
        for entry in self.store.history[:80]:
            outputs = entry.get("outputs") or []
            when = datetime.fromtimestamp(entry.get("time", time.time())).strftime("%d.%m %H:%M")
            if not outputs:
                text = f"{t(entry.get('preset_name', ''))} — {t('failed')}\n" + "; ".join(entry.get("errors", [])[:2])
                item = QListWidgetItem(QIcon.fromTheme("dialog-warning"), text)
                item.setToolTip("\n".join(entry.get("errors", [])))
                recent.addItem(item)
                rows += 1
                continue
            for out in outputs[:4]:
                path = Path(out)
                exists = path.exists()
                source = Path(entry["sources"][0]).name if entry.get("sources") else ""
                text = f"{path.name}\n{when} · {t(entry.get('preset_name', ''))} · {source}" + ("" if exists else f" · {t('moved or deleted')}")
                item = QListWidgetItem(file_icon(path), text)
                item.setData(Qt.UserRole, str(path))
                item.setToolTip(str(path))
                if not exists:
                    item.setForeground(self.palette().placeholderText())
                pixmap = self.thumbs.request(path) if exists else None
                if pixmap:
                    item.setIcon(QIcon(thumb_frame(pixmap, 48)))
                self._recent_items.setdefault(str(path), []).append(item)
                recent.addItem(item)
                rows += 1
            if len(outputs) > 4:
                recent.addItem(QListWidgetItem(t("… and {0} more").format(len(outputs) - 4)))
        self.home.empty.setVisible(rows == 0)
        recent.setVisible(rows > 0)

    def _thumb_ready(self, path, pixmap):
        for item in self._recent_items.get(path, []):
            try:
                item.setIcon(QIcon(thumb_frame(pixmap, 48)))
            except RuntimeError:
                pass

    def closeEvent(self, event):
        if self.store.settings.get("close_to_tray", True) and self.controller.tray_available():
            event.ignore()
            self.hide()
        else:
            event.accept()
            self.quit_requested.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.layer.isVisible():
            self.layer.clear()
        else:
            super().keyPressEvent(event)
