import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/cut"))
HAS_CV = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None
if HAS_CV:
    import build_known_cuts as scan
    import numpy as np


@unittest.skipUnless(HAS_CV, "Survey requires OpenCV and NumPy")
class DetectorTests(unittest.TestCase):
    def test_single_bright_or_similar_frame_cannot_trigger_logo(self):
        self.assertIsNone(scan.first_run([.1, .99, .2, .98, .1], .8, 3))
        self.assertEqual(scan.first_run([.1, .81, .98, .9, .2], .8, 3), 1)

    def test_black_or_unrelated_stage_does_not_match_template(self):
        rng = np.random.default_rng(11)
        ref = rng.integers(0, 255, (55, 70), dtype=np.uint8)
        unrelated = rng.integers(0, 255, (5, 90, 160), dtype=np.uint8)
        unrelated[0] = 0
        scores = scan.match_scores(unrelated, ref, "S03")
        self.assertTrue(np.all(scores < .8))
        unrelated[2, 20:75, 45:115] = ref
        self.assertGreater(scan.match_scores(unrelated, ref, "S03")[2], .99)

    def test_library_requires_complete_known_numbering(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Missing/unexpected"):
                scan.discover({"S01": Path(folder)})


if __name__ == "__main__":
    unittest.main()
