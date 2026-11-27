import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from episode_lifecycle import load, save, sha256
from promote_publishing_final import promote


class PromotePublishingFinal(unittest.TestCase):
    def prepare(self, root):
        body = root / 'final/chapter.mkv'
        pub = root / 'publish/chapter.mkv'
        for p, data in ((body, b'body'), (pub, b'intro-and-body')):
            p.parent.mkdir(parents=True)
            p.write_bytes(data)
        save(root / '.kwjd/catalog/S03E02.json', dict(episode='S03E02', final_name='chapter.mkv'))
        save(root / '.kwjd/receipts/S03E02.json', dict(sha256=sha256(body), received=True,
             technical_checked=True, quality_checked=True, windows_cleanup_published=True,
             local_path=str(body)))
        save(root / '.kwjd/publishing/S03E02.with-intro.json', dict(
             state='PUBLISHING_COPY_VERIFIED', intro_seconds=27, body_path=str(body),
             body_sha256=sha256(body), path=str(pub), sha256=sha256(pub),
             body_coded_picture_payloads_identical=True, body_pcm_identical=True,
             join_and_first_middle_end_decode_passed=True))
        return body, pub

    def test_promotes_intro_and_preserves_body_receipt_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body, pub = self.prepare(root)
            body_hash = sha256(body)
            result = promote(root)
            self.assertEqual(body.read_bytes(), b'intro-and-body')
            self.assertFalse(pub.exists())
            receipt = load(root / '.kwjd/receipts/S03E02.json')
            self.assertEqual(receipt['sha256'], body_hash)
            self.assertEqual(Path(receipt['local_path']).read_bytes(), b'body')
            self.assertEqual(promote(root), result)

    def test_changed_publishing_copy_leaves_both_inputs_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body, pub = self.prepare(root)
            pub.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                promote(root)
            self.assertEqual(body.read_bytes(), b'body')
            self.assertEqual(pub.read_bytes(), b'changed')


if __name__ == '__main__':
    unittest.main()
