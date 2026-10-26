import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_trt_backend as prepare


class PrepareTrtTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name);(self.post/'backend').mkdir();(self.post/'reports').mkdir()
        python=self.post/'cuda-venv/bin/python';python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nexit 99\n');python.chmod(0o700)
        self.asset=self.post/'asset';self.asset.write_bytes(b'asset')
        self.base={'rife_backend':'cuda_onnx','restore_strength':.35,'pipeline_prefetch':True,
                   'file_sha256':{str(self.asset):prepare.sha256(self.asset)}}
        self.additions={'rife_trt_engine':str(self.asset),'rife_trt_model':str(self.asset),
                        'rife_trt_provenance':str(self.asset),'file_sha256':{}}
        self.save()

    def save(self):
        (self.post/'backend/runtime.cuda.json').write_text(json.dumps(self.base))
        (self.post/'reports/rife_trt_config_additions.json').write_text(json.dumps(self.additions))

    def test_candidate_merge_is_inactive_and_idempotent(self):
        active=self.post/'backend/enhance.sh';active.write_bytes(b'previous launcher')
        with patch.object(prepare,'validate_assets') as validate:
            result=prepare.prepare(self.post)
            profile=Path(result['profile']);launcher=Path(result['launcher'])
            before=(profile.stat().st_ino,launcher.stat().st_ino)
            self.assertEqual(prepare.prepare(self.post),result)
            self.assertEqual(before,(profile.stat().st_ino,launcher.stat().st_ino))
            self.assertEqual(validate.call_count,2)
        cfg=json.loads(profile.read_text())
        self.assertEqual(cfg['rife_backend'],'cuda_trt');self.assertEqual(cfg['restore_strength'],.35)
        self.assertTrue(cfg['pipeline_prefetch']);self.assertFalse(result['activated'])
        self.assertIn('segmented_backend.py',launcher.read_text());self.assertTrue(os.access(launcher,os.X_OK))
        self.assertEqual(active.read_bytes(),b'previous launcher')
        self.assertFalse((self.post/'backend/production-ready.json').exists())

    def test_changed_asset_rejected_before_validator_or_outputs(self):
        self.asset.write_bytes(b'changed')
        with patch.object(prepare,'validate_assets') as validate,self.assertRaisesRegex(ValueError,'Pinned asset'):
            prepare.prepare(self.post)
        validate.assert_not_called();self.assertFalse((self.post/'backend/runtime.trt.json').exists())

    def test_conflicting_pin_and_unexpected_overrides_rejected(self):
        self.additions['file_sha256'][str(self.asset)]='0'*64;self.save()
        with self.assertRaisesRegex(ValueError,'Conflicting'):prepare.prepare(self.post)
        self.additions['file_sha256']={};self.additions['restore_strength']=0;self.save()
        with self.assertRaisesRegex(ValueError,'only the reviewed'):prepare.prepare(self.post)

    def test_differing_launcher_prevents_either_publication(self):
        launcher=self.post/'backend/enhance.trt.sh';launcher.write_bytes(b'existing candidate')
        with patch.object(prepare,'validate_assets'),self.assertRaisesRegex(ValueError,'never overwrite'):
            prepare.prepare(self.post)
        self.assertEqual(launcher.read_bytes(),b'existing candidate')
        self.assertFalse((self.post/'backend/runtime.trt.json').exists())

    def test_failed_provenance_validation_prevents_publication(self):
        with patch.object(prepare,'validate_assets',side_effect=ValueError('provenance differs')),self.assertRaisesRegex(ValueError,'provenance'):
            prepare.prepare(self.post)
        self.assertFalse((self.post/'backend/runtime.trt.json').exists())


if __name__=='__main__':unittest.main()
