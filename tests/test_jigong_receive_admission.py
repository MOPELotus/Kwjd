import sys
from pathlib import Path
import unittest
import tempfile
import json
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jigong_receive_admission import transfer_assessment,snapshot


class ReceiveProductionBudgetTests(unittest.TestCase):
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
