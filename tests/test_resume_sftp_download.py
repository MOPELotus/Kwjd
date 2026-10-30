import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resume_sftp_download import validate, watch


class ResumeTests(unittest.TestCase):
    def test_stalled_owned_process_exits_and_partial_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'partial'
            target.write_bytes(b'valid prefix')
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                self.assertEqual(watch(child, target, .15, .03), 'STALLED')
                self.assertIsNotNone(child.poll())
                self.assertEqual(target.read_bytes(), b'valid prefix')
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_failed_exit_does_not_imply_complete_download(self):
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(3)'])
            self.assertEqual(watch(child, Path(folder) / 'absent', 10, .03), 'EXITED_FAILED')

    def test_rejects_wrong_hash_name_and_remote_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            digest = 'a' * 64
            target = Path(folder) / (digest + '.downloading')
            validate('TodayEatUbuntu-7K62', 'qingqingwu/post/final/S01E50.mkv', target, digest)
            with self.assertRaises(ValueError):
                validate('host', '../other', target, digest)
            with self.assertRaises(ValueError):
                validate('host', 'safe', Path(folder) / 'final.mkv', digest)


if __name__ == '__main__':
    unittest.main()
