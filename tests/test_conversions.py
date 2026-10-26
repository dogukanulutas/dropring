import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ["DROPRING_LANG"] = "en"

from dropring import presets
from dropring.jobs import DONE, JobQueue


def tool(name):
    return unittest.skipUnless(shutil.which(name), f"{name} not installed")


class ConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix="dropring-test-"))
        cls.queue = JobQueue(workers=2)

    @classmethod
    def tearDownClass(cls):
        cls.queue.shutdown()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def convert(self, paths, preset_id):
        job = self.queue.run_sync(paths, presets.BY_ID[preset_id], {"location": "same", "conflict": "rename"})
        for f in job.files:
            self.assertEqual(f.status, DONE, f.error)
        self.assertTrue(job.outputs)
        for out in job.outputs:
            self.assertTrue(Path(out).exists())
        return job.outputs

    def image(self):
        path = self.folder / "picture.png"
        if not path.exists():
            subprocess.run(["magick", "-size", "120x80", "gradient:red-blue", str(path)], check=True)
        return path

    @tool("magick")
    def test_image(self):
        out = self.convert([self.image()], "to-jpg")[0]
        self.assertEqual(out.suffix, ".jpg")
        half = self.convert([self.image()], "half-size")[0]
        size = subprocess.run(["magick", "identify", "-format", "%wx%h", str(half)], capture_output=True, text=True).stdout
        self.assertEqual(size, "60x40")

    @tool("ffmpeg")
    def test_video_to_audio(self):
        video = self.folder / "clip.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=160x120", "-f", "lavfi", "-i", "sine=d=1",
                        "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
        self.assertEqual(self.convert([video], "to-mp3")[0].suffix, ".mp3")

    @tool("soffice")
    def test_document_to_png_two_hops(self):
        text = self.folder / "letter.txt"
        text.write_text("Hello\n", encoding="utf-8")
        self.assertEqual(self.convert([text], "to-png")[0].suffix, ".png")

    def test_zip_and_extract(self):
        a = self.folder / "a.txt"
        b = self.folder / "b.txt"
        a.write_text("a")
        b.write_text("b")
        archive = self.convert([a, b], "zip-all")[0]
        folder = self.convert([archive], "extract")[0]
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["a.txt", "b.txt"])


if __name__ == "__main__":
    unittest.main()
