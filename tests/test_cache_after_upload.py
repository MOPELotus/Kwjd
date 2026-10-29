import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cache_after_upload import previous_released
from episode_lifecycle import save
from resolve_cache import snapshot, check_snapshot


class DeferredCacheReleaseTests(unittest.TestCase):
    def test_previous_master_must_be_verified_and_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master = root / 'master.mov'
            receipt = {'episode': 'S01E50', 'verified': True, 'local_master_deleted': True,
                       'sha256': 'a' * 64, 'local_path': str(master)}
            save(root / '.kwjd/uploads/S01E50.json', receipt)
            save(root / '.kwjd/handoffs/S01E50.upload.json', receipt)
            previous_released(root, 'S01E50', 'a' * 64)
            master.write_bytes(b'still local')
            with self.assertRaisesRegex(ValueError, 'remains local'):
                previous_released(root, 'S01E50', 'a' * 64)

    def test_wrong_hash_cannot_release_next_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            receipt = {'episode': 'S01E50', 'verified': True,
                       'local_master_deleted': True, 'sha256': 'b' * 64}
            save(root / '.kwjd/uploads/S01E50.json', receipt)
            save(root / '.kwjd/handoffs/S01E50.upload.json', receipt)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                previous_released(root, 'S01E50', 'a' * 64)

    def test_new_mask_cache_invalidates_original_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'old.cache').write_bytes(b'old')
            frozen = snapshot(root)
            (root / 'new.cache').write_bytes(b'new episode')
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_snapshot(root, frozen)
            self.assertTrue((root / 'new.cache').exists())


if __name__ == '__main__':
    unittest.main()
