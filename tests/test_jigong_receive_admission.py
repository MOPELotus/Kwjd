import sys
from pathlib import Path
import unittest
import tempfile
import json
import hashlib
import os
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jigong_receive_admission import transfer_assessment,snapshot,occupied_segment_credit


class ReceiveProductionBudgetTests(unittest.TestCase):
    def test_credit_only_sha_verified_current_regular_segments_and_recheck_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            post=Path(temp);(post/'backend').mkdir();master='a'*64;fingerprint='b'*64;ep='S03E03'
            (post/'backend/production-ready.json').write_text(json.dumps(dict(
                master_sha256=master,backend_fingerprint=fingerprint,technical_checked=True)))
            folder=post/f'cache/{ep}/{ep}.segments'/(master[:16]+'-'+fingerprint[:16]);folder.mkdir(parents=True)
            media=folder/'000001.mkv';media.write_bytes(b'verified-current-encode')
            receipt=folder/'000001.ready.json';receipt.write_text(json.dumps(dict(
                episode=ep,master_sha256=master,backend_fingerprint=fingerprint,
                sha256=hashlib.sha256(media.read_bytes()).hexdigest())))
            job=dict(episode=ep,master_sha256=master);device=media.stat().st_dev
            self.assertEqual(occupied_segment_credit(post,job,device,1000),media.stat().st_size)
            media.write_bytes(b'changed')
            self.assertEqual(occupied_segment_credit(post,job,device,1000),0)
            media.unlink();target=post/'other-file';target.write_bytes(b'other')
            media.symlink_to(target)
            self.assertEqual(occupied_segment_credit(post,job,device,1000),0)
            media.unlink();os.link(target,media)
            self.assertEqual(occupied_segment_credit(post,job,device,1000),0)

    def test_separate_receiver_still_reads_actual_production_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'receive';production=Path(temp)/'production'
            intake=root/'post/managed-S03E05';intake.mkdir(parents=True)
            (intake/'native25-intake.json').write_text(json.dumps(dict(
                expected_master_sha256='d'*64,expected_master_bytes=100)))
            for folder in ['jobs','cache','final','handoffs','incoming']:
                (production/'post'/folder).mkdir(parents=True,exist_ok=True)
            (production/'post/jobs/S03E03.json').write_text(json.dumps(dict(
                episode='S03E03',state='ENHANCING',master_sha256='a'*64)))
            (production/'post/handoffs/S03E03.master-received.json').write_text(json.dumps(dict(
                sha256='a'*64,duration_seconds=3660.44)))
            with patch('jigong_receive_admission.subprocess.check_output',return_value=
                       b'{"streams":[{"sample_rate":"48000","channels":2}]}') as probe, \
                 patch('jigong_receive_admission.phase_peaks',return_value={}) as peaks:
                result=snapshot(root,'S03E05',production)
            self.assertEqual(result['active_production_episodes'],['S03E03'])
            self.assertEqual(result['production_root'],str(production))
            self.assertIn(str(production/'post/incoming/S03E03.clean-master.mov'),probe.call_args.args[0])
            self.assertEqual(peaks.call_args.args[:3],(3660.44,48000,2))

    def test_local_quality_hold_preserves_capacity_assessment_but_never_admits(self):
        from types import SimpleNamespace
        for pending, free, capacity in [(True,100*2**30,True),(True,2**30,False),(False,100*2**30,True)]:
            with self.subTest(pending=pending,free=free),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);folder=root/'post/managed-S03E08';folder.mkdir(parents=True)
                for name in ['cache','final','jobs']:(root/'post'/name).mkdir()
                (folder/'native25-intake.json').write_text(json.dumps(dict(
                    expected_master_sha256='d'*64,expected_master_bytes=100,
                    local_quality_disposition_pending=pending)))
                with patch('jigong_receive_admission.shutil.disk_usage',return_value=SimpleNamespace(free=free)):
                    result=snapshot(root,'S03E08')
                self.assertEqual(result['assessment']['allowed'],capacity)
                self.assertEqual(result['admitted'],capacity and not pending)
                if pending:
                    self.assertEqual(result['state'],'WAIT_LOCAL_QUALITY_DISPOSITION')
                    self.assertEqual(result['capacity_admitted'],capacity)

    def test_user_pause_prevents_admission_and_receiving_directory_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'post/managed-S03E03';folder.mkdir(parents=True)
            (folder/'native25-intake.json').write_text(json.dumps(dict(
                expected_master_sha256='d'*64,expected_master_bytes=100,upload_paused_by_user=True)))
            result=snapshot(root,'S03E03')
            self.assertEqual(result['state'],'USER_PAUSED_UPLOAD')
            self.assertFalse(result['admitted']);self.assertFalse((root/'post/incoming').exists())

    def test_incoming_only_space_cannot_consume_active_production_reservation(self):
        gib=2**30
        result=transfer_assessment({1:20*gib},{1:30*gib},1,8*gib,0)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['filesystems'][1]['shortfall_bytes'],3*gib)

    def test_partial_resume_reserves_remaining_bytes_once(self):
        gib=2**30
        result=transfer_assessment({1:20*gib},{1:30*gib},1,8*gib,4*gib)
        self.assertTrue(result['allowed'])

    def test_separate_receiving_disk_cannot_hide_production_disk_shortfall(self):
        gib=2**30
        result=transfer_assessment({1:12*gib},{1:16*gib,2:40*gib},2,8*gib,0)
        self.assertFalse(result['allowed'])

    def test_finished_production_allows_queued_mother_with_floor(self):
        gib=2**30
        self.assertTrue(transfer_assessment({}, {1:14*gib},1,8*gib,0)['allowed'])
        with self.assertRaises(ValueError):transfer_assessment({}, {1:14*gib},1,8*gib,9*gib)
