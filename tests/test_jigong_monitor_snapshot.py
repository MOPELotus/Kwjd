import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jigong_monitor_snapshot import segment_stats


class ProgressSnapshot(unittest.TestCase):
    def test_only_matching_segment_receipts_contribute(self):
        with tempfile.TemporaryDirectory() as tmp:
            post = Path(tmp)
            folder = post / 'cache/S03E02/S03E02.segments/test'
            folder.mkdir(parents=True)
            identity = dict(episode='S03E02', master_sha256='a'*64,
                            backend_fingerprint='b'*64, input_frames=300,
                            segment_source_frames=250)
            (folder / 'context.json').write_text(json.dumps(identity))
            (folder / '000000.ready.json').write_text(json.dumps(dict(
                **identity, index=0, source_start=0, source_frames=250)))
            (folder / '000001.ready.json').write_text(json.dumps(dict(
                **{**identity, 'master_sha256': 'wrong'}, index=1, source_start=250, source_frames=50)))
            (post / 'logs').mkdir()
            (post / 'logs/S03E02.log').write_text('\n'.join(json.dumps(r) for r in [
                dict(episode='S03E02', source_start=250, processed=10, total=50, elapsed=2),
                dict(episode='S03E02', source_start=55555, processed=40, total=900, elapsed=1)]))
            result = segment_stats(post, 'S03E02', {'master_sha256': 'a'*64})
            self.assertEqual(result['verified_frames'], 250)
            self.assertEqual(result['verified_segments'], 1)
            self.assertAlmostEqual(result['percent'], 83.33)
            self.assertEqual(result['running_frames'], 260)
            self.assertEqual(result['fps'], 5)
            self.assertIsNone(segment_stats(post, 'S03E02', {'master_sha256': 'other'})['percent'])

    def test_missing_progress_is_unknown_not_fake_zero_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(segment_stats(Path(tmp), 'S03E02', {})['percent'])


if __name__ == '__main__':
    unittest.main()
