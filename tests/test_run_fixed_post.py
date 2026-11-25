import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import hashlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_fixed_post import inputs,WaitInput,select_project,freeze_style,serial_gate,retire_idle_worker,restart_master_gate,archive_retired_pending_report
from run_fixed_post import archive_retired_artifact_registry


class FixedPostInputGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);folder=self.root/'post/incoming';folder.mkdir(parents=True)
        self.master=folder/'S03E02.clean-master.mov';self.master.write_bytes(b'synthetic master for gate test')
        handoffs=self.root/'post/handoffs';handoffs.mkdir()
        self.receipt=dict(episode='S03E02',transfer_verified=True,bytes=self.master.stat().st_size,
            sha256=hashlib.sha256(self.master.read_bytes()).hexdigest())
        self.receipt_path=handoffs/'S03E02.master-received.json';self.receipt_path.write_text(json.dumps(self.receipt))

    def test_unsealed_subtitle_never_launches_install_or_worker(self):
        with patch('run_fixed_post.subprocess.run') as launch:
            with self.assertRaises(WaitInput):inputs(self.root,self.master,self.root/'missing.ass')
        launch.assert_not_called()

    def test_filename_presence_or_modified_master_does_not_admit_production(self):
        self.master.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'receipt'):inputs(self.root,self.master,self.root/'missing.ass')

    def restart_policy(self,denied):
        directory=self.root/'.kwjd';directory.mkdir()
        (directory/'jigong-native25-restart.json').write_text(json.dumps(dict(
            schema_version=1,active=True,required_input_fps=25,retired_master_sha256=denied)))

    def test_old_sha_is_rejected_even_with_matching_transfer_receipt(self):
        self.restart_policy({'S03E02':self.receipt['sha256']})
        with patch('run_fixed_post.subprocess.check_output') as probe:
            with self.assertRaisesRegex(WaitInput,'Retired30'):inputs(self.root,self.master,self.root/'missing.ass')
        probe.assert_not_called()

    def test_new_hash_cannot_bypass_native25_rate_gate(self):
        self.restart_policy({'S03E02':'a'*64})
        with patch('run_fixed_post.subprocess.check_output',return_value=json.dumps(
                {'streams':[{'r_frame_rate':'30/1','avg_frame_rate':'30/1'}]})):
            with self.assertRaisesRegex(WaitInput,'non25'):
                restart_master_gate(self.root,'S03E02',self.master,self.receipt['sha256'])

    def test_new_native25_still_cannot_bypass_subtitle_gate(self):
        self.restart_policy({'S03E02':'a'*64})
        with patch('run_fixed_post.subprocess.check_output',return_value=json.dumps(
                {'streams':[{'r_frame_rate':'25/1','avg_frame_rate':'25/1'}]})):
            with self.assertRaisesRegex(WaitInput,'sealed'):inputs(self.root,self.master,self.root/'missing.ass')

    def test_incomplete_restart_policy_fails_closed(self):
        self.restart_policy({})
        with self.assertRaisesRegex(WaitInput,'inventory'):
            restart_master_gate(self.root,'S03E02',self.master,self.receipt['sha256'])

    def test_retired_later_job_does_not_block_first_replacement_but_cannot_approve_next(self):
        self.restart_policy({'S03E02':'a'*64,'S03E03':'b'*64})
        jobs=self.root/'post/jobs';jobs.mkdir()
        (jobs/'S03E03.json').write_text(json.dumps(dict(state='SUPERSEDED_WAIT_NATIVE25_MASTER',master_sha256='b'*64)))
        serial_gate(self.root,'S03E02')
        (jobs/'S03E02.json').write_text(json.dumps(dict(state='SUPERSEDED_WAIT_NATIVE25_MASTER',master_sha256='a'*64)))
        with self.assertRaisesRegex(WaitInput,'finish first'):serial_gate(self.root,'S03E03')
        (jobs/'S03E03.json').write_text(json.dumps(dict(state='SUPERSEDED_WAIT_NATIVE25_MASTER',master_sha256='c'*64)))
        with self.assertRaisesRegex(WaitInput,'owns'):serial_gate(self.root,'S03E02')

    def test_unadmitted_episode_is_not_added_to_queue(self):
        repo=Path(__file__).resolve().parents[1]
        self.assertEqual(select_project(repo,'S03E02').name,'jigong-s03.json')
        self.assertEqual(select_project(repo,'S03E03').name,'jigong-s03.json')
        with self.assertRaisesRegex(ValueError,'admitted'):select_project(repo,'S03E15')

    def test_retired_pending_checkpoint_is_archived_but_current_one_is_retained(self):
        self.restart_policy({'S03E02':'a'*64})
        pending=self.root/'post/reports/S03E02.pending.json';pending.parent.mkdir()
        old=json.dumps(dict(episode='S03E02',master_sha256='a'*64)).encode()
        pending.write_bytes(old)
        self.assertTrue(archive_retired_pending_report(self.root,'S03E02'))
        self.assertFalse(pending.exists())
        archived=list((self.root/'.kwjd/retired/jigong-cfr30-20261125/reports').iterdir())
        self.assertEqual([p.read_bytes() for p in archived],[old])
        pending.write_text(json.dumps(dict(episode='S03E02',master_sha256='b'*64)))
        self.assertFalse(archive_retired_pending_report(self.root,'S03E02'))
        self.assertTrue(pending.exists())

    def test_retired_pending_with_media_cannot_be_silently_archived(self):
        self.restart_policy({'S03E02':'a'*64})
        pending=self.root/'post/reports/S03E02.pending.json';pending.parent.mkdir()
        pending.write_text(json.dumps(dict(episode='S03E02',master_sha256='a'*64)))
        final=self.root/'post/final/S03E02.final-1080p60.mkv';final.parent.mkdir();final.write_bytes(b'retained')
        with self.assertRaisesRegex(WaitInput,'still has media'):
            archive_retired_pending_report(self.root,'S03E02')
        self.assertTrue(pending.exists());self.assertTrue(final.exists())

    def test_replacement_retires_only_old_master_bound_cleanup_registry(self):
        self.restart_policy({'S03E02':'a'*64})
        registry=self.root/'.kwjd/artifacts/S03E02.linux.json';registry.parent.mkdir()
        def inventory(digest):return dict(episode='S03E02',role='linux',artifacts=[
            dict(relative='incoming/S03E02.clean-master.mov',fingerprint=dict(kind='file',sha256=digest))])
        original=json.dumps(inventory('a'*64)).encode();registry.write_bytes(original)
        self.assertTrue(archive_retired_artifact_registry(self.root,'S03E02'))
        saved=list((self.root/'.kwjd/retired/jigong-cfr30-20261125/artifacts').iterdir())
        self.assertEqual([p.read_bytes() for p in saved],[original]);self.assertFalse(registry.exists())
        registry.write_text(json.dumps(inventory('b'*64)))
        self.assertFalse(archive_retired_artifact_registry(self.root,'S03E02'))
        self.assertTrue(registry.exists())

    def test_next_chapter_cannot_skip_prior_media_or_actual_quality(self):
        jobs=self.root/'post/jobs';jobs.mkdir()
        previous=jobs/'S03E02.json';previous.write_text(json.dumps(dict(state='ENHANCING')))
        with self.assertRaises(WaitInput):serial_gate(self.root,'S03E03')
        previous.write_text(json.dumps(dict(state='READY')))
        with patch('jigong_quality.verify_quality',side_effect=ValueError('Missing real quality')):
            with self.assertRaises(ValueError):serial_gate(self.root,'S03E03')

    def test_next_chapter_needs_qc_manifest_but_not_windows_receipt(self):
        jobs=self.root/'post/jobs';jobs.mkdir();(jobs/'S03E02.json').write_text(json.dumps(dict(state='READY')))
        quality=dict(final_sha256='a'*64,quality_sha256='b'*64)
        manifest=self.root/'post/handoffs/S03E02.cloud-final.json'
        manifest.write_text(json.dumps(dict(server_quality_checked=True,sha256='a'*64,quality_evidence_sha256='b'*64)))
        with patch('jigong_quality.verify_quality',return_value=quality):serial_gate(self.root,'S03E03')
        manifest.write_text(json.dumps(dict(server_quality_checked=True,sha256='c'*64,quality_evidence_sha256='b'*64)))
        with patch('jigong_quality.verify_quality',return_value=quality):
            with self.assertRaises(WaitInput):serial_gate(self.root,'S03E03')

    def test_serial_service_retires_only_an_idle_ready_scope(self):
        from subprocess import CompletedProcess
        jobs=self.root/'post/jobs';jobs.mkdir();previous=jobs/'S03E02.json'
        with patch('run_fixed_post.subprocess.run',return_value=CompletedProcess([],0)) as run, \
                patch('run_fixed_post.subprocess.check_output',return_value='12345'), \
                patch.object(Path,'read_bytes',return_value=b'python\0server_worker.py\0--episode\0S03E02\0'):
            previous.write_text(json.dumps(dict(state='ENHANCING')))
            with self.assertRaises(WaitInput):retire_idle_worker(self.root,'S03E03','kwjd-fixed-s03e02')
            self.assertEqual(run.call_count,1)  # is-active only; no stop
            run.reset_mock();previous.write_text(json.dumps(dict(state='READY')))
            retire_idle_worker(self.root,'S03E03','kwjd-fixed-s03e02')
            self.assertEqual(run.call_args.args[0],['systemctl','--user','stop','kwjd-fixed-s03e02.service'])

    def test_next_master_approval_does_not_rebind_an_active_episode_style(self):
        folder=self.root/'post/styles';folder.mkdir();template=folder/'template.ass'
        template.write_text('synthetic test style')
        marker=template.with_name(template.name+'.ready.json')
        approval=dict(approved=True,master_sha256='first-master',sha256=hashlib.sha256(template.read_bytes()).hexdigest())
        marker.write_text(json.dumps(approval))
        first=freeze_style(self.root,'S03E02',{'_selected_style':str(template)})
        first_marker=first.with_name(first.name+'.ready.json');original=first_marker.read_bytes()
        marker.write_text(json.dumps({**approval,'master_sha256':'next-master'}))
        freeze_style(self.root,'S03E03',{'_selected_style':str(template)})
        self.assertEqual(first_marker.read_bytes(),original)
        self.assertEqual(json.loads(first_marker.read_text())['master_sha256'],'first-master')


if __name__=='__main__':unittest.main()
