import tempfile
import unittest
from pathlib import Path
from argparse import Namespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kwjd import read_srt, ass_time, cmd_make_ass, episode, inventory, discover_episodes

SRT = "1\n00:00:01,000 --> 00:00:02,300\n艺术啊 最简单\n\n2\n00:00:03,000 --> 00:00:04,100\n真实字幕\n"


class Tests(unittest.TestCase):
    def test_episode_range(self):
        self.assertEqual(episode(1), "S01E01")
        self.assertEqual(episode(49), "S01E49")
        self.assertEqual(episode(56), "S01E56")
        self.assertEqual(episode(100), "S01E100")
        with self.assertRaises(ValueError):
            episode(0)

    def test_srt_and_ass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srt = root / "test.srt"
            srt.write_text(SRT, encoding="utf-8")
            cues = read_srt(srt)
            self.assertEqual(cues[0][2], "艺术啊 最简单")
            self.assertEqual(ass_time(2300), "0:00:02.30")
            template = root / "template.ass"
            template.write_text(
                "[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n"
                "\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
                "Style: Default,MiSans,50\n\n[Events]\n", encoding="utf-8")
            output = root / "out.ass"
            cmd_make_ass(Namespace(srt=str(srt), template=str(template), output=str(output)))
            text = output.read_text(encoding="utf-8")
            self.assertIn("艺术啊 最简单", text)
            self.assertNotIn("艺术啊，最简单。", text)
            self.assertEqual(text.count("Dialogue:"), 2)

    def test_discovers_episode_outside_original_seven(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "input" / "S01E08.mp4").write_bytes(b"media-placeholder")
            found = discover_episodes("linux", root)
            self.assertIn(8, found)
            self.assertIn(49, found)

    def test_adoption_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            f = root / "input" / "S01E49.mp4"
            f.write_bytes(b"sample-not-real-video")
            data = inventory("linux", root)
            self.assertEqual(data["episodes"]["S01E49"]["ocr_input"]["status"], "detected-not-validated")
            self.assertEqual(f.read_bytes(), b"sample-not-real-video")


if __name__ == "__main__":
    unittest.main()
