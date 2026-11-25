import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from subtitle_review_storage import require_space


class EvidenceStorageTests(unittest.TestCase):
    def test_relocated_evidence_is_budgeted_on_real_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'other-volume';target.mkdir();link=root/'review';link.symlink_to(target,target_is_directory=True)
            def free(path):
                self.assertEqual(path,target)
                return SimpleNamespace(free=6*1024**3)
            with patch('subtitle_review_storage.shutil.disk_usage',side_effect=free):
                with self.assertRaisesRegex(RuntimeError,'reserve'):require_space(link/'new-scan',2)

    def test_full_workspace_does_not_block_roomy_evidence_volume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'other-volume';target.mkdir();link=root/'review';link.symlink_to(target,target_is_directory=True)
            with patch('subtitle_review_storage.shutil.disk_usage',return_value=SimpleNamespace(free=9*1024**3)) as usage:
                self.assertEqual(require_space(link/'nested/new-scan',3),9*1024**3)
                usage.assert_called_once_with(target)


if __name__=='__main__':unittest.main()
