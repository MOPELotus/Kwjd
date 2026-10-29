import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import accept_reworked_final as receiver
from episode_lifecycle import load, save


class ReworkedFinalTests(unittest.TestCase):
    def setup_files(self, folder):
        root = Path(folder)
        target = root / 'final/第一回 测试.mkv'
        target.parent.mkdir()
        target.write_bytes(b'old final')
        partial = root / '.kwjd/downloads/new.downloading'
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b'new final')
        state = {'episode': 'S01E49', 'target': str(target), 'partial': str(partial),
                 'source_sha256': receiver.sha256(target)}
        old = {'sha256': state['source_sha256'], 'quality_checked': False}
        save(root / '.kwjd/receipts/S01E49.json', old)
        return root, target, partial, state, old

    def test_publish_archives_old_receipt_without_inheriting_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            root, target, partial, state, old = self.setup_files(folder)
            evidence = {'sha256': receiver.sha256(partial), 'quality_checked': False,
                        'technical_checked': True}
            with patch.object(receiver, 'catalog', return_value={'final_name': target.name}):
                record = receiver.publish(root, state, evidence)
            self.assertEqual(target.read_bytes(), b'new final')
            self.assertFalse(partial.exists())
            self.assertFalse(record['quality_checked'])
            self.assertEqual(load(root / f'.kwjd/receipts/history/S01E49.{old["sha256"]}.json'), old)

    def test_changed_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root, target, partial, state, _ = self.setup_files(folder)
            target.write_bytes(b'user replacement')
            with patch.object(receiver, 'catalog', return_value={'final_name': target.name}):
                with self.assertRaisesRegex(ValueError, 'rejected source'):
                    receiver.publish(root, state, {'sha256': receiver.sha256(partial)})
            self.assertEqual(target.read_bytes(), b'user replacement')
            self.assertTrue(partial.exists())

    def test_changed_download_is_preserved_and_not_published(self):
        with tempfile.TemporaryDirectory() as folder:
            root, target, partial, state, _ = self.setup_files(folder)
            with patch.object(receiver, 'catalog', return_value={'final_name': target.name}):
                with self.assertRaisesRegex(ValueError, 'Staging file changed'):
                    receiver.publish(root, state, {'sha256': hashlib.sha256(b'another file').hexdigest()})
            self.assertEqual(target.read_bytes(), b'old final')


if __name__ == '__main__':
    unittest.main()
