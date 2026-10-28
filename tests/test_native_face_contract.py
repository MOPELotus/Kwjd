"""CPU-only approval and lifecycle contracts for native fused delivery."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_face_recipe as recipe
import native_face_job as native
import server_worker as worker
from enhance_stage import sha256
from subtitle_timing import read_mapping

EP = 'S01E49'
MASTER = 'a' * 64


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class NativeRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.post = self.root/'post'
        self.font = self.post/'styles/fonts/subtitle.ttf'
        self.font.parent.mkdir(parents=True); self.font.write_bytes(b'font')
        self.fonts = {self.font.name: sha256(self.font)}
        self.srt = self.root/'S01E49.zh.srt'; self.srt.write_text('reviewed subtitle')
        self.style = self.post/'styles/template.ass'; self.style.write_text('approved template')
        self.ass = self.post/'cache/styled.ass'; self.ass.parent.mkdir(); self.ass.write_text('derived ASS')
        self.seal = self.srt.with_name(self.srt.name+'.ready.json')
        self.approval = self.style.with_name(self.style.name+'.ready.json')
        write(self.seal, dict(reviewed=True, sha256=sha256(self.srt)))
        write(self.approval, dict(approved=True, sha256=sha256(self.style), master_sha256=MASTER, fonts=self.fonts))
        evidence = self.post/'reports/sync.json'; write(evidence, {'actual_visual_check': True})
        self.timing_path = self.post/f'receipts/{EP}.subtitle-timing.json'
        write(self.timing_path, dict(schema_version=1, reviewed=True, episode=EP,
            master_sha256=MASTER, srt_sha256=sha256(self.srt), offset_ms=-99,
            evidence_path='reports/sync.json', evidence_sha256=sha256(evidence)))
        self.timing = read_mapping(self.post, EP, MASTER, sha256(self.srt))
        self.path = self.post/'cache/native.recipe.json'
        self.args = dict(post=self.post, episode=EP, master_sha256=MASTER, ass=self.ass,
                         srt=self.srt, style=self.style, fonts=self.fonts, timing=self.timing)

    def create(self):
        return recipe.create_recipe(self.path, **self.args)

    def test_create_validate_and_idempotent_reuse(self):
        expected = self.create(); digest = sha256(self.path)
        self.assertEqual(recipe.validate_recipe(self.path, digest), expected)
        self.assertEqual(self.create(), expected)
        self.assertEqual(sha256(self.path), digest)

    def test_changed_srt_style_ass_or_font_rejected(self):
        for name in ('srt', 'style', 'ass', 'font'):
            with self.subTest(asset=name):
                self.create(); digest = sha256(self.path); target = getattr(self, name)
                original = target.read_bytes(); target.write_bytes(original+b'changed')
                with self.assertRaises(ValueError): recipe.validate_recipe(self.path, digest)
                target.write_bytes(original)

    def test_srt_review_revoked_or_hash_replaced_rejected(self):
        self.create(); digest = sha256(self.path); original = self.seal.read_text()
        for changes in ({'reviewed': False}, {'sha256': 'b'*64}):
            with self.subTest(changes=changes):
                write(self.seal, {**json.loads(original), **changes})
                with self.assertRaises(ValueError): recipe.validate_recipe(self.path, digest)
        self.seal.write_text(original)

    def test_style_approval_master_font_or_approval_revoked_rejected(self):
        self.create(); digest = sha256(self.path); original = self.approval.read_text()
        for changes in ({'approved': False}, {'sha256': 'b'*64},
                        {'master_sha256': 'b'*64}, {'fonts': {}}):
            with self.subTest(changes=changes):
                write(self.approval, {**json.loads(original), **changes})
                with self.assertRaises(ValueError): recipe.validate_recipe(self.path, digest)
        self.approval.write_text(original)

    def test_added_font_rejected(self):
        self.create(); digest = sha256(self.path)
        (self.font.parent/'extra.otf').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'font set'): recipe.validate_recipe(self.path, digest)

    def test_timing_replaced_or_revoked_rejected(self):
        self.create(); digest = sha256(self.path); original = self.timing_path.read_text()
        for changes in ({'offset_ms': 0}, {'reviewed': False}, {'master_sha256': 'b'*64}):
            with self.subTest(changes=changes):
                write(self.timing_path, {**json.loads(original), **changes})
                with self.assertRaises(ValueError): recipe.validate_recipe(self.path, digest)
        self.timing_path.write_text(original)

    def test_production_cannot_inherit_45_second_sample_origin(self):
        data = self.create(); data['program_origin_seconds'] = 45
        write(self.path, data)
        with self.assertRaisesRegex(ValueError, 'Production program origin'):
            recipe.validate_recipe(self.path, sha256(self.path))

    def test_recipe_hash_and_ass_binding_cannot_be_substituted(self):
        data = self.create(); digest = sha256(self.path)
        data['ass_sha256'] = 'b'*64; write(self.path, data)
        with self.assertRaisesRegex(ValueError, 'hash changed'): recipe.validate_recipe(self.path, digest)
        with self.assertRaisesRegex(ValueError, 'fused ass'): recipe.validate_recipe(self.path, sha256(self.path))

    def test_existing_recipe_cannot_be_rebound(self):
        self.create(); before = self.path.read_bytes()
        self.ass.write_text('another derived ASS')
        with self.assertRaisesRegex(ValueError, 'Existing fused recipe changed'): self.create()
        self.assertEqual(self.path.read_bytes(), before)

    def test_checkpoint_and_recovery_bind_single_encode_and_all_approvals(self):
        data = self.create()
        context = dict(recipe_path=self.path, recipe_sha256=sha256(self.path))
        binding = {k: data[k] for k in ('master_sha256','srt_sha256','style_sha256','fonts','subtitle_timing','ass_sha256')}
        receipt = dict(native_recipe=str(self.path), native_recipe_sha256=sha256(self.path),
                       output_kind='ass_styled_video', video_encodes=1, fused_subtitle_binding=binding)
        native.check_checkpoint(receipt, context)
        self.assertEqual(native.recover_context(receipt)['ass'], self.ass)
        bads = [dict(video_encodes=2), dict(output_kind='unsubtitled_video'),
                dict(native_recipe_sha256='b'*64)]
        for key in binding:
            bads.append({'fused_subtitle_binding': {**binding, key: 'different'}})
        for changes in bads:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                native.check_checkpoint({**receipt, **changes}, context)
        write(self.seal, dict(reviewed=False, sha256=sha256(self.srt)))
        with self.assertRaises(ValueError): native.recover_context(receipt)

    def test_final_mux_is_only_stream_copy_and_preserves_audio_offset(self):
        command = native.final_command(Path('/enhanced.mkv'), Path('/mix.flac'), -.099, Path('/final.partial.mkv'))
        self.assertEqual(command[command.index('-c')+1], 'copy')
        self.assertEqual(command[command.index('-itsoffset')+1], '-0.099')
        self.assertEqual(command[-1], '/final.partial.mkv')
        self.assertEqual([command[i+1] for i,x in enumerate(command[:-1]) if x=='-map'], ['0:v:0','1:a:0'])
        for forbidden in ('-vf','-filter_complex','-c:v','-c:a','-gpu','-r','-t'):
            self.assertNotIn(forbidden, command)
        self.assertFalse(any('ass=' in arg or 'nvenc' in arg for arg in command))

    def test_prepare_reuses_approved_ass_without_reapplying_timing(self):
        write(self.post/'backend/runtime.native-template.json', {'test_config': True})
        def make_ass(args):
            Path(args.output).write_text('[Events]\nFormat: Start, End, Text\nDialogue: 0:00:01.00,0:00:02.00,原文\n')
        with patch('kwjd.cmd_make_ass',side_effect=make_ass) as generate, \
             patch('font_preflight.preflight',return_value={}), \
             patch('enhance_backend.config',return_value={}), \
             patch.object(native,'apply_offset',wraps=native.apply_offset) as offset:
            first=native.prepare(self.post,EP,MASTER,self.srt,self.style,self.fonts,self.root/'log')
            first_bytes=first['ass'].read_bytes()
            second=native.prepare(self.post,EP,MASTER,self.srt,self.style,self.fonts,self.root/'log')
        generate.assert_called_once();offset.assert_called_once()
        self.assertEqual(first_bytes,second['ass'].read_bytes())
        self.assertIn(b'0:00:00.90',first_bytes)


class NativeCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name)/'post';self.target=self.post/f'scratch/native-calibration/{EP}/fp'
        self.target.mkdir(parents=True);(self.post/'backend').mkdir()
        self.sample=self.target/'sample.mkv';self.sample.write_bytes(b'verified sample')
        self.result=dict(master_sha256=MASTER,backend_fingerprint='fp',technical_checked=True,
            source_fps=3.5,output_frames=300,sample_sha256=sha256(self.sample),
            validation={'sha256':sha256(self.sample)},gfpgan_exercised=True)
        write(self.target/'result.json',self.result)
        self.context=dict(cfg={'scratch':str(self.post/'scratch')},recipe={'master_sha256':MASTER},config_path=self.post/'config.json')
        self.ready=self.post/'backend/production-ready.json'
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(native.subprocess,'check_output',return_value=json.dumps({'backend_fingerprint':'fp'})))
        self.run=stack.enter_context(patch.object(native.subprocess,'run',side_effect=AssertionError('No real backend may run')))
        self.decode=stack.enter_context(patch('face_rework_worker.validate_video',return_value={}))

    def calibrate(self):
        native.calibrate(self.post/'master.mov',self.post,EP,self.context,self.post/'log')

    def test_cached_result_rejects_wrong_master_fingerprint_or_invalid_fps(self):
        for changes in ({'master_sha256':'b'*64},{'backend_fingerprint':'other'},
                        {'source_fps':0},{'source_fps':float('nan')}):
            with self.subTest(changes=changes):
                write(self.target/'result.json',{**self.result,**changes})
                with self.assertRaises(ValueError):self.calibrate()
                self.assertFalse(self.ready.exists())
        self.run.assert_not_called();self.decode.assert_not_called()

    def test_corrupted_sample_rejected_despite_old_technical_ready(self):
        write(self.ready,dict(master_sha256=MASTER,backend_fingerprint='fp',technical_checked=True,performance_acceptable=True))
        self.sample.write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError,'media changed'):self.calibrate()
        self.run.assert_not_called();self.decode.assert_not_called()

    def test_stale_ready_master_or_fp_requires_current_verified_sample(self):
        for changes in ({'master_sha256':'b'*64},{'backend_fingerprint':'other'}):
            with self.subTest(changes=changes):
                write(self.ready,dict(master_sha256=MASTER,backend_fingerprint='fp',technical_checked=True,performance_acceptable=True))
                ready=json.loads(self.ready.read_text());ready.update(changes);write(self.ready,ready)
                write(self.target/'result.json',{**self.result,'source_fps':0})
                with self.assertRaises(ValueError):self.calibrate()
                self.assertEqual(json.loads(self.ready.read_text()),ready)
        self.run.assert_not_called()

    def test_old_performance_false_is_revalidated_and_updated(self):
        write(self.ready,dict(master_sha256=MASTER,backend_fingerprint='fp',technical_checked=True,performance_acceptable=False))
        old_hash=sha256(self.ready)
        self.calibrate()
        self.decode.assert_called_once_with(self.sample,300,self.target)
        self.assertTrue(json.loads(self.ready.read_text())['performance_acceptable'])
        self.assertTrue((self.post/'backend/readiness-history'/f'{old_hash}.json').exists())
        self.run.assert_not_called()


class NativeWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.post = self.root/'post'
        for name in ('incoming','cache/S01E49','styles','final','logs','reports','receipts'):
            (self.post/name).mkdir(parents=True)
        self.master = self.post/'incoming/master.mkv'; self.master.write_bytes(b'original')
        write(self.post/'backend/runtime.json', {'encoder_gpu': 0, 'gpu_name': 'TEST GPU'})
        self.enhanced = self.post/'cache/S01E49/S01E49.enhanced.mkv'; self.enhanced.write_bytes(b'styled')
        self.mixed = self.post/'cache/S01E49/S01E49.mixed.flac'; self.mixed.write_bytes(b'original PCM')
        self.bundle = self.post/'cache/S01E49/S01E49.source.ready.json'; self.bundle.write_text('{}')
        self.style = self.post/'styles/template.ass'; self.style.write_text('style')
        self.srt = self.root/'output/S01E49.zh.srt'; self.srt.parent.mkdir(); self.srt.write_text('subtitle')
        self.seal = self.srt.with_name(self.srt.name+'.ready.json')
        self.approval = self.style.with_name(self.style.name+'.ready.json')
        write(self.seal, dict(reviewed=True, sha256=sha256(self.srt)))
        write(self.approval, dict(approved=True, sha256=sha256(self.style), master_sha256=MASTER, fonts={}))
        write(self.post/'receipts/S01E49.upload.json', dict(episode=EP, sha256=MASTER, verified=True, local_master_deleted=True))
        self.job = self.post/'job.json'; write(self.job, dict(schema_version=1, episode=EP,
            master='incoming/master.mkv', style='styles/template.ass', output='final/out.mkv'))
        self.receipt = dict(master_sha256=MASTER, sha256=sha256(self.enhanced), output_kind='ass_styled_video',
                            fused_subtitle_binding={}, native_recipe_sha256='b'*64)
        write(self.bundle,dict(master_sha256=MASTER,enhanced_checkpoint=self.receipt))
        self.media = dict(format={'duration':'10'}, streams=[
            dict(codec_type='video',codec_name='hevc',width=1920,height=1080,avg_frame_rate='60/1',r_frame_rate='60/1',pix_fmt='yuv420p10le'),
            dict(codec_type='audio',codec_name='flac',sample_rate='48000',channels=2,bits_per_raw_sample='24')])
        self.source = dict(source_probe=self.media, audio_offset_seconds=0,
            decoded_pcm_sha256='pcm', enhanced_frame_count=600, audio_sha256=sha256(self.mixed))
        self.context = dict(ass=self.post/'cache/S01E49/native-style/key/S01E49.ass')
        self.context['ass'].parent.mkdir(parents=True)
        self.context['ass'].write_text('already shifted approved ASS')
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.multiple(worker, ROOT=self.root,POST=self.post,BACKEND=self.post/'backend/enhance.sh',STAGE='all'))
        mocks = dict(ready=True,verified_master_hash=MASTER,register_artifact=None,reject_retired=None,
            approved_inputs=(sha256(self.srt),sha256(self.style),self.seal,self.approval,{}),
            ensure_enhanced=self.receipt,preserve_source=(self.source,self.mixed),
            check_space=None,font_preflight={},read_mapping={'offset_ms':-99,'receipt_sha256':'mapping'},
            probe=self.media,decoded_pcm_hash='pcm',decoded_video_frames=600,
            approved_fonts={},register_completed_assets=None,queue_completion_notice=None,
            release_enhancement_inputs=None,release_master_copy=None,apply_offset=None)
        self.mocks={}
        for name,value in mocks.items():
            self.mocks[name]=stack.enter_context(patch.object(worker,name,return_value=value))
        self.native_mocks={}
        for name,value in [('enabled',True),('prepare',self.context),('calibrate',None),('check_checkpoint',None),('recover_context',self.context)]:
            self.native_mocks[name]=stack.enter_context(patch.object(native,name,return_value=value))
        self.command = stack.enter_context(patch.object(worker,'command',side_effect=self.encode))
        self.make_ass = stack.enter_context(patch('kwjd.cmd_make_ass'))
        self.hardware_query=stack.enter_context(patch.object(worker.subprocess,'check_output',return_value='TEST GPU\n'))

    def encode(self, command, log):
        Path(command[-1]).write_bytes(b'final mux')
        return 0

    def test_fused_enhance_stage_keeps_master_before_final_qc(self):
        with patch.object(worker,'STAGE','enhance'):
            worker.do_job(self.job)
        self.mocks['release_master_copy'].assert_not_called()
        self.command.assert_not_called()
        self.assertEqual(json.loads(self.job.read_text())['state'],'ENHANCED_READY')

    def test_fused_final_pcm_or_frame_qc_failure_keeps_master(self):
        for name,value,message in [('decoded_pcm_hash','wrong','PCM differs'),('decoded_video_frames',599,'lost/added frames')]:
            with self.subTest(check=name),patch.object(worker,name,return_value=value),self.assertRaisesRegex(ValueError,message):
                worker.do_job(self.job)
            self.mocks['release_master_copy'].assert_not_called()
            self.assertTrue(self.master.exists())
            self.assertFalse((self.post/'final/out.mkv').exists())
            (self.post/'final/out.partial.mkv').unlink(missing_ok=True)

    def test_revoked_approval_after_mux_keeps_master_and_partial(self):
        def revoke(command, log):
            self.encode(command, log)
            write(self.approval,dict(approved=False,sha256=sha256(self.style),master_sha256=MASTER,fonts={}))
            return 0
        self.command.side_effect=revoke
        with self.assertRaisesRegex(ValueError,'approval changed'): worker.do_job(self.job)
        self.mocks['release_master_copy'].assert_not_called()
        self.assertTrue((self.post/'final/out.partial.mkv').exists())
        self.assertFalse((self.post/'final/out.mkv').exists())

    def test_fused_success_releases_only_after_qc_and_acceptance_report(self):
        def release(*args,**kwargs):
            self.assertTrue((self.post/'final/out.mkv').exists())
            report=json.loads((self.post/'reports/S01E49.json').read_text())
            self.assertTrue(report['final_decode_checked']);self.assertTrue(report['final_pcm_equivalent'])
            self.assertEqual(report['video_encodes'],1)
            self.mocks['decoded_pcm_hash'].assert_called_once()
            self.mocks['decoded_video_frames'].assert_called_once()
        self.mocks['release_master_copy'].side_effect=release
        worker.do_job(self.job)
        self.mocks['release_master_copy'].assert_called_once()
        self.mocks['apply_offset'].assert_not_called();self.make_ass.assert_not_called()
        self.assertEqual(json.loads(self.job.read_text())['state'],'READY')
        command=self.command.call_args.args[0]
        self.assertIn('copy',command);self.assertNotIn('-vf',command)

    def test_existing_final_recovery_does_not_encode_or_shift_ass_again(self):
        worker.do_job(self.job)
        self.command.reset_mock();self.mocks['release_master_copy'].reset_mock()
        worker.do_job(self.job)
        self.command.assert_not_called()
        self.mocks['apply_offset'].assert_not_called();self.make_ass.assert_not_called()
        self.mocks['release_master_copy'].assert_called_once()
        self.assertEqual(json.loads(self.job.read_text())['state'],'READY')

    def test_pending_final_recovery_requires_prior_complete_qc_before_master_release(self):
        worker.do_job(self.job)
        final=self.post/'final/out.mkv';partial=self.post/'final/out.partial.mkv'
        final.rename(partial)
        report=self.post/'reports/S01E49.json'
        data=json.loads(report.read_text());data['final_decode_checked']=False
        write(self.post/'reports/S01E49.pending.json',data);report.unlink()
        self.command.reset_mock();self.mocks['release_master_copy'].reset_mock()
        with self.assertRaisesRegex(ValueError,'Incomplete final acceptance'):worker.do_job(self.job)
        self.mocks['release_master_copy'].assert_not_called();self.command.assert_not_called()
        self.assertTrue(partial.exists());self.assertFalse(final.exists());self.assertTrue(self.master.exists())

    def test_fused_report_binding_rejects_missing_or_replaced_pipeline_evidence(self):
        worker.do_job(self.job)
        report=json.loads((self.post/'reports/S01E49.json').read_text())
        final=self.post/'final/out.mkv'
        for key in ('picture_pipeline','video_encodes','native_recipe_sha256','fused_subtitle_binding'):
            for replacement in (None,'changed'):
                stale={**report,key:replacement}
                with self.subTest(field=key,value=replacement),self.assertRaisesRegex(ValueError,'retained'):
                    worker.validate_final_binding(stale,final,EP,MASTER,self.receipt,sha256(self.srt),sha256(self.style),{})

    def test_fused_report_retains_nested_archive_ass_path_and_needs_no_gpu_query(self):
        (self.post/'backend/runtime.json').unlink()
        worker.do_job(self.job)
        report=json.loads((self.post/'reports/S01E49.json').read_text())
        expected=self.post/'reports/stages'/EP/MASTER/'native-style/key/S01E49.ass'
        self.assertEqual(report['final_ass'],str(expected))
        self.hardware_query.assert_not_called()

    def test_missing_upload_receipt_keeps_master_and_handoff_for_late_cleanup(self):
        (self.post/f'receipts/{EP}.upload.json').unlink()
        worker.do_job(self.job)
        self.mocks['release_master_copy'].assert_not_called()
        self.mocks['release_enhancement_inputs'].assert_not_called()
        self.assertTrue(json.loads(self.job.read_text())['native_source_release_pending'])
        self.assertTrue(self.master.exists());self.assertTrue(self.enhanced.exists());self.assertTrue(self.mixed.exists())
        self.command.reset_mock()
        worker.do_job(self.job)
        self.command.assert_not_called()
        self.mocks['release_master_copy'].assert_not_called()
        self.mocks['release_enhancement_inputs'].assert_not_called()

    def test_late_matching_receipt_releases_master_before_handoff_cleanup_without_reencode(self):
        upload=self.post/f'receipts/{EP}.upload.json';saved=upload.read_text();upload.unlink()
        worker.do_job(self.job)
        upload.write_text(saved);self.command.reset_mock()
        order=[]
        def master_release(*args,**kwargs):
            order.append('master');self.assertTrue(self.enhanced.exists());self.assertTrue(self.mixed.exists())
            self.master.unlink()
        def input_release(*args,**kwargs):
            order.append('handoff');self.assertFalse(self.master.exists())
        self.mocks['release_master_copy'].side_effect=master_release
        self.mocks['release_enhancement_inputs'].side_effect=input_release
        worker.do_job(self.job)
        self.assertEqual(order,['master','handoff']);self.command.assert_not_called()
        self.assertFalse(json.loads(self.job.read_text())['native_source_release_pending'])

    def test_ready_queue_waits_for_every_late_upload_gate(self):
        queued=self.post/f'jobs/{EP}.json'
        write(queued,dict(episode=EP,state='READY',master_sha256=MASTER,native_source_release_pending=True))
        upload=self.post/f'receipts/{EP}.upload.json'
        good=dict(episode=EP,sha256=MASTER,verified=True,local_master_deleted=True)
        variants=[None,{**good,'episode':'S01E50'},{**good,'sha256':'b'*64},
                  {**good,'verified':False},{**good,'local_master_deleted':False}]
        for receipt in variants:
            if receipt is None:upload.unlink(missing_ok=True)
            else:write(upload,receipt)
            with self.subTest(receipt=receipt),patch.object(sys,'argv',['server_worker.py','--once','--episode',EP]), \
                 patch.object(worker,'do_job') as run:
                worker.main()
                run.assert_not_called()
        write(upload,good)
        with patch.object(sys,'argv',['server_worker.py','--once','--episode',EP]),patch.object(worker,'do_job') as run:
            worker.main()
            run.assert_called_once_with(queued)

    def test_native_activation_preserves_legacy_source_bundle_without_preparing_profile(self):
        write(self.bundle,dict(master_sha256=MASTER,enhanced_checkpoint={'sha256':sha256(self.enhanced)}))
        worker.do_job(self.job)
        self.assertEqual(json.loads(self.job.read_text())['state'],'WAIT_LEGACY_BACKEND')
        self.native_mocks['prepare'].assert_not_called();self.native_mocks['calibrate'].assert_not_called()
        self.mocks['ensure_enhanced'].assert_not_called();self.command.assert_not_called()
        self.assertTrue(self.master.exists());self.assertTrue(self.enhanced.exists())

    def test_native_activation_preserves_legacy_checkpoint_without_source_bundle(self):
        self.bundle.unlink()
        write(self.enhanced.with_name(self.enhanced.name+'.ready.json'),dict(master_sha256=MASTER,sha256=sha256(self.enhanced)))
        worker.do_job(self.job)
        self.assertEqual(json.loads(self.job.read_text())['state'],'WAIT_LEGACY_BACKEND')
        self.native_mocks['prepare'].assert_not_called();self.native_mocks['calibrate'].assert_not_called()
        self.mocks['ensure_enhanced'].assert_not_called();self.command.assert_not_called()

    def test_fresh_job_freezes_native_picture_pipeline_before_processing(self):
        self.bundle.unlink();self.enhanced.unlink()
        with patch.object(worker,'STAGE','enhance'):worker.do_job(self.job)
        self.assertEqual(json.loads(self.job.read_text())['picture_pipeline'],'native_light_gfpgan_rife_ass_one_encode')
        self.native_mocks['prepare'].assert_called_once()


if __name__ == '__main__': unittest.main()
