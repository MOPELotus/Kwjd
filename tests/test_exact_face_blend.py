"""CPU numeric contracts; never import a face model or CUDA runtime."""
import sys
import unittest
from pathlib import Path
import warnings
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from exact_face_blend import blend_faces


def reference(background, faces, *, convert_output):
    value = background.copy()
    for soft_mask, pasted_face in faces:
        value = soft_mask * pasted_face + (1 - soft_mask) * value
    if convert_output:
        value = value.astype(np.uint16 if np.max(value) > 256 else np.uint8)
    return value


class ExactFaceBlendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rng = np.random.default_rng(20261029)
        cls.background = cls.rng.integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
        cls.face = cls.rng.integers(0, 256, cls.background.shape, dtype=np.uint8)

    def check(self, faces, background=None):
        bg = self.background if background is None else background
        original = bg.tobytes()
        frozen = [(m.tobytes(), f.tobytes()) for m, f in faces]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            for convert in (False, True):
                want = reference(bg, faces, convert_output=convert)
                actual = blend_faces(bg, faces, convert_output=convert)
                self.assertEqual(actual.dtype, want.dtype)
                self.assertEqual(actual.shape, want.shape)
                self.assertEqual(actual.tobytes(), want.tobytes())
                self.assertFalse(np.shares_memory(actual, bg))
        self.assertEqual(bg.tobytes(), original)
        self.assertEqual([(m.tobytes(), f.tobytes()) for m, f in faces], frozen)

    def mask(self, y, x, height, width):
        m = np.zeros((1080, 1920, 1), np.float32)
        m[y:y+height, x:x+width, 0] = self.rng.random((height, width), dtype=np.float32)
        return m

    def test_no_faces_and_all_zero_mask(self):
        self.check([])
        self.check([(np.zeros((1080, 1920, 1), np.float32), self.face)])

    def test_full_and_compact_masks(self):
        self.check([(np.ones((1080, 1920, 1), np.float32), self.face)])
        self.check([(self.mask(300, 700, 250, 250), self.face)])

    def test_edges_and_noncontiguous_arrays(self):
        m = self.mask(0, 0, 240, 250)
        self.check([(m, self.face)])
        self.check([(m[::-1, ::-1], self.face[::-1, ::-1])], self.background[::-1, ::-1])

    def test_ordered_overlapping_and_disjoint_faces(self):
        one = self.mask(250, 400, 300, 300)
        two = self.mask(300, 450, 280, 250)
        three = self.mask(700, 1400, 150, 220)
        self.check([(one, self.face), (two, 255-self.face), (three, self.face)])
        self.check([(two, 255-self.face), (one, self.face)])

    def test_tiny_nonzero_subnormal_is_not_thresholded_away(self):
        mask = np.zeros((1080, 1920, 1), np.float32)
        mask[42, 99, 0] = np.nextafter(np.float32(0), np.float32(1))
        self.check([(mask, self.face)])

    def test_nonfinite_or_unsupported_inputs_use_original_expression(self):
        for bad in (np.nan, np.inf, -np.inf):
            mask = self.mask(5, 5, 20, 20)
            mask[0, 0, 0] = bad
            self.check([(mask, self.face)])
        self.check([(self.mask(5, 5, 20, 20).astype(np.float64), self.face)])
        # Zero times NaN must propagate like the full-frame reference.
        small_bg = np.zeros((3, 4, 3), np.float32)
        small_face = small_bg.copy(); small_face[1, 1, 0] = np.nan
        self.check([(np.zeros((3, 4, 1), np.float32), small_face)], small_bg)
        self.check([(np.full((1080, 1920, 1), 3, np.float32), self.face)])

    def test_cv2_warp_outputs_and_extreme_native_backgrounds(self):
        try: import cv2
        except ImportError: self.skipTest('Existing OpenCV installation required for warp simulation')
        cv2.setNumThreads(4)
        restored = self.rng.integers(0, 256, (512, 512, 3), dtype=np.uint8)
        mask = np.zeros((512, 512), np.float32); mask[25:480, 25:480] = 255
        for _ in range(2): mask = cv2.GaussianBlur(mask, (101, 101), 11)
        mask[:10] = 0; mask[-10:] = 0; mask[:, :10] = 0; mask[:, -10:] = 0
        mask = mask / 255.
        for angle, scale, dx, dy in [(-25, .4, -180, -100), (15, .8, 400, 200),
                                      (30, 1.5, 1300, 750), (0, 1, 700, 250)]:
            matrix = cv2.getRotationMatrix2D((256, 256), angle, scale)
            matrix[:, 2] += [dx, dy]
            face = cv2.warpAffine(restored, matrix, (1920, 1080))
            soft = cv2.warpAffine(mask, matrix, (1920, 1080), flags=3)[:, :, None]
            self.check([(soft, face)])
        for value in (0, 255):
            self.check([(self.mask(0, 0, 120, 180), self.face)],
                       np.full_like(self.background, value))

    def test_mutating_returned_image_does_not_mutate_caller_background(self):
        original = self.background.tobytes()
        value = blend_faces(self.background, [(self.mask(100, 100, 50, 50), self.face)])
        value[:] = 123
        self.assertEqual(self.background.tobytes(), original)


if __name__ == '__main__': unittest.main()
