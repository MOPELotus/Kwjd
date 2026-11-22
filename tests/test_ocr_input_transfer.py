import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/receive_ocr_input.py'


class OCRTransferTests(unittest.TestCase):
    def invoke(self,root,data,finalize=False):
        command=[sys.executable,str(SCRIPT),'--root',str(root),'--episode','S03E02',
                 '--sha256',hashlib.sha256(data).hexdigest()]
        return subprocess.run(command+(['--finalize'] if finalize else []),capture_output=True)

    def test_publish_only_matching_partial_and_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'input').mkdir()
            partial=root/'input/S03E02.mp4.uploading'
            partial.write_bytes(b'test transport content')
            self.assertEqual(self.invoke(root,b'test transport content',True).returncode,0)
            self.assertFalse(partial.exists())
            self.assertTrue((root/'input/S03E02.mp4.transfer.json').is_file())
            self.assertEqual(self.invoke(root,b'test transport content').returncode,0)

    def test_changed_existing_final_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'input').mkdir()
            final=root/'input/S03E02.mp4'; final.write_bytes(b'existing')
            self.assertNotEqual(self.invoke(root,b'other',True).returncode,0)
            self.assertEqual(final.read_bytes(),b'existing')

    def test_bad_partial_is_not_published(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'input').mkdir()
            partial=root/'input/S03E02.mp4.uploading'; partial.write_bytes(b'incomplete')
            self.assertNotEqual(self.invoke(root,b'complete',True).returncode,0)
            self.assertTrue(partial.exists())
            self.assertFalse((root/'input/S03E02.mp4').exists())


if __name__=='__main__':
    unittest.main()
