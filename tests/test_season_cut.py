import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("season_cut", Path(__file__).resolve().parents[1] / "scripts/cut/season_cut.py")
cut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cut)


class SeasonCutTests(unittest.TestCase):
    def test_refuse_nan_and_closing_shot_lost_to_guard(self):
        for args in [(float("nan"), 10, 20, 30, .25), (1, 19.5, 20, 30, .25),
                     (1, 10, 31, 30, .25), (5, 2, 20, 30, .25)]:
            with self.assertRaises(ValueError):
                cut.validate_bounds(*args)

    def test_each_season_has_own_profile_and_s01_legacy_unchanged(self):
        for season in range(1, 5):
            _, profile, _ = cut.profile_for(f"坑王驾到S0{season}E01.example.mp4")
            self.assertTrue(profile["cut_review"]["source_sha256_bound_review_required"])
            self.assertIn("clean_eof", profile["cut_review"]["tail_kinds"])
        self.assertEqual(cut.read(cut.REPO / "config/seasons/S01.json")["cut"]["tail"], "first_white_frame")
        with self.assertRaises(ValueError):
            cut.profile_for("S05E01.mp4")

    def test_source_sha_mismatch_fails_before_any_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "坑王驾到S03E20.example.mp4"
            source.write_bytes(b"changed original")
            episode, _, sha = cut.profile_for(source)
            plan = root / "plan.json"
            cut.save_new(plan, dict(source=str(source), episode=episode,
                                   profile_sha256=sha, source_sha256="0" * 64))
            with self.assertRaisesRegex(ValueError, "Source or audit changed"):
                cut.cut(plan, root / "output.mp4")
            self.assertFalse((root / "output.mp4").exists())

    def test_receipts_and_plans_never_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            cut.save_new(path, {"old": True})
            with self.assertRaises(FileExistsError):
                cut.save_new(path, {"old": False})
            self.assertEqual(cut.read(path), {"old": True})

    def test_invalid_json_never_publishes_partial_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            with self.assertRaises(ValueError):
                cut.save_new(path, {"invalid": float("nan")})
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_bframe_stream_copy_preserves_bounds_and_refuses_stale_or_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "坑王驾到S03E20.fixture.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "12",
                "-c:v", "libx264", "-g", "25", "-bf", "3", "-c:a", "aac", str(source)], check=True)
            audit_dir = root / "audit"
            cut.inspect(source, audit_dir, [1, 1.5, 2, 8, 8.5, 9, 9.5, 10])
            plan_path = root / "plan.json"
            with self.assertRaisesRegex(ValueError, "clean_eof"):
                cut.make_plan(audit_dir / "audit.json", 1.5, 8, 10, "clean_eof", "test", plan_path)
            cut.make_plan(audit_dir / "audit.json", 1.5, 8, 10, "logo_transition",
                          "Generated fixture, no real programme quality claim", plan_path)
            output = root / "out.mp4"
            receipt = cut.cut(plan_path, output)
            self.assertGreaterEqual(receipt["actual_start_seconds"], 1.5)
            self.assertLessEqual(receipt["video_source_end_seconds"], 9.75)
            self.assertGreaterEqual(receipt["video_source_end_seconds"], 8)
            self.assertTrue(receipt["output_visual_review_pending"])
            with self.assertRaisesRegex(ValueError, "new MP4"):
                cut.cut(plan_path, output)
            (audit_dir / "audit.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "audit changed"):
                cut.cut(plan_path, root / "another.mp4")


if __name__ == "__main__":
    unittest.main()
