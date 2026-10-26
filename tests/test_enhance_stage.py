import json
from pathlib import Path
import sys
import tempfile
import unittest
import fcntl
import subprocess
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import enhance_stage as stage
import server_worker as worker


def media(fps=60, seconds=3):
    return {'format':{'duration':str(seconds)}, 'streams':[
        {'codec_type':'video','width':1920,'height':1080,
         'avg_frame_rate':f'{fps}/1','r_frame_rate':f'{fps}/1'}]}


class StageTests(unittest.TestCase):
    def setUp(self):
        # Stage ordering tests isolate the separately integration-tested audio handoff.
        for name, result in [('preserve_source', ({'source_probe':media()},Path('/unused/audio.flac'))),
                             ('release_master_copy',None),('register_artifact',None),('backend_technically_ready',True)]:
            mock=patch.object(worker,name,return_value=result)
            mock.start();self.addCleanup(mock.stop)

    def test_checkpoint_reuse_and_changed_master_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master.mkv';out=root/'out.mkv';master.write_bytes(b'original')
            def encode(args, **kwargs):Path(args[2]).write_bytes(b'enhanced-video')
            with patch.object(stage,'probe',side_effect=lambda p:media(25 if Path(p)==master else 60)), \
                 patch.object(stage.subprocess,'check_output',return_value=json.dumps({'additional_peak_bytes':10,'backend_fingerprint':'modelsha'})), \
                 patch.object(stage,'check_space'),patch.object(stage.subprocess,'run',side_effect=encode) as run:
                receipt=stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(receipt['stage'],'ENHANCED_READY');self.assertEqual(run.call_count,1)
                stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(run.call_count,1)
                master.write_bytes(b'different')
                with self.assertRaisesRegex(ValueError,'hash differs'):
                    stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(out.read_bytes(),b'enhanced-video')

    def test_outer_enhancement_publication_recovers_from_missing_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';master.write_bytes(b'original');out=root/'enhanced.mkv'
            real_atomic=stage.atomic_json
            def crash(path,data):
                if Path(path).name=='enhanced.mkv.ready.json':raise OSError('simulated receipt crash')
                real_atomic(path,data)
            def encode(args,**kw):Path(args[2]).write_bytes(b'accepted-video');return Mock(returncode=0)
            with patch.object(stage,'probe',return_value=media()),patch.object(stage,'check_space'), \
                 patch.object(stage.subprocess,'check_output',return_value=json.dumps({'additional_peak_bytes':10,'backend_fingerprint':'model'})), \
                 patch.object(stage.subprocess,'run',side_effect=encode) as run:
                with patch.object(stage,'atomic_json',side_effect=crash),self.assertRaises(OSError):
                    stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertTrue(out.exists());self.assertFalse(out.with_name(out.name+'.ready.json').exists())
                stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(run.call_count,1)
                self.assertTrue(out.with_name(out.name+'.ready.json').exists())

    def test_orphan_output_never_trusted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';out=root/'out';master.write_bytes(b'x');out.write_bytes(b'orphan')
            with patch.object(stage,'probe',return_value=media()),self.assertRaisesRegex(ValueError,'no verified checkpoint'):
                stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
            self.assertEqual(out.read_bytes(),b'orphan')

    def test_outer_publication_recovers_before_link_without_running_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';master.write_bytes(b'original');out=root/'enhanced.mkv'
            partial=root/'enhanced.partial.mkv';partial.write_bytes(b'accepted-video')
            record={'episode':'S01E49','master_sha256':stage.sha256(master),
                    'sha256':stage.sha256(partial),'backend_fingerprint':'model'}
            stage.atomic_json(out.with_name(out.name+'.ready.pending.json'),record)
            with patch.object(stage,'probe',return_value=media()), \
                 patch.object(stage.subprocess,'check_output',return_value=json.dumps({'additional_peak_bytes':10,'backend_fingerprint':'model'})), \
                 patch.object(stage.subprocess,'run') as run,patch.object(stage,'check_space') as space:
                self.assertEqual(stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log'),record)
                run.assert_not_called();space.assert_not_called()
            self.assertFalse(partial.exists());self.assertEqual(out.read_bytes(),b'accepted-video')
            self.assertEqual(json.loads(out.with_name(out.name+'.ready.json').read_text()),record)

    def test_modified_pending_partial_is_retained_and_never_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';master.write_bytes(b'original');out=root/'enhanced.mkv'
            partial=root/'enhanced.partial.mkv';partial.write_bytes(b'accepted-video')
            record={'episode':'S01E49','master_sha256':stage.sha256(master),
                    'sha256':stage.sha256(partial),'backend_fingerprint':'model'}
            stage.atomic_json(out.with_name(out.name+'.ready.pending.json'),record)
            partial.write_bytes(b'unverified replacement')
            with patch.object(stage,'probe',return_value=media()), \
                 patch.object(stage.subprocess,'run') as run,self.assertRaisesRegex(ValueError,'Pending enhancement'):
                stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
            run.assert_not_called();self.assertFalse(out.exists())
            self.assertEqual(partial.read_bytes(),b'unverified replacement')

    def test_backend_disk_pause_maps_to_waitdisk_and_preserves_checkpoints(self):
        for returncode in (75,1):
            with self.subTest(returncode=returncode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);master=root/'master';master.write_bytes(b'original');out=root/'enhanced.mkv'
                def pause(args,**kw):
                    (root/'saved-segment.ready.json').write_text('{"verified": true}')
                    raise subprocess.CalledProcessError(returncode,args)
                with patch.object(stage,'probe',return_value=media()),patch.object(stage,'check_space'), \
                     patch.object(stage.subprocess,'check_output',return_value=json.dumps({'additional_peak_bytes':10,'backend_fingerprint':'model'})), \
                     patch.object(stage.subprocess,'run',side_effect=pause), \
                     self.assertRaises(stage.WaitDisk if returncode==75 else subprocess.CalledProcessError):
                    stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertTrue((root/'saved-segment.ready.json').exists())
                self.assertFalse(out.exists());self.assertFalse(out.with_name(out.name+'.ready.json').exists())

    def test_backend_disk_pause_during_plan_maps_to_waitdisk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';master.write_bytes(b'original')
            with patch.object(stage,'probe',return_value=media()), \
                 patch.object(stage.subprocess,'check_output',side_effect=subprocess.CalledProcessError(75,['backend','--plan'])), \
                 patch.object(stage.subprocess,'run') as run,self.assertRaises(stage.WaitDisk):
                stage.ensure_enhanced(master,root/'out',root/'backend','S01E49',root/'log')
            run.assert_not_called()

    def test_reserve_and_wrong_fps(self):
        with patch.object(stage.shutil,'disk_usage',return_value=Mock(free=30*stage.GIB)):
            with self.assertRaises(stage.WaitDisk):stage.check_space(Path('/tmp'),6*stage.GIB,25)
        with self.assertRaises(ValueError):stage.validate_video(media(),media(25))

    def test_enhance_only_needs_no_srt_style_or_fonts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';post.mkdir();backend=post/'backend';backend.write_text('#!/bin/sh');backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49','master':'incoming/S01E49.clean-master.mkv','style':'styles/missing.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='enhance'), \
                 patch.object(worker,'ready',return_value=True),patch.object(worker,'verified_master_hash',return_value='confirmed'),patch.object(worker,'ensure_enhanced',return_value={'sha256':'validated','master_sha256':'confirmed'}) as enhance:
                worker.do_job(job)
                self.assertEqual(json.loads(job.read_text())['state'],'ENHANCED_READY')
                enhance.assert_called_once()
                self.assertFalse((root/'output').exists())

    def test_all_mode_enhances_before_waiting_for_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';post.mkdir();(root/'output').mkdir();srt=root/'output/S01E49.zh.srt';srt.write_text('reviewed')
            srt.with_name(srt.name+'.ready.json').write_text(json.dumps({'reviewed':True,'sha256':stage.sha256(srt)}))
            backend=post/'backend';backend.touch();backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49','master':'incoming/master.mkv','style':'styles/missing.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='all'), \
                 patch.object(worker,'ready',return_value=True),patch.object(worker,'verified_master_hash',return_value='confirmed'),patch.object(worker,'ensure_enhanced',return_value={'sha256':'validated','master_sha256':'confirmed'}) as enhance:
                worker.do_job(job)
                self.assertEqual(json.loads(job.read_text())['state'],'WAIT_STYLE')
                enhance.assert_called_once()

    def test_template_presence_does_not_authorize_burn_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';(post/'styles').mkdir(parents=True)
            (root/'output').mkdir();srt=root/'output/S01E49.zh.srt';srt.write_text('reviewed')
            srt.with_name(srt.name+'.ready.json').write_text(json.dumps({'reviewed':True,'sha256':stage.sha256(srt)}))
            (post/'styles/template.ass').write_text('not approved')
            (post/'final').mkdir();(post/'final/out.mkv').write_bytes(b'old-final')
            (post/'reports').mkdir();(post/'reports/S01E49.json').write_text(json.dumps({
                'master_sha256':'confirmed','sha256':stage.sha256(post/'final/out.mkv')}))
            backend=post/'backend';backend.touch();backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49','master':'incoming/master.mkv','style':'styles/template.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='all'), \
                 patch.object(worker,'ready',return_value=True),patch.object(worker,'verified_master_hash',return_value='confirmed'),patch.object(worker,'ensure_enhanced',return_value={'sha256':'validated','master_sha256':'confirmed'}), \
                 patch.object(worker,'command') as command:
                worker.do_job(job)
                self.assertEqual(json.loads(job.read_text())['state'],'WAIT_STYLE_APPROVAL')
                command.assert_not_called()

    def test_final_reuse_binds_all_current_source_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            final=Path(tmp)/'final.mkv';final.write_bytes(b'final')
            receipt={'sha256':'enhanced'}
            report={'episode':'S01E49','master_sha256':'new-master',
                    'enhanced_sha256':'enhanced','srt_sha256':'reviewed',
                    'style_sha256':'approved','sha256':stage.sha256(final)}
            worker.validate_final_binding(report,final,'S01E49','new-master',receipt,'reviewed','approved')
            for key in ('episode','master_sha256','enhanced_sha256','srt_sha256','style_sha256','sha256'):
                with self.subTest(key=key):
                    stale={**report,key:'obsolete'}
                    with self.assertRaisesRegex(ValueError,'retained'):
                        worker.validate_final_binding(stale,final,'S01E49','new-master',receipt,'reviewed','approved')

    def test_confirmed_master_must_match_enhancement_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';post.mkdir();backend=post/'backend';backend.touch();backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49',
                'master':'incoming/master.mkv','style':'styles/template.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='enhance'), \
                 patch.object(worker,'ready',return_value=True), \
                 patch.object(worker,'verified_master_hash',return_value='new-master'), \
                 patch.object(worker,'ensure_enhanced',return_value={'sha256':'old-video','master_sha256':'old-master'}):
                with self.assertRaisesRegex(ValueError,'confirmed master'):worker.do_job(job)

    def test_changed_font_cannot_reuse_style_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp);fonts=post/'styles/fonts';fonts.mkdir(parents=True)
            font=fonts/'approved.ttf';font.write_bytes(b'original-font')
            approval={'fonts':{'approved.ttf':stage.sha256(font)}}
            with patch.object(worker,'POST',post):
                self.assertEqual(worker.approved_fonts(approval),approval['fonts'])
                font.write_bytes(b'replaced-font')
                with self.assertRaisesRegex(ValueError,'differs'):worker.approved_fonts(approval)

    def test_accepted_final_partial_recovers_after_publication_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';(post/'final').mkdir(parents=True);(post/'reports').mkdir()
            directory=post/'cache/S01E49';directory.mkdir(parents=True)
            bundle=directory/'S01E49.source.ready.json'
            bundle.write_text(json.dumps({'master_sha256':'confirmed','enhanced_checkpoint':{'sha256':'enhanced'}}))
            partial=post/'final/out.partial.mkv';partial.write_bytes(b'verified-final')
            report={'episode':'S01E49','master_sha256':'confirmed','enhanced_sha256':'enhanced',
                    'srt_sha256':'srt','style_sha256':'style','fonts':{},'sha256':stage.sha256(partial),
                    'source_bundle_sha256':stage.sha256(bundle),'final_decode_checked':True,'final_pcm_equivalent':True}
            (post/'reports/S01E49.pending.json').write_text(json.dumps(report))
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49',
                'master':'incoming/master.mov','master_sha256':'confirmed','style':'styles/template.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,STAGE='all'), \
                 patch.object(worker,'approved_inputs',return_value=('srt','style',None,None,{})), \
                 patch.object(worker,'release_enhancement_inputs'),patch.object(worker,'register_completed_assets'), \
                 patch.object(worker,'ensure_enhanced') as enhance:
                worker.do_job(job)
                enhance.assert_not_called()
            self.assertEqual((post/'final/out.mkv').read_bytes(),b'verified-final')
            self.assertFalse(partial.exists());self.assertEqual(json.loads(job.read_text())['state'],'READY')
            self.assertEqual(json.loads((post/'reports/S01E49.json').read_text()),report)

    def test_idle_worker_releases_real_flock_for_receipt_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp)
            def idle(_):
                self.assertFalse(worker.WORKER_LOCK_HELD)
                with (post/'jobs/.worker.lock').open('a+') as other:
                    fcntl.flock(other.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                raise KeyboardInterrupt('stop empty polling loop')
            with patch.object(worker,'POST',post),patch.object(worker.sys,'argv',['worker','--loop']), \
                 patch.object(worker.time,'sleep',side_effect=idle):
                with self.assertRaises(KeyboardInterrupt):worker.main()

    def test_final_ass_and_nested_stage_receipts_survive_cache_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';directory=post/'cache/S01E49';directory.mkdir(parents=True)
            (directory/'S01E49.ass').write_text('final ASS')
            (directory/'S01E49.source.ready.json').write_text('{"source":"verified"}')
            (directory/'.release').mkdir();(directory/'.release/receipt.json').write_text('{"released":true}')
            with patch.multiple(worker,ROOT=root,POST=post):
                worker.register_completed_assets('S01E49','masterhash',post/'final/S01E49.final-1080p60.mkv',directory)
            import shutil
            shutil.rmtree(directory)
            archive=post/'reports/stages/S01E49/masterhash'
            self.assertEqual((archive/'S01E49.ass').read_text(),'final ASS')
            self.assertEqual((archive/'.release/receipt.json').read_text(),'{"released":true}')


if __name__=='__main__':unittest.main()
