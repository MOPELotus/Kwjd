import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/cut"))
import freeze_known_cuts as freeze
import season_cut as base


class FrozenCatalogTests(unittest.TestCase):
    def test_shipped_catalog_covers_exactly_260_reviewed_known_sources(self):
        catalog_path = base.REPO / "config/cuts/four-seasons-known-v1.json"
        catalog = base.read(catalog_path)
        self.assertTrue(freeze.validate_catalog(catalog))
        validation = base.read(base.REPO / "config/cuts/four-seasons-known-v1.validation.json")
        self.assertEqual(validation["catalog_sha256"], base.digest(catalog_path))
        content_sha = hashlib.sha256(json.dumps(catalog, sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(validation["catalog_content_sha256"], content_sha)
        self.assertEqual(validation["boundary_copy_passed"], 260)
        self.assertEqual(set(validation["episode_receipt_sha256"]),
                         {e["episode"] for e in catalog["entries"]})
        for entry in catalog["entries"]:
            self.assertNotIn("source", entry)  # No device-specific source paths in Git.
            self.assertNotIn("/", entry["source_name"])
            self.assertNotIn("\\", entry["source_name"])

    def test_missing_episode_and_nan_boundary_are_rejected(self):
        catalog = base.read(base.REPO / "config/cuts/four-seasons-known-v1.json")
        broken = copy.deepcopy(catalog)
        broken["entries"].pop()
        with self.assertRaisesRegex(ValueError, "260"):
            freeze.validate_catalog(broken)
        catalog["entries"][0]["first_forbidden_seconds"] = float("nan")
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            freeze.validate_catalog(catalog)

    def test_incomplete_review_never_publishes_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base.save_new(root / "reviews/final/S01.json", dict(episodes_reviewed=1))
            output = root / "frozen.json"
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                freeze.freeze(root, output)
            self.assertFalse(output.exists())

    def test_changed_review_sheet_never_publishes_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sheets").mkdir()
            (root / "sheets/S01.jpg").write_bytes(b"changed")
            base.save_new(root / "reviews/final/S01.json", dict(episodes_reviewed=58,
                          sheets=[dict(name="S01.jpg", sha256="0" * 64)]))
            output = root / "frozen.json"
            with self.assertRaisesRegex(ValueError, "sheet changed"):
                freeze.freeze(root, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
