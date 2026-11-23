import sys
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jigong_progress_tick import event,quota,tick,additional_master_observations,quota_resume_state,event_signature,recover_enospc


class ProgressTickTests(unittest.TestCase):
    def snapshot(self,**values):
        data=dict(job={},review_ready=False,cloud_final_exists=False,
            windows_online=False,windows_rule_acknowledged=False,receipt_exists=False,
            both_cleaned=False,media_log_age_seconds=10)
        data.update(values);return data

    def test_waiting_for_review_does_not_wake_model(self):
        self.assertIsNone(event(self.snapshot(),{}))

    def test_enospc_preserves_failed_receipt_and_reuses_queue_without_media_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);jobs=root/'post/jobs';jobs.mkdir(parents=True)
            (root/'post/reports').mkdir()
            old=dict(episode='S03E02',state='FAILED',detail='[Errno 28] No space left on device',master_sha256='fixed')
            path=jobs/'S03E02.json';path.write_text(json.dumps(old))
            media=root/'post/retained.mkv';media.write_bytes(b'verified-prefix')
            updated=recover_enospc(root,'S03E02',old)
            self.assertEqual(updated['state'],'WAIT_DISK')
            self.assertEqual(updated['master_sha256'],'fixed')
            self.assertEqual(json.loads(Path(updated['enospc_recovery_evidence']).read_text())['previous_job'],old)
            self.assertEqual(media.read_bytes(),b'verified-prefix')
            self.assertEqual(recover_enospc(root,'S03E02',updated),updated)
            unrelated=dict(old,detail='Changed model hash')
            self.assertEqual(recover_enospc(root,'S03E02',unrelated),unrelated)

    def test_quota_resume_survives_unknown_and_each_pause_has_distinct_signature(self):
        paused=quota_resume_state({},1.9,100)
        unknown=quota_resume_state(paused,None,200)
        self.assertEqual(unknown,paused)
        recovered=quota_resume_state(unknown,2,300)
        snapshot=self.snapshot(quota_recovery_pending=recovered['resume_required'],
            quota_remaining_percent=2,quota_pause_epoch=recovered['pause_epoch'])
        kind=event(snapshot,{})
        self.assertEqual(kind,'MODEL_QUOTA_RECOVERED_RESUME_EXISTING_AGENTS')
        first=event_signature(kind,{},snapshot)
        again=quota_resume_state(recovered,50,400)
        self.assertEqual(again['pause_epoch'],100)
        self.assertEqual(first,event_signature(kind,{},snapshot))
        acknowledged={**again,'resume_required':False}
        self.assertIsNone(event(self.snapshot(quota_recovery_pending=False,quota_remaining_percent=50),{}))
        second=quota_resume_state(acknowledged,1.5,500)
        self.assertTrue(second['resume_required'])
        self.assertNotEqual(first,event_signature(kind,{},dict(snapshot,quota_pause_epoch=second['pause_epoch'])))
        self.assertIsNone(event(self.snapshot(quota_recovery_pending=True,quota_remaining_percent=None),{}))

    def test_following_upload_is_not_received_until_handoff_exists(self):
        following=dict(handoff_present=False,master_verified=False,review_ready=False,job_exists=False)
        self.assertIsNone(event(self.snapshot(following_episode=following),{}))
        following['handoff_present']=True
        self.assertEqual(event(self.snapshot(following_episode=following),{}),
                         'FOLLOWING_MASTER_HANDOFF_REQUIRES_VERIFICATION:S03E03')

    def test_registered_separate_root_requires_real_handoff_and_main_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);receiving=root/'other-volume/E04';handoff=receiving/'post/handoffs/S03E04.master-received.json'
            registry=root/'.kwjd/queued-master-storage.json';registry.parent.mkdir()
            binding=dict(remote_root_for_existing_uploader=str(receiving),staged_real_handoff=str(handoff))
            registry.write_text(json.dumps({'episodes':{'S03E04':binding}}))
            observations=additional_master_observations(root)
            self.assertIsNone(event(self.snapshot(additional_masters=observations),{}))
            handoff.parent.mkdir(parents=True);handoff.write_text('{}')
            observations=additional_master_observations(root)
            self.assertFalse(observations[0]['master_verified'])
            self.assertEqual(event(self.snapshot(additional_masters=observations),{}),
                             'FOLLOWING_MASTER_HANDOFF_REQUIRES_VERIFICATION:S03E04')
            progress=root/'post/managed-S03E04/progress.json';progress.parent.mkdir(parents=True)
            progress.write_text('{"master_handoff_verified":true}')
            self.assertIsNone(event(self.snapshot(additional_masters=additional_master_observations(root)),{}))
            binding['staged_real_handoff']=str(root/'wrong.json')
            registry.write_text(json.dumps({'episodes':{'S03E04':binding}}))
            with self.assertRaises(ValueError):additional_master_observations(root)

    def test_following_episode_waits_for_serial_slot_and_independent_gates(self):
        following=dict(handoff_present=True,master_verified=True,review_ready=True,job_exists=False)
        self.assertIsNone(event(self.snapshot(job={'state':'PROCESSING'},following_episode=following),{}))
        self.assertEqual(event(self.snapshot(job={'state':'READY'},cloud_final_exists=True,following_episode=following),{}),
                         'FOLLOWING_EPISODE_READY_FOR_SERIAL_PRODUCTION:S03E03')
        following['job_exists']=True
        self.assertIsNone(event(self.snapshot(job={'state':'READY'},cloud_final_exists=True,following_episode=following),
                                {'job':{'state':'READY'}}))

    def test_real_first_seal_can_wake_main_but_active_media_does_not(self):
        self.assertEqual(event(self.snapshot(review_ready=True),{}),'FIRST_SRT_READY_START_PRODUCTION')
        self.assertIsNone(event(self.snapshot(review_ready=True,job={'state':'PROCESSING'}),{}))

    def test_actual_final_requires_codex_qc_not_automatic_approval(self):
        self.assertEqual(event(self.snapshot(job={'state':'READY'}),{}),'FINAL_READY_REQUIRES_ACTUAL_CODEX_QC')

    def test_idle_media_log_wakes_main_without_killing_media(self):
        self.assertEqual(event(self.snapshot(job={'state':'PROCESSING'},media_log_age_seconds=3601),{}),
                         'PRODUCTION_LOG_UNCHANGED_OVER_ONE_HOUR')

    def test_stalled_subtitle_checkpoint_wakes_main_until_all13_complete(self):
        self.assertEqual(event(self.snapshot(review_checkpoint_age_seconds=3601),{}),
                         'SUBTITLE_REVIEW_CHECKPOINT_UNCHANGED_OVER_ONE_HOUR')
        self.assertIsNone(event(self.snapshot(review_checkpoint_age_seconds=3601,review_checkpoint_complete=True),{}))

    def test_clean_deployment_completion_or_failure_wakes_same_main(self):
        self.assertEqual(event(self.snapshot(clean_recovery_state='PASSED'),{}),'FIXED_CLEAN_RECOVERY_TEST_PASSED')
        self.assertIsNone(event(self.snapshot(clean_recovery_state='PASSED',clean_recovery_acknowledged=True),{'clean_recovery_state':'PASSED'}))
        self.assertEqual(event(self.snapshot(clean_recovery_state='PASSED'),{'clean_recovery_state':'PASSED'}),'FIXED_CLEAN_RECOVERY_TEST_PASSED')
        self.assertEqual(event(self.snapshot(clean_recovery_state='FAILED_RETAINED_FOR_INSPECTION'),{}),
                         'FIXED_CLEAN_RECOVERY_TEST_NEEDS_ATTENTION')

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

    def test_each_actual_quota_recovery_queues_same_thread_once_without_touching_media(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);project=root/'repo/config/projects/jigong-s03.json'
            project.parent.mkdir(parents=True);project.write_text(json.dumps({'cloud_supervision':{
                'model_pause_below_remaining_percent':2,'continue_media_processes_when_model_paused':True}}))
            media=root/'post/actual-media.partial.mkv';media.parent.mkdir();media.write_bytes(b'keep-media-running')
            thread='01a203c2-2956-74e1-a574-bfb03258d250'
            config=root/'timer.json';config.write_text(json.dumps({'root':str(root),'thread_id':thread}))
            with patch('subtitle_review_supervisor.validate_episode',return_value=False), \
                 patch('jigong_progress_tick.subprocess.check_output',return_value='{}'), \
                 patch('jigong_progress_tick.quota',side_effect=[1.9,ValueError('RPC'),50,50,1.5,50]), \
                 patch('jigong_progress_tick.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='queued',stderr='')) as dispatch, \
                 patch('builtins.print'):
                reports=[tick(config,wake=True) for _ in range(6)]
            self.assertEqual(dispatch.call_count,2)
            self.assertEqual(reports[2]['attention_event'],'MODEL_QUOTA_RECOVERED_RESUME_EXISTING_AGENTS')
            self.assertEqual(reports[3]['dispatch'],'ALREADY_QUEUED_EVENT')
            for call in dispatch.call_args_list:
                command=call.args[0]
                self.assertEqual(command[command.index('--thread')+1],thread)
                self.assertIn('--resume',command)
            self.assertEqual(media.read_bytes(),b'keep-media-running')


if __name__=='__main__':unittest.main()
