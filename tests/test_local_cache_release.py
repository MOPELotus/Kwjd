import sys
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import resolve_cache
from episode_lifecycle import save


class LocalCacheReleaseTests(unittest.TestCase):
    def test_later_upload_does_not_touch_new_episode_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save(root / '.kwjd/uploads/S01E50.json',
                 {'episode': 'S01E50', 'verified': True, 'sha256': 'a' * 64})
            save(root / '.kwjd/cache-cleanup/S01E50.local-anchor.json',
                 {'state': 'LOCAL_ANCHORED_CACHE_CLEANED', 'master_sha256': 'a' * 64})
            with patch.object(resolve_cache, 'snapshot', side_effect=AssertionError('New cache touched')):
                result = resolve_cache.cleanup(root, 'S01E50', execute=True)
            self.assertEqual(result['state'], 'CACHE_ALREADY_RELEASED_NEW_CACHE_RETAINED')

    def test_different_master_cannot_reuse_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save(root / '.kwjd/uploads/S01E50.json',
                 {'episode': 'S01E50', 'verified': True, 'sha256': 'b' * 64})
            save(root / '.kwjd/cache-cleanup/S01E50.local-anchor.json',
                 {'state': 'LOCAL_ANCHORED_CACHE_CLEANED', 'master_sha256': 'a' * 64})
            with self.assertRaisesRegex(ValueError, 'CacheClip path'):
                resolve_cache.cleanup(root, 'S01E50', execute=True)

    def test_local_release_requires_explicit_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save(root / '.kwjd/cache-local-approvals/S01E50.json',
                 {'episode': 'S01E50', 'authorized': False})
            with self.assertRaisesRegex(ValueError, 'authorization'):
                resolve_cache.cleanup_local_anchor(root, 'S01E50', execute=True)

    def test_repeated_local_release_leaves_new_cache_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master = root / 'masters/S01E50.clean-master.mov'
            master.parent.mkdir()
            master.write_bytes(b'verified master')
            digest = hashlib.sha256(master.read_bytes()).hexdigest()
            save(root / '.kwjd/cache-local-approvals/S01E50.json',
                 {'episode': 'S01E50', 'authorized': True, 'master_sha256': digest})
            save(root / '.kwjd/e50-export-handoff.json',
                 {'path': str(master), 'sha256': digest, 'sampled_av_decode_passed': True,
                  'duration_gate_passed': True})
            save(root / '.kwjd/cache-cleanup/S01E50.local-anchor.json',
                 {'state': 'LOCAL_ANCHORED_CACHE_CLEANED', 'master_sha256': digest})
            with patch.object(resolve_cache, 'snapshot', side_effect=AssertionError('New cache touched')):
                result = resolve_cache.cleanup_local_anchor(root, 'S01E50', execute=True)
            self.assertEqual(result['state'], 'CACHE_ALREADY_RELEASED_NEW_CACHE_RETAINED')


if __name__ == '__main__':
    unittest.main()
