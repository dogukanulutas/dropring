import os
import tempfile
import unittest
from pathlib import Path

os.environ["DROPRING_LANG"] = "en"

from dropring import formats, output, presets
from dropring.engines import Edge
from dropring.planner import RoutePlanner


class FakeEngine:
    def __init__(self, engine_id, edges):
        self.id = engine_id
        self.edges = edges


class FormatTests(unittest.TestCase):
    def test_double_extensions(self):
        self.assertEqual(formats.detect("backup.tar.gz").id, "tgz")
        self.assertEqual(formats.name_without_extension("backup.tar.gz"), "backup")
        self.assertEqual(formats.detect("Photo.JPEG").id, "jpg")
        self.assertIsNone(formats.detect("README"))


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner([
            FakeEngine("office", [Edge("docx", "pdf", 20)]),
            FakeEngine("pdf", [Edge("pdf", "png", 8, final_only=True), Edge("pdf", "docx", 40, start_only=True)]),
            FakeEngine("image", [Edge("png", "jpg", 10), Edge("png", "pdf", 14, final_only=True), Edge("png", "png", 10)]),
        ])

    def test_two_hops(self):
        route = self.planner.plan("docx", "png")
        self.assertEqual([(s.source, s.target) for s in route.steps], [("docx", "pdf"), ("pdf", "png")])

    def test_final_only_is_not_a_middle_step(self):
        self.assertIsNone(self.planner.plan("docx", "jpg"))
        self.assertIsNone(self.planner.plan("png", "docx"))

    def test_self_edge(self):
        self.assertEqual(len(self.planner.plan("png", "png").steps), 1)
        self.assertIsNone(self.planner.plan("jpg", "jpg"))

    def test_targets_match_plans(self):
        for source in ("docx", "pdf", "png"):
            for target in self.planner.targets_from(source):
                self.assertIsNotNone(self.planner.plan(source, target), (source, target))


class OutputTests(unittest.TestCase):
    def test_rename_on_conflict_and_never_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "a.png"
            source.touch()
            (Path(folder) / "a.jpg").touch()
            rule = {"location": "same", "pattern": "{name}", "conflict": "overwrite"}
            self.assertEqual(output.resolve(rule, source, "png", lambda p: Path(p).exists()).name, "a (2).png")
            self.assertEqual(output.resolve(rule, source, "jpg", lambda p: Path(p).exists()).name, "a.jpg")
            rule["conflict"] = "rename"
            self.assertEqual(output.resolve(rule, source, "jpg", lambda p: Path(p).exists()).name, "a (2).jpg")
            rule["conflict"] = "skip"
            self.assertIsNone(output.resolve(rule, source, "jpg", lambda p: Path(p).exists()))

    def test_subfolder_and_pattern(self):
        rule = {"location": "subfolder", "subfolder": "out", "pattern": "{name}-{preset}"}
        path = output.resolve(rule, "/x/report.docx", "pdf", lambda p: False, preset_name="PDF")
        self.assertEqual(str(path), "/x/out/report-PDF.pdf")

    def test_sanitize(self):
        self.assertEqual(output.sanitize('a/b:c?', "x"), "a_b_c_")
        self.assertEqual(output.sanitize(" . ", "x"), "x")


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.planner = RoutePlanner([FakeEngine("image", [Edge("png", "jpg"), Edge("jpg", "png"), Edge("png", "png"), Edge("png", "pdf"), Edge("jpg", "pdf")])])

    def test_profile_selection(self):
        self.assertEqual(presets.select_profile(["a.png", "b.jpg"]).id, "images")
        self.assertEqual(presets.select_profile(["a.png", "b.mp4"]).id, "mixed")
        self.assertEqual(presets.select_profile(["a.mp3"]).id, "audio")

    def test_availability(self):
        self.assertFalse(presets.availability(presets.BY_ID["to-png"], ["a.png"], self.planner)[0])
        self.assertTrue(presets.availability(presets.BY_ID["to-png"], ["a.png", "b.jpg"], self.planner)[0])
        self.assertTrue(presets.availability(presets.BY_ID["half-size"], ["a.png"], self.planner)[0])
        self.assertFalse(presets.availability(presets.BY_ID["merge-pdf"], ["a.png"], self.planner)[0])
        self.assertTrue(presets.availability(presets.BY_ID["merge-pdf"], ["a.png", "b.jpg"], self.planner)[0])

    def test_find(self):
        self.assertEqual(presets.find("JPEG").id, "to-jpg")
        self.assertEqual(presets.find(".png").id, "to-png")
        self.assertEqual(presets.find("merge-pdf").id, "merge-pdf")
        self.assertIsNone(presets.find("nope"))

    def test_profiles_reference_presets(self):
        for profile in presets.PROFILES:
            for pid in profile.front + profile.back:
                self.assertIn(pid, presets.BY_ID, (profile.id, pid))
            self.assertLessEqual(len(profile.front), 12)
            self.assertLessEqual(len(profile.back), 12)


class DonutGeometryTests(unittest.TestCase):
    def test_slice_at(self):
        from dropring.gui.donut import INNER, OUTER, slice_at

        middle = (INNER + OUTER) / 2
        self.assertEqual(slice_at(0, -middle, 4), 0)
        self.assertEqual(slice_at(middle, 0, 4), 1)
        self.assertEqual(slice_at(0, middle, 4), 2)
        self.assertEqual(slice_at(-middle, 0, 4), 3)
        self.assertEqual(slice_at(0, 0, 4), -1)
        self.assertEqual(slice_at(OUTER * 2, 0, 4), -1)


if __name__ == "__main__":
    unittest.main()
