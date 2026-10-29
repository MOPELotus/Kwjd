from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_local_episode_media import candidates, release
from episode_lifecycle import save, sha256


class LocalMediaReleaseTests(unittest.TestCase):
    def test_only_generated_media_for_selected_episode_is_released(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = ['final/final.mkv', 'videos/S01E49.mp4', 'work/S01E50/test.mkv',
                     'work/S01E49/proof.png', 'previews/E49-test/report.json',
                     'previews/E49-test/sample.mkv', 'work/S01E49/test.wav']
            for name in files:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b'test')
            final = root / files[0]
            save(root / '.kwjd/receipts/S01E49.json',
                 {'episode': 'S01E49', 'received': True, 'technical_checked': True,
                  'local_path': str(final), 'sha256': sha256(final)})
            result = release(root, 'S01E49', True)
            self.assertEqual(result['state'], 'RELEASED')
            self.assertEqual(len(result['entries']), 2)
            for name in files[:5]:
                self.assertTrue((root / name).exists())

    def test_changed_final_prevents_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            final = root / 'final/final.mkv'
            final.parent.mkdir()
            final.write_bytes(b'changed')
            save(root / '.kwjd/receipts/S01E49.json',
                 {'episode': 'S01E49', 'received': True, 'technical_checked': True,
                  'local_path': str(final), 'sha256': 'a' * 64})
            with self.assertRaisesRegex(ValueError, 'final does not match'):
                release(root, 'S01E49', True)


if __name__ == '__main__':
    unittest.main()
