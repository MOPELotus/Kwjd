import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from bootstrap_face_runtime import ensure_asset
from verify_clean_recovery import resume_area


class RecoveryResumeTests(unittest.TestCase):
    def test_verified_download_is_reused_but_corrupt_download_is_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'model.pth';good=b'verified synthetic model'
            digest=hashlib.sha256(good).hexdigest()
            def bad_download(command,**kwargs):
                Path(command[command.index('-o')+1]).write_bytes(b'incomplete')
            with patch('bootstrap_face_runtime.subprocess.run',side_effect=bad_download):
                with self.assertRaises(ValueError):ensure_asset(target,'https://example.invalid/model',digest)
            self.assertFalse(target.exists());self.assertTrue(target.with_suffix('.pth.downloading').exists())
            target.write_bytes(good)
            with patch('bootstrap_face_runtime.subprocess.run') as download:
                ensure_asset(target,'https://example.invalid/model',digest)
            download.assert_not_called();self.assertEqual(target.read_bytes(),good)

    def test_only_the_owned_retained_test_directory_can_resume(self):
        with tempfile.TemporaryDirectory(prefix='kwjd-fixed-clean-check-',dir='/var/tmp') as temporary:
            area=Path(temporary);root=Path('/home/lotus/qingqingwu')
            (area/'owner.json').write_text(json.dumps(dict(token='synthetic-owner',root=str(root))))
            state=dict(test_root=str(area),temporary_artifact_manifest={'owner_token':'synthetic-owner'})
            self.assertEqual(resume_area(root,state),(area,'synthetic-owner'))
            state['temporary_artifact_manifest']['owner_token']='wrong-owner'
            with self.assertRaises(ValueError):resume_area(root,state)


if __name__=='__main__':unittest.main()
