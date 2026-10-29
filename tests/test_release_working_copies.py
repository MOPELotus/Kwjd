from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_working_copies import bounded_file, plan, execute
from episode_lifecycle import save, sha256


class WorkingCopyReleaseTests(unittest.TestCase):
    def prepare(self, folder):
        base = Path(folder)
        root, originals = base / 'project', base / 'originals'
        (root / 'videos').mkdir(parents=True)
        originals.mkdir()
        original = originals / 'original.mp4'
        original.write_bytes(b'full original')
        trimmed = root / 'videos/trim.mp4'
        trimmed.write_bytes(b'trimmed copy')
        (root / 'videos/fifth.mp4').write_bytes(b'active episode')
        save(root / 'manifests/S01E49.json', {'Episode': 'S01E49', 'InputName': original.name,
             'OutputName': trimmed.name, 'SourceDurationSeconds': 100})
        proof = {'path': str(original), 'sha256': sha256(original), 'bytes': original.stat().st_size}
        with patch('release_working_copies.catalog'), patch('release_working_copies.check_original', return_value=proof):
            report = plan(root, originals, ['S01E49'])
        return root, original, trimmed, report

    def test_releases_only_selected_copy_and_preserves_original_and_fifth(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, trimmed, report = self.prepare(folder)
            execute(report, root / 'audit.json')
            self.assertFalse(trimmed.exists())
            self.assertTrue(original.exists())
            self.assertTrue((root / 'videos/fifth.mp4').exists())

    def test_changed_original_prevents_any_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            root, original, trimmed, report = self.prepare(folder)
            original.write_bytes(b'changed original')
            with self.assertRaisesRegex(ValueError, 'original changed'):
                execute(report, root / 'audit.json')
            self.assertTrue(trimmed.exists())

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'exact manifest filename'):
                bounded_file(folder, '../original.mp4')


if __name__ == '__main__':
    unittest.main()
