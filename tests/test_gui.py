import math
import os
import tempfile
import unittest
from pathlib import Path

os.environ["DROPRING_LANG"] = "en"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dropring import presets
from dropring.clipboard import parse_uri_list
from dropring.engines import Edge
from dropring.gui.donut import INNER, OUTER, slice_span
from dropring.planner import RoutePlanner

APP = QApplication.instance() or QApplication([])


class FakeEngine:
    id = "image"
    edges = [Edge(s, t) for s in ("png", "jpg", "webp") for t in ("png", "jpg", "webp", "pdf")]


PLANNER = RoutePlanner([FakeEngine()])


def slice_point(donut, index):
    span = slice_span(len(donut.slices))
    angle = math.radians(-90 + span * index + span / 2)
    radius = (INNER + OUTER) / 2
    center = donut.center()
    return QPoint(int(center.x() + math.cos(angle) * radius), int(center.y() + math.sin(angle) * radius))


class Files:
    def __init__(self, test, *names):
        self.folder = tempfile.TemporaryDirectory()
        test.addCleanup(self.folder.cleanup)
        self.paths = []
        for name in names:
            path = Path(self.folder.name) / name
            path.write_bytes(b"x")
            self.paths.append(path)


class DonutTests(unittest.TestCase):
    def make(self, *names):
        from dropring.gui.donut import DonutWidget

        self.files = Files(self, *names)
        donut = DonutWidget(motion=False)
        donut.set_files(self.files.paths, PLANNER)
        donut.show()
        return donut

    def test_click_enabled_slice(self):
        donut = self.make("a.png")
        chosen = []
        donut.activated.connect(chosen.append)
        index = next(i for i, s in enumerate(donut.slices) if s.preset.id == "to-jpg")
        QTest.mouseMove(donut, slice_point(donut, index))
        self.assertEqual(donut.hovered, index)
        QTest.mouseClick(donut, Qt.LeftButton, pos=slice_point(donut, index))
        self.assertEqual([p.id for p in chosen], ["to-jpg"])

    def test_disabled_slice_does_nothing(self):
        donut = self.make("a.png")
        chosen = []
        donut.activated.connect(chosen.append)
        index = next(i for i, s in enumerate(donut.slices) if s.preset.id == "to-png")
        self.assertFalse(donut.slices[index].ok)
        QTest.mouseClick(donut, Qt.LeftButton, pos=slice_point(donut, index))
        self.assertEqual(chosen, [])

    def test_keyboard(self):
        donut = self.make("a.png")
        chosen = []
        donut.activated.connect(chosen.append)
        dismissed = []
        donut.dismissed.connect(lambda: dismissed.append(True))
        QTest.keyClick(donut, Qt.Key_Right)
        first = donut.hovered
        self.assertTrue(donut.slices[first].ok)
        QTest.keyClick(donut, Qt.Key_Return)
        self.assertEqual(chosen[-1].id, donut.slices[first].preset.id)
        QTest.keyClick(donut, Qt.Key_2)
        self.assertEqual(chosen[-1].id, donut.slices[1].preset.id)
        QTest.keyClick(donut, Qt.Key_Escape)
        self.assertEqual(dismissed, [True])

    def test_flip_with_tab(self):
        donut = self.make("a.png")
        front = [s.preset.id for s in donut.slices]
        QTest.keyClick(donut, Qt.Key_Tab)
        self.assertEqual(donut.face, 1)
        self.assertNotEqual([s.preset.id for s in donut.slices], front)
        QTest.keyClick(donut, Qt.Key_Tab)
        self.assertEqual(donut.face, 0)

    def test_outside_click_dismisses(self):
        donut = self.make("a.png")
        dismissed = []
        donut.dismissed.connect(lambda: dismissed.append(True))
        QTest.mouseClick(donut, Qt.LeftButton, pos=QPoint(2, 2))
        self.assertEqual(dismissed, [True])


class OverlayTests(unittest.TestCase):
    def test_overlay_places_ring_where_pointer_enters_and_reports_choice(self):
        from PySide6.QtGui import QEnterEvent

        from dropring.gui.overlay import RingOverlay

        files = Files(self, "a.png", "b.jpg")
        overlay = RingOverlay(files.paths, PLANNER, {"motion": False})
        overlay.trust_cursor = False
        chosen = []
        overlay.chosen.connect(lambda paths, preset: chosen.append((preset.id, paths)))
        overlay.open()
        self.assertGreaterEqual(len(overlay.layers), 1)
        layer = overlay.layers[0]
        APP.sendEvent(layer, QEnterEvent(QPointF(300, 200), QPointF(300, 200), QPointF(300, 200)))
        self.assertIs(overlay.active, layer)
        donut = overlay.donut
        self.assertTrue(donut.isVisible())
        center = donut.geometry().center()
        self.assertLessEqual(abs(center.x() - 300), 2)
        self.assertLessEqual(abs(center.y() - 200), 2)
        index = next(i for i, s in enumerate(donut.slices) if s.preset.id == "to-webp")
        QTest.mouseClick(donut, Qt.LeftButton, pos=slice_point(donut, index))
        self.assertEqual(chosen, [("to-webp", files.paths)])
        self.assertEqual(overlay.layers, [])

    def test_fallback_when_no_pointer_event(self):
        from dropring.gui.overlay import RingOverlay

        files = Files(self, "a.png")
        overlay = RingOverlay(files.paths, PLANNER, {"motion": False})
        overlay.trust_cursor = False
        overlay.fallback_ms = 50
        overlay._fallback.setInterval(50)
        overlay.open()
        if overlay.active is None:
            QTest.qWait(150)
        self.assertIsNotNone(overlay.active)
        self.assertTrue(overlay.donut.isVisible())

    def test_escape_closes(self):
        from dropring.gui.overlay import RingOverlay

        files = Files(self, "a.png")
        overlay = RingOverlay(files.paths, PLANNER, {"motion": False})
        closed = []
        overlay.closed.connect(lambda: closed.append(True))
        overlay.open()
        QTest.keyClick(overlay.layers[0], Qt.Key_Escape)
        self.assertEqual(closed, [True])


class FakeController:
    def __init__(self, store):
        from dropring.gui.widgets import QIcon

        self.store = store
        self.queue = type("Q", (), {"planner": PLANNER})()
        self.icon = QIcon()
        self.started = []

    def start(self, files, preset):
        self.started.append((preset.id, list(files)))

    def tray_available(self):
        return False

    def open_ring(self, files):
        pass


class MainWindowTests(unittest.TestCase):
    def setUp(self):
        from dropring.gui.main_window import MainWindow
        from dropring.store import Store

        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        self.addCleanup(lambda: self.window.deleteLater())
        self.controller = FakeController(Store(self.config.name))
        self.window = MainWindow(self.controller)
        self.window.resize(900, 700)
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)
        self.files = Files(self, "photo.png")
        self.mime = QMimeData()
        self.mime.setUrls([QUrl.fromLocalFile(str(p)) for p in self.files.paths])

    def drag(self, pos, drop_at):
        enter = QDragEnterEvent(pos, Qt.CopyAction | Qt.MoveAction, self.mime, Qt.LeftButton, Qt.ControlModifier)
        APP.sendEvent(self.window, enter)
        self.assertTrue(enter.isAccepted())
        donut = self.window.layer.donut
        self.assertIsNotNone(donut)
        target = donut.mapTo(self.window, drop_at(donut))
        APP.sendEvent(self.window, QDragMoveEvent(target, Qt.CopyAction, self.mime, Qt.LeftButton, Qt.NoModifier))
        drop = QDropEvent(QPointF(target), Qt.CopyAction | Qt.MoveAction, self.mime, Qt.LeftButton, Qt.NoModifier)
        APP.sendEvent(self.window, drop)
        return donut, drop

    def test_drop_on_slice_starts_conversion(self):
        def on_pdf(donut):
            index = next(i for i, s in enumerate(donut.slices) if s.preset.id == "to-pdf")
            return slice_point(donut, index)

        donut, drop = self.drag(QPoint(450, 300), on_pdf)
        self.assertEqual(drop.dropAction(), Qt.CopyAction)
        self.assertEqual(self.controller.started, [("to-pdf", self.files.paths)])
        self.assertIsNone(self.window.layer.donut)

    def test_drop_on_hub_keeps_ring_for_click(self):
        donut, _ = self.drag(QPoint(450, 300), lambda d: d.center().toPoint())
        self.assertEqual(self.controller.started, [])
        self.assertIs(self.window.layer.donut, donut)
        index = next(i for i, s in enumerate(donut.slices) if s.preset.id == "to-jpg")
        QTest.mouseClick(donut, Qt.LeftButton, pos=slice_point(donut, index))
        self.assertEqual(self.controller.started, [("to-jpg", self.files.paths)])

    def test_ring_stays_inside_window(self):
        enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, self.mime, Qt.LeftButton, Qt.NoModifier)
        APP.sendEvent(self.window, enter)
        geometry = self.window.layer.donut.geometry()
        self.assertGreaterEqual(geometry.left(), 0)
        self.assertGreaterEqual(geometry.top(), 0)

    def test_non_file_drag_is_ignored(self):
        mime = QMimeData()
        mime.setText("hello")
        enter = QDragEnterEvent(QPoint(100, 100), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        APP.sendEvent(self.window, enter)
        self.assertFalse(enter.isAccepted())
        self.assertIsNone(self.window.layer.donut)


class ClipboardTests(unittest.TestCase):
    def test_uri_list(self):
        files = Files(self, "a b.png", "ç.txt")
        text = "# comment\r\n" + "\r\n".join(QUrl.fromLocalFile(str(p)).toString(QUrl.FullyEncoded) for p in files.paths) + "\r\nfile:///does/not/exist\r\nhttps://example.com/x.png\r\n"
        self.assertEqual(parse_uri_list(text), files.paths)
        self.assertEqual(parse_uri_list("file://otherhost/etc/passwd"), [])


class ControllerTests(unittest.TestCase):
    def setUp(self):
        from dropring.gui.controller import Controller
        from dropring.store import Store

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        os.environ["XDG_RUNTIME_DIR"] = self.temp.name
        store = Store(Path(self.temp.name) / "config")
        store.settings["hotkey_enabled"] = False
        store.settings["notify"] = False
        from unittest import mock

        from dropring.gui.overlay import RingOverlay

        self.controller = Controller(APP, store)
        self.notices = []
        self.controller.notify = lambda *args, **kwargs: self.notices.append(args)
        patcher = mock.patch.object(RingOverlay, "trust_cursor", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.controller.queue.shutdown)
        self.addCleanup(self.controller.server.close)
        self.files = Files(self, "shot.png")
        import subprocess

        subprocess.run(["magick", "-size", "40x30", "xc:red", str(self.files.paths[0])], check=True)

    def wait_for_job(self):
        for _ in range(100):
            QTest.qWait(50)
            jobs = self.controller.queue.jobs
            if jobs and jobs[-1].done:
                return jobs[-1]
        self.fail("conversion did not finish")

    @unittest.skipUnless(__import__("shutil").which("magick"), "ImageMagick not installed")
    def test_file_manager_ring_converts(self):
        from PySide6.QtGui import QEnterEvent

        self.controller.handle({"cmd": "menu", "files": [str(p) for p in self.files.paths]})
        overlay = self.controller.overlays[-1]
        layer = overlay.layers[0]
        APP.sendEvent(layer, QEnterEvent(QPointF(400, 300), QPointF(400, 300), QPointF(400, 300)))
        donut = overlay.donut
        index = next(i for i, s in enumerate(donut.slices) if s.preset.id == "to-jpg")
        QTest.mouseClick(donut, Qt.LeftButton, pos=slice_point(donut, index))
        job = self.wait_for_job()
        self.assertEqual([f.status for f in job.files], ["done"])
        self.assertEqual(job.outputs[0].name, "shot.jpg")
        self.assertTrue(job.outputs[0].exists())
        self.assertEqual(self.controller.overlays, [])

    @unittest.skipUnless(__import__("shutil").which("magick"), "ImageMagick not installed")
    def test_convert_command(self):
        self.controller.handle({"cmd": "convert", "preset": "to-webp", "files": [str(p) for p in self.files.paths]})
        job = self.wait_for_job()
        self.assertTrue(job.outputs[0].name.endswith(".webp"))

    def test_clipboard_opens_ring(self):
        from unittest import mock

        with mock.patch("dropring.clipboard.copied_files", return_value=self.files.paths):
            self.controller.handle({"cmd": "clipboard"})
        self.assertEqual(len(self.controller.overlays), 1)
        self.assertEqual(self.controller.overlays[0].files, self.files.paths)
        self.controller.overlays[0].close()

    def test_unavailable_choice_is_reported_not_raised(self):
        self.assertIsNone(self.controller.start(self.files.paths, presets.BY_ID["to-png"]))
        self.assertEqual(len(self.notices), 1)


class ProfileTests(unittest.TestCase):
    def test_every_ring_has_something_for_its_files(self):
        self.assertTrue(any(presets.availability(presets.BY_ID[p], ["a.png"], PLANNER)[0] for p in presets.select_profile(["a.png"]).front))


if __name__ == "__main__":
    unittest.main()
