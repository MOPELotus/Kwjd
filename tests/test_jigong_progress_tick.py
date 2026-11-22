import sys
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jigong_progress_tick import event,quota,tick


class ProgressTickTests(unittest.TestCase):
    def snapshot(self,**values):
        data=dict(job={},review_ready=False,cloud_final_exists=False,
            windows_online=False,windows_rule_acknowledged=False,receipt_exists=False,
            both_cleaned=False,media_log_age_seconds=10)
        data.update(values);return data

    def test_waiting_for_review_does_not_wake_model(self):
        self.assertIsNone(event(self.snapshot(),{}))

    def test_real_first_seal_can_wake_main_but_active_media_does_not(self):
        self.assertEqual(event(self.snapshot(review_ready=True),{}),'FIRST_SRT_READY_START_PRODUCTION')
        self.assertIsNone(event(self.snapshot(review_ready=True,job={'state':'PROCESSING'}),{}))

    def test_actual_final_requires_codex_qc_not_automatic_approval(self):
        self.assertEqual(event(self.snapshot(job={'state':'READY'}),{}),'FINAL_READY_REQUIRES_ACTUAL_CODEX_QC')

    def test_idle_media_log_wakes_main_without_killing_media(self):
        self.assertEqual(event(self.snapshot(job={'state':'PROCESSING'},media_log_age_seconds=3601),{}),
                         'PRODUCTION_LOG_UNCHANGED_OVER_ONE_HOUR')

    def test_quota_uses_real_percent_and_rejects_rpc_error_with_zero_exit(self):
        with patch('jigong_progress_tick.subprocess.run',return_value=SimpleNamespace(stdout='remaining 1.5%\npoints 62500')):
            self.assertEqual(quota(),1.5)
        with patch('jigong_progress_tick.subprocess.run',return_value=SimpleNamespace(stdout='RPC error -32603')):
            with self.assertRaises(ValueError):quota()

    def test_low_or_unknown_quota_prevents_model_wake_and_preserves_media(self):
        for remaining in (1.9,None):
            with self.subTest(remaining=remaining),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);project=root/'repo/config/projects/jigong-s03.json'
                project.parent.mkdir(parents=True);project.write_text(json.dumps({'cloud_supervision':{
                    'model_pause_below_remaining_percent':2,'continue_media_processes_when_model_paused':True}}))
                jobs=root/'post/jobs';jobs.mkdir(parents=True)
                (jobs/'S03E02.json').write_text('{"state":"FAILED"}')
                media=root/'post/actual-media.partial.mkv';media.write_bytes(b'active-background-media')
                config=root/'timer.json';config.write_text(json.dumps({'root':str(root),
                    'thread_id':'01a203c2-2956-74e1-a574-bfb03258d250'}))
                with patch('subtitle_review_supervisor.validate_episode',return_value=False), \
                     patch('jigong_progress_tick.subprocess.check_output',return_value='{}'), \
                     patch('jigong_progress_tick.quota',side_effect=ValueError('RPC') if remaining is None else None,return_value=remaining), \
                     patch('jigong_progress_tick.subprocess.run') as dispatch, \
                     patch('builtins.print'):
                    report=tick(config,wake=True)
                dispatch.assert_not_called()
                self.assertTrue(report['media_processes_untouched'])
                self.assertEqual(media.read_bytes(),b'active-background-media')


if __name__=='__main__':unittest.main()
