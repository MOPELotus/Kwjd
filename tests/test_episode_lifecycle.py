import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import episode_lifecycle as life
import resolve_cache as cache


class LifecycleTests(unittest.TestCase):
    def receipt(self, root, ep='S01E49', master_hash=None):
        final = root / 'final' / '第一回 测试标题.mkv'
        final.parent.mkdir(exist_ok=True)
        final.write_bytes(b'verified-final')
        result = {'schema_version': 1, 'episode': ep, 'received': True,
                  'technical_checked': True, 'quality_checked': True,
                  'local_path': str(final), 'sha256': life.sha256(final),
                  'remote_final_name': f'{ep}.final-1080p60.mkv',
                  'master_sha256': master_hash or 'a' * 64}
        life.save(life.receipt_path(root, ep), result)
        return life.receipt_path(root, ep)

    def test_story_number_is_not_season_episode(self):
        meta = life.metadata_from_name('坑王驾到S01E49. 2017-08-20丨卿卿误我（一）家门遭横祸，俊才跌凡尘.mp4', 'S01E49')
        self.assertEqual(meta['final_name'], '第一回 家门遭横祸，俊才跌凡尘.mkv')
        other = life.metadata_from_name('节目S01E108.丨其他故事（十二）后续标题.mp4', 'S01E108')
        self.assertEqual(other['final_name'], '第十二回 后续标题.mkv')
        with self.assertRaises(ValueError):
            life.metadata_from_name('S01E49.mp4', 'S01E49')

    def test_receipt_quality_and_final_hash_gate_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / 'masters/S01E49.clean-master.mov'
            master.parent.mkdir()
            master.write_bytes(b'master')
            receipt = self.receipt(root, master_hash=life.sha256(master))
            life.register_artifact(root, 'S01E49', 'windows', 'masters/S01E49.clean-master.mov')
            data = life.load(receipt)
            data['quality_checked'] = False
            life.save(receipt, data)
            with self.assertRaises(ValueError):
                life.cleanup(root, 'S01E49', 'windows', receipt, True)
            self.assertTrue(master.exists())
            data['quality_checked'] = True
            life.save(receipt, data)
            Path(data['local_path']).write_bytes(b'corrupted-final')
            with self.assertRaises(ValueError):
                life.cleanup(root, 'S01E49', 'windows', receipt, True)
            self.assertTrue(master.exists())

    def test_plan_is_non_destructive_and_changed_master_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / 'masters/S01E49.clean-master.mov'
            master.parent.mkdir()
            master.write_bytes(b'old-master')
            receipt = self.receipt(root, master_hash=life.sha256(master))
            life.register_artifact(root, 'S01E49', 'windows', 'masters/S01E49.clean-master.mov')
            result = life.cleanup(root, 'S01E49', 'windows', receipt)
            self.assertEqual(result['state'], 'PLAN')
            self.assertTrue(master.exists())
            master.write_bytes(b'new-same-name-master')
            with self.assertRaises(ValueError):
                life.cleanup(root, 'S01E49', 'windows', receipt, True)
            self.assertEqual(master.read_bytes(), b'new-same-name-master')

    def test_cleanup_preserves_final_sources_and_other_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / 'masters/S01E49.clean-master.mov'
            master.parent.mkdir()
            master.write_bytes(b'current-master')
            other = root / 'masters/S01E50.clean-master.mov'
            other.write_bytes(b'other')
            source = root / 'videos/S01E49.mp4'
            source.parent.mkdir()
            source.write_bytes(b'source')
            receipt = self.receipt(root, master_hash=life.sha256(master))
            life.register_artifact(root, 'S01E49', 'windows', 'masters/S01E49.clean-master.mov')
            life.cleanup(root, 'S01E49', 'windows', receipt, True)
            self.assertFalse(master.exists())
            self.assertTrue(other.exists())
            self.assertTrue(source.exists())
            self.assertTrue(Path(life.load(receipt)['local_path']).exists())

    def test_unsafe_or_shared_artifacts_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for relative in ('../outside', 'final/keep.mkv', 'styles', 'work', 'videos/S01E49.mp4',
                             'masters/S01E50.clean-master.mov', '/absolute'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    life.artifact_path(Path(tmp), 'S01E49', 'windows', relative)

    def test_linux_storage_symlink_base_is_supported_but_child_links_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / 'alternate-storage'
            actual.mkdir()
            try:
                (root / 'post').symlink_to(actual, target_is_directory=True)
            except OSError:
                self.skipTest('Symlink creation is not permitted')
            incoming = actual / 'incoming'
            incoming.mkdir()
            self.assertEqual(life.artifact_path(root, 'S01E49', 'linux', 'incoming/S01E49.clean-master.mov').resolve(),
                             incoming / 'S01E49.clean-master.mov')
            (actual / 'cache').mkdir()
            (actual / 'cache/S01E49').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                life.artifact_path(root, 'S01E49', 'linux', 'cache/S01E49')

    def test_gate_next_requires_local_release_but_not_windows_final_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            life.save(root / '.kwjd/uploads/S01E49.json',
                      {'verified': True, 'local_master_deleted': True, 'sha256': 'a' * 64})
            with self.assertRaises(FileNotFoundError):
                life.gate_next(root, 'S01E50')
            life.save(root / '.kwjd/cache-cleanup/S01E49.json',
                      {'state': 'CACHE_CLEANED', 'master_sha256': 'a' * 64})
            life.gate_next(root, 'S01E50')

    def test_cache_snapshot_detects_new_mask_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            frame = folder / 'generated-frame.bin'
            frame.write_bytes(b'old')
            baseline = cache.snapshot(folder)
            frame.write_bytes(b'new-content')
            with self.assertRaises(ValueError):
                cache.check_snapshot(folder, baseline)

    def test_uploaded_master_delete_requires_matching_remote_and_local_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / 'masters/S01E49.clean-master.mov'
            master.parent.mkdir()
            master.write_bytes(b'export')
            digest = life.sha256(master)
            record = {'episode': 'S01E49', 'verified': True, 'sha256': digest,
                      'local_path': str(master), 'remote_path': 'qingqingwu/post/incoming/S01E49.clean-master.mov',
                      'ssh_alias': 'test-host'}
            life.save(root / '.kwjd/uploads/S01E49.json', record)
            with patch.object(cache.subprocess, 'check_output', return_value='b' * 64 + ' master'):
                with self.assertRaises(ValueError):
                    cache.delete_uploaded_master(root, 'S01E49')
            self.assertTrue(master.exists())
            with patch.object(cache.subprocess, 'check_output', return_value=digest + ' master'):
                cache.delete_uploaded_master(root, 'S01E49')
            self.assertFalse(master.exists())


if __name__ == '__main__':
    unittest.main()
