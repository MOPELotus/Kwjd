"""Regress composited scenery contamination without weakening clock gates."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_video_clock as clock


class NativeClockTests(unittest.TestCase):
    def check_synthetic(self, matching_index=5, detected_anchors=None):
        rng = np.random.default_rng(263)
        source = np.full((540, 960), 180, dtype=np.uint8)
        face = rng.integers(20, 220, size=(60, 60), dtype=np.uint8)
        source[130:190, 330:390] = face
        candidates = np.full((11, 540, 960), 30, dtype=np.uint8)
        for index in range(11):
            candidates[index, 130:190, 330:390] = rng.integers(
                20, 220, size=(60, 60), dtype=np.uint8)
        candidates[matching_index, 130:190, 330:390] = face
        detector = Mock()
        box = np.array([[300, 100, 120, 120]])
        detector.detectMultiScale.side_effect = [
            box if detected_anchors is None or i in detected_anchors else []
            for i in range(11)]

        def decode(command):
            return (source if command[command.index('-frames:v') + 1] == '1'
                    else candidates).tobytes()

        with patch.object(clock.cv2, 'CascadeClassifier', return_value=detector), \
                patch.object(clock.subprocess, 'check_output', side_effect=decode):
            return clock.check(Path('source.mp4'), Path('master.mov'), -.093)

    def test_matching_face_survives_different_surrounding_scenery(self):
        result = self.check_synthetic()
        self.assertTrue(result['verified'])
        self.assertTrue(all(row['best_index'] == 5 and row['match']
                            for row in result['rows']))

    def test_face_match_at_wrong_native_time_still_fails(self):
        result = self.check_synthetic(matching_index=0)
        self.assertFalse(result['verified'])
        self.assertTrue(all(row['best_index'] == 0 and not row['match']
                            for row in result['rows']))

    def test_three_matching_anchors_without_program_coverage_fail(self):
        result = self.check_synthetic(detected_anchors={0, 1, 2})
        self.assertEqual(len(result['rows']), 3)
        self.assertTrue(all(row['match'] for row in result['rows']))
        self.assertFalse(result['verified'])


if __name__ == '__main__':
    unittest.main()
