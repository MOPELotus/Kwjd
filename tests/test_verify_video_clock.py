"""Regress composited scenery contamination without weakening clock gates."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_video_clock as clock


class NativeClockTests(unittest.TestCase):
    def check_synthetic(self, matching_index=5, detected_anchors=None, master_rate=25):
        rng = np.random.default_rng(263)
        source = np.full((540, 960), 180, dtype=np.uint8)
        face = rng.integers(20, 220, size=(60, 60), dtype=np.uint8)
        source[130:190, 330:390] = face
        window_count = master_rate // 5 * 2 + 1
        candidates = np.full((window_count, 540, 960), 30, dtype=np.uint8)
        for index in range(window_count):
            candidates[index, 130:190, 330:390] = rng.integers(
                20, 220, size=(60, 60), dtype=np.uint8)
        candidates[matching_index, 130:190, 330:390] = face
        detector = Mock()
        detector.empty.return_value = False
        box = np.array([[300, 100, 120, 120]])
        detector.detectMultiScale.side_effect = [
            box if detected_anchors is None or i in detected_anchors else []
            for i in range(11)]

        def probe(command):
            rate = master_rate if command[-1] == 'master.mov' else 25
            return ('{"streams":[{"avg_frame_rate":"%s/1","r_frame_rate":"%s/1"}]}' % (rate, rate)).encode()

        def decode(path,start,count):
            if count==1:return source[None], [start]
            self.assertEqual(count,window_count)
            return candidates,[start+i/master_rate for i in range(count)]

        with patch.object(clock.cv2, 'CascadeClassifier', return_value=detector), \
                patch.object(clock.subprocess, 'check_output', side_effect=probe), \
                patch.object(clock, 'decode_frames', side_effect=decode):
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

    def test_30fps_master_uses_sixth_frame_for_25fps_source_anchor(self):
        result = self.check_synthetic(matching_index=6, master_rate=30)
        self.assertTrue(result['verified'])
        self.assertEqual(result['source_frame_rate'], 25)
        self.assertEqual(result['master_frame_rate'], 30)
        self.assertTrue(all(row['expected_index'] == 6 and row['match'] for row in result['rows']))

    def test_30fps_drift_over_one_source_frame_is_rejected(self):
        result = self.check_synthetic(matching_index=4, master_rate=30)
        self.assertFalse(result['verified'])

    def test_decoded_pts_keep_seek_rounding_instead_of_inventing_grid(self):
        output=SimpleNamespace(stdout=np.zeros((2,540,960),dtype=np.uint8).tobytes(),
            stderr=b'config in time_base: 1/15360\nn: 0 pts: 917504 pts_time:59.7333\nn: 1 pts: 918016 pts_time:59.7667\n')
        with patch.object(clock.subprocess,'run',return_value=output):
            images,pts=clock.decode_frames(Path('master.mov'),59.704,2)
        self.assertEqual(len(images),2)
        self.assertAlmostEqual(pts[0],917504/15360)
        self.assertAlmostEqual(pts[1],918016/15360)
        output.stderr=b'config in time_base: 1/15360\nn: 0 pts: 917504 pts_time:59.7333\n'
        with patch.object(clock.subprocess,'run',return_value=output):
            with self.assertRaises(ValueError):clock.decode_frames(Path('master.mov'),59.704,2)


if __name__ == '__main__':
    unittest.main()
