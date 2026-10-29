import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from retire_e49_generated import validate_receipt
from inventory_e49_retirement import allowed_media


class RetirementGates(unittest.TestCase):
    def test_actual_technical_receipt_does_not_need_invented_quality(self):
        ready = dict(episode='S01E49', sha256='new', bytes=123,
                     source_sha256='old', master_sha256='master', decoded_pcm_sha256='pcm',
                     final_path='/project/post/final/S01E49.face-reworked-1080p60.mkv')
        receipt = dict(ready, received=True, technical_checked=True, quality_checked=False,
                       ready_report_sha256='report', remote_final_name=Path(ready['final_path']).name)
        validate_receipt(receipt, ready, 'report')
        self.assertFalse(receipt['quality_checked'])
        receipt['sha256'] = 'old'
        with self.assertRaisesRegex(ValueError, 'sha256 differs'):
            validate_receipt(receipt, ready, 'report')

    def test_started_download_is_not_a_receipt(self):
        with self.assertRaisesRegex(ValueError, 'Actual Windows'):
            validate_receipt({'received': False, 'technical_checked': True}, {}, 'report')

    def test_new_final_original_input_and_other_episode_are_out_of_scope(self):
        root = Path('/project')
        for relative in ('post/final/S01E49.face-reworked-1080p60.mkv', 'input/S01E49.mp4',
                         'post/cache/S01E50/part.mkv', 'ResolveWork/CacheClip/third.mkv'):
            self.assertFalse(allowed_media(root, root / relative))
        self.assertTrue(allowed_media(root, root / 'post/final/S01E49.final-1080p60.mkv'))


if __name__ == '__main__':
    unittest.main()
