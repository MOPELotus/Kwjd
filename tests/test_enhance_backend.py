from fractions import Fraction
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import enhance_backend as backend
import server_worker as worker
from master_policy import reject_retired, RETIRED_MASTER_SHA256
from enhance_stage import sha256, GIB


class BackendTests(unittest.TestCase):
    def test_native_scheduling_rejects_invalid_options_before_model_hash_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'runtime.json'
            path.write_text(json.dumps(dict(restoration_backend='cuda_gfpgan_fused',
                                            pipeline_prefetch_frames=3)))
            with patch.object(backend,'sha256') as hashing:
                with self.assertRaisesRegex(ValueError,'prefetch_frames'):
                    backend.config(path)
                hashing.assert_not_called()

    def test_known_retired_hash_rejected_without_local_policy(self):
        with self.assertRaisesRegex(ValueError,'Retired master'):
            reject_retired(next(iter(RETIRED_MASTER_SHA256)))

    def test_rational_clock_has_no_cumulative_rounding(self):
        self.assertEqual(backend.frame_position(12),(5,Fraction(0)))
        self.assertEqual(backend.frame_position(1),(0,Fraction(5,12)))
        self.assertEqual(backend.output_count(87939),211054)
        for n in range(1,100):
            frames=backend.output_count(n)
            self.assertLess(abs(frames/60-n/25),1/60+.000001)
            self.assertEqual(backend.frame_position(frames-1)[0],n-1)

    def test_packet_clock_rejects_vfr_gaps_and_duplicates(self):
        with patch.object(backend.subprocess,'check_output',return_value='0.080\n0\n0.040\n'):
            self.assertEqual(backend.source_packet_count('master'),3)
        for pts in ['0\n0.080\n','0\n0\n']:
            with patch.object(backend.subprocess,'check_output',return_value=pts),self.assertRaises(ValueError):
                backend.source_packet_count('master')

    def test_software_device_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json';p.write_text(json.dumps({'gpu':-1}))
            with self.assertRaisesRegex(ValueError,'no CPU fallback'):backend.config(p)

    def test_unknown_restore_backend_is_not_an_implicit_ncnn_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';path.write_text(json.dumps({'gpu':1,'gpu_name':'Tesla T10',
                'restore':True,'restoration_backend':'typo','file_sha256':{}}))
            with self.assertRaisesRegex(ValueError,'Unknown restoration backend'):backend.config(path)

    def test_nonzero_video_start_is_rejected_before_frame_index_seek(self):
        info={'format':{'start_time':'0','duration':'2'},'streams':[{'codec_type':'video','width':1920,
            'height':1080,'avg_frame_rate':'25/1','r_frame_rate':'25/1','start_time':'0.04'}]}
        with patch.object(backend,'probe',return_value=info),self.assertRaisesRegex(ValueError,'start offset'):
            backend.source_info('master')

    def test_scratch_and_output_budget_is_bounded(self):
        d=backend.disk_plan({'format':{'duration':'3517.56'}},{'scratch':'/tmp/private','fingerprint':'modelhash'})
        self.assertGreater(d['additional_peak_bytes'],3517.56*40_000_000/8)
        self.assertEqual(d['scratch_peak_bytes'],8*GIB)
        self.assertLess(d['additional_peak_bytes']/GIB,27)

    def test_old_master_hash_cannot_be_reused_under_same_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp);(post/'backend').mkdir();p=post/'S01E49.clean-master.mov';p.write_bytes(b'old master')
            digest=sha256(p);(post/'backend/master-policy.json').write_text(json.dumps({'retired_sha256':[digest]}))
            with patch.object(worker,'POST',post),self.assertRaisesRegex(ValueError,'Retired master'):
                worker.verified_master_hash(p,{'master_sha256':digest})

    def test_transfer_hash_required_and_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp);p=post/'master.mov';p.write_bytes(b'new master')
            with patch.object(worker,'POST',post):
                self.assertIsNone(worker.verified_master_hash(p,{}))
                with self.assertRaisesRegex(ValueError,'differs'):
                    worker.verified_master_hash(p,{'master_sha256':'0'*64})
                p.with_name(p.name+'.verified.json').write_text(json.dumps({'transfer_verified':True,'sha256':sha256(p)}))
                self.assertEqual(worker.verified_master_hash(p,{}),sha256(p))


if __name__=='__main__':unittest.main()
