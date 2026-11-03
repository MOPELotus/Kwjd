import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('review',Path(__file__).parents[1]/'scripts/batch_review_backgrounds.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)


class BatchReviewTests(unittest.TestCase):
    def test_frame_rate_does_not_trigger_encoding(self):
        for fps in ['24/1','25/1','30/1','60/1']:
            self.assertTrue(review.standard_picture(dict(width=1280,height=720,avg_frame_rate=fps)))

    def test_nonstandard_crop_or_non_square_pixels_not_passed(self):
        self.assertFalse(review.standard_picture(dict(width=1920,height=992)))
        self.assertFalse(review.standard_picture(dict(width=1920,height=1080,sample_aspect_ratio='4:3')))

    def test_legacy_schema_does_not_get_lost(self):
        self.assertEqual(review.prior_verdict('abc',dict(disposition='SCENERY_CANDIDATE'))[0],'CLEAN_CANDIDATE')
        self.assertEqual(review.prior_verdict('abc',dict(state='CHARACTER_SCENERY_NEEDS_CROP'))[0],'EXCLUDE')

    def test_unknown_legacy_state_stays_pending(self):
        self.assertEqual(review.prior_verdict('abc',{})[0],'REVIEW_REQUIRED')


if __name__=='__main__':unittest.main()
