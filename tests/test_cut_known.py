import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/cut"))
import cut_known as known


class KnownCutTests(unittest.TestCase):
    def fixture(self, root):
        source = root / "坑王驾到S04E33.fixture.mp4"
        source.write_bytes(b"actual source")
        _, profile, _ = known.base.profile_for(source)
        entry = dict(episode="S04E33", boundary_reviewed=True, source_bytes=13,
                     source_sha256=hashlib.sha256(b"actual source").hexdigest())
        catalog = dict(status="FROZEN_BOUNDARIES_REVIEWED", entries=[entry],
                       cut_policy_sha256={"S04": known.policy_hash(profile)})
        return source, entry, catalog

    def test_only_reviewed_exact_source_and_policy_can_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            source, entry, catalog = self.fixture(Path(folder))
            self.assertEqual(known.select(source, catalog)[0], entry)
            source.write_bytes(b"edited source")
            with self.assertRaisesRegex(ValueError, "Unknown source"):
                known.select(source, catalog)

    def test_incomplete_catalog_different_policy_and_duplicates_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            source, entry, catalog = self.fixture(Path(folder))
            catalog["entries"].append(entry.copy())
            with self.assertRaisesRegex(ValueError, "duplicate"):
                known.select(source, catalog)
            catalog["entries"].pop()
            catalog["cut_policy_sha256"]["S04"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "policy changed"):
                known.select(source, catalog)
            catalog["status"] = "CANDIDATES"
            with self.assertRaisesRegex(ValueError, "not completed"):
                known.select(source, catalog)

    def test_existing_unregistered_media_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, entry, catalog = self.fixture(root)
            entry["review_note"] = "actual boundary test"
            catalog_path = root / "catalog.json"
            known.base.save_new(catalog_path, catalog)
            output = root / "existing.mp4"
            output.write_bytes(b"preserve")
            with self.assertRaisesRegex(ValueError, "Existing media"):
                known.cut_known(source, output, catalog_path)
            self.assertEqual(output.read_bytes(), b"preserve")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_frozen_catalog_cuts_real_media_then_reuses_matching_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "坑王驾到S04E33.fixture.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "12",
                "-c:v", "libx264", "-g", "25", "-bf", "3", "-c:a", "aac", str(source)], check=True)
            _, profile, _ = known.base.profile_for(source)
            entry = dict(episode="S04E33", boundary_reviewed=True,
                source_bytes=source.stat().st_size, source_sha256=known.base.digest(source),
                opening_seconds=2, first_forbidden_seconds=10, keep_through_seconds=8,
                duration_seconds=float(known.base.probe(source)["format"]["duration"]),
                tail_kind="logo_transition", review_note="Synthetic fixture, not programme review")
            catalog = dict(status="FROZEN_BOUNDARIES_REVIEWED", entries=[entry],
                           cut_policy_sha256={"S04": known.policy_hash(profile)})
            catalog_path = root / "catalog.json"
            known.base.save_new(catalog_path, catalog)
            output = root / "out.mp4"
            result = known.cut_known(source, output, catalog_path)
            self.assertFalse(result["boundary_selection_requires_manual_action"])
            self.assertTrue(result["output_visual_review_pending"])
            reused = known.cut_known(source, output, catalog_path)
            self.assertTrue(reused["reused"])
            self.assertEqual(result["output_sha256"], reused["output_sha256"])


if __name__ == "__main__":
    unittest.main()
