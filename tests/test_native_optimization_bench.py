"""CPU-only orchestration tests: no model, GPU, FFmpeg or production jobs."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

SPEC=importlib.util.spec_from_file_location('native_optimization_bench',Path(__file__).resolve().parents[1]/'scripts/benchmark_native_optimization.py')
b=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(b)


class BenchTests(unittest.TestCase):
    def test_exact_clocks_and_lookahead(self):
        c=b.clock_bounds(5,125,131)
        self.assertEqual((c['output_frames'],c['audio_start_sample'],c['audio_sample_count'],c['lookahead_frames']),(300,9600,240000,1))
        self.assertEqual(b.clock_bounds(5,125,130)['lookahead_frames'],0)
        for start,count,full in [(1,125,200),(0,126,200),(-5,5,200),(0,255,300),(0,0,10),(5,125,129)]:
            with self.assertRaises(ValueError):b.clock_bounds(start,count,full)

    def test_new_output_bounds_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            post=Path(temp)/'post';base=post/'cache/scratch';base.mkdir(parents=True)
            for path in [base,post/'outside']:
                with self.assertRaises(ValueError):b.new_output(path,post)
            link=base/'link';link.symlink_to(Path(temp),target_is_directory=True)
            with self.assertRaises(ValueError):b.new_output(link/'escaped',post)
            out=b.new_output(base/'new',post)
            with self.assertRaises(ValueError):b.new_output(out,post)

    def test_variants_preserve_active_and_location(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=Path(temp)/'post/backend';backend.mkdir(parents=True)
            active=backend/'runtime.native-active.json';active.write_text('{"approved": true, "native_recipe": "fixed"}')
            before=active.read_bytes();paths=b.variant_configs(active,None,'test1')
            self.assertEqual(active.read_bytes(),before)
            self.assertEqual(len(paths),4)
            self.assertEqual([json.loads(p.read_text())['pipeline_prefetch_frames'] for _,p in paths],[1,1,1,2])
            for _,p in paths:
                self.assertEqual(p.parent,backend)
                self.assertEqual(p.stat().st_mode & 0o222,0)
                self.assertTrue(json.loads(p.read_text())['benchmark_native_frame_sha256'])
            with self.assertRaises(FileExistsError):b.variant_configs(active,None,'test1')
            with self.assertRaises(ValueError):b.variant_configs(active,None,'../escape')

    def test_frame_evidence_catches_missing_native_and_lookahead(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'frames.jsonl';clocks=b.clock_bounds(5,5,11)
            rows=[dict(source_frame=i,lookahead=i==10) for i in range(5,11)]
            p.write_text('\n'.join(map(json.dumps,rows)))
            result=b.frame_evidence(p,5,clocks)
            self.assertEqual(result['lost_native_frames'],0)
            self.assertIsNone(result['raw_native_hashes'])
            rows[-1]['source_frame']=9;p.write_text('\n'.join(map(json.dumps,rows)))
            with self.assertRaises(ValueError):b.frame_evidence(p,5,clocks)

    def test_token_requires_live_bound_owner_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'token.json'
            data=dict(owner_pid=123,owner_start_ticks='456',owner_terminal='/dev/pts/0',episode='S01E50',master_sha256='sha',backend_fingerprint='fp',production_paused=True,expires_unix=200)
            p.write_text(json.dumps(data));p.chmod(0o600)
            with patch.object(b,'process_identity',return_value=dict(start_ticks='456',terminal='/dev/pts/0',uid=b.os.getuid())):
                b.validate_token(p,'S01E50','sha','fp',now=100)
                with self.assertRaises(ValueError):b.validate_token(p,'S01E50','wrong','fp',now=100)
                with self.assertRaises(ValueError):b.validate_token(p,'S01E50','sha','fp',now=201)

    def test_comparison_does_not_claim_bitexact_native(self):
        left=dict(video_hashes=dict(frame_hashes=['a','b']),pipeline=dict(cuts=[5]),frame_evidence=dict(raw_native_hashes=None))
        right=dict(video_hashes=dict(frame_hashes=['a','c']),pipeline=dict(cuts=[5]),frame_evidence=dict(raw_native_hashes=None))
        result=b.compare(left,right)
        self.assertEqual(result['different_decoded_frames'],1)
        self.assertIsNone(result['raw_native_equal']);self.assertIsNone(result['pcm_equal'])

    def test_telemetry_keeps_latency_and_physical_names(self):
        with tempfile.TemporaryDirectory() as temp:
            t=b.Telemetry(Path(temp)/'gpu.jsonl')
            def query(*args,**kwargs):
                t.stop.set()
                return '0, GPU-A, NVIDIA RTX A4000, 100, 15000, 70, 10\n1, GPU-B, Tesla T10, 200, 16000, 80, 20\n'
            with patch.object(b.subprocess,'check_output',side_effect=query):t.collect()
            summary=t.summary();row=json.loads(t.path.read_text())
            self.assertEqual(summary['peaks']['GPU-B']['peak_temperature_c'],80)
            self.assertGreaterEqual(row['query_seconds'],0)

    def test_release_drains_both_devices_and_closes_lease_last(self):
        events=[]
        class Device:
            def __init__(self,index):self.index=index
            def __enter__(self):events.append(('device',self.index))
            def __exit__(self,*args):pass
        cuda=SimpleNamespace(synchronize=lambda d:events.append(('sync',d)),
            device=Device,empty_cache=lambda:events.append(('empty',)))
        processor=SimpleNamespace(torch=SimpleNamespace(cuda=cuda),cfg=dict(restore_gpu=0,gpu=1),
            face=SimpleNamespace(_lease=SimpleNamespace(close=lambda:events.append(('close',)))))
        with patch.object(b.gc,'collect',side_effect=lambda:events.append(('collect',))):
            b.release_models(processor)
        self.assertEqual(events,[('sync',0),('sync',1),('collect',),('device',0),('empty',),('device',1),('empty',),('close',)])
        self.assertEqual(processor.__dict__,{})

    def test_audio_sample_duration_uses_rational_clock(self):
        # No media subprocess: only the exact FLAC sample-duration arithmetic.
        for count in (55,110,115,135,205,220,230,245):
            samples=b.clock_bounds(0,count,250)['audio_sample_count']
            self.assertEqual(samples*b.Fraction('1/48000')*48000,samples)

    def test_case_selection_requires_serial_first_and_no_duplicates(self):
        self.assertEqual(b.selected_cases(['serial','overlap1-gate']),['serial','overlap1-gate'])
        for names in ([],['overlap1-gate'],['serial','serial'],['serial','bogus']):
            with self.assertRaises(ValueError):b.selected_cases(names)

    def test_external_baseline_pins_media_receipts_and_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);backend=root/'backend';backend.mkdir();suite=root/'suite';suite.mkdir()
            active=backend/'runtime.native-active.json';active.write_text('{}')
            config=b.variant_configs(active,None,'external')[0][1]
            clocks=b.clock_bounds(0,5,10)
            binding=dict(master_sha256='master',active_config_sha256=b.digest(active),recipe_sha256='recipe',recipe=dict(ass_sha256='ass'),prepared_frame_count=10,clocks=clocks)
            binding_path=suite/'input-binding.json';b.save(binding_path,binding)
            sample=suite/'sample.mkv';sample.write_bytes(b'original encoded bytes')
            frames=sample.with_name(sample.name+'.face-frames.jsonl')
            frames.write_text('\n'.join(json.dumps(dict(source_frame=i,lookahead=i==5,restored_bgr_sha256='a'*64)) for i in range(6)))
            pipeline=dict(episode='S01E50',source_start=0,input_frames=5,output_frames=12,lookahead_frames=1,native_recipe_sha256='recipe',ass_sha256='ass',pipeline_prefetch=False,resource_wait_in_process=False,video_encodes=1,ass_after_rife=True,coverage=dict(failed_frames=0,processed_frames=6,restored_frames=6,no_detection_frames=0),frame_log_sha256=b.digest(frames),cuts=[])
            receipt=suite/'pipeline.json';b.save(receipt,pipeline)
            decoded=suite/'decoded.sha256';decoded.write_text('\n'.join('0, 0, 0, 1, 12, '+('b'*64) for _ in range(12)))
            peaks={'GPU-A':dict(index=0,name='NVIDIA RTX A4000'),'GPU-B':dict(index=1,name='Tesla T10')}
            telemetry=suite/'gpu.jsonl';telemetry.write_text(json.dumps(dict(gpus=[dict(uuid=k,**v) for k,v in peaks.items()])))
            case=dict(name='serial',config=str(config),config_sha256=b.digest(config),backend_fingerprint='fp',gfpgan_exercised=True,pipeline=pipeline,
                validation=dict(sha256=b.digest(sample),frames=12,max_video_grid_error=0),frame_evidence=b.frame_evidence(frames,0,clocks),
                video_hashes=dict(path=str(decoded),sha256=b.digest(decoded),frames=12,frame_hashes=['b'*64]*12),
                attempts=[dict(status='CHECKED',output=str(sample),pipeline_binding=dict(pipeline_receipt_path=str(receipt),pipeline_receipt_sha256=b.digest(receipt)),telemetry=dict(path=str(telemetry),sha256=b.digest(telemetry),samples=1,errors=[],peaks=peaks))])
            case_path=suite/'serial.result.json';b.save(case_path,case)
            args=SimpleNamespace(external_serial_baseline=case_path,external_baseline_sha256=b.digest(case_path),external_baseline_binding=binding_path,external_binding_sha256=b.digest(binding_path),with_audio=False)
            engine=SimpleNamespace(config=lambda p:{})
            borrowed=b.external_baseline(args,binding,active,engine,lambda c:'fp')
            self.assertTrue(borrowed['borrowed']);self.assertTrue(borrowed['not_executed_this_suite'])
            self.assertEqual(b.selected_cases(['overlap1-gate'],True),['overlap1-gate'])
            with self.assertRaises(ValueError):b.selected_cases(['serial','overlap1-gate'],True)
            changed=dict(binding,master_sha256='changed')
            with self.assertRaises(ValueError):b.external_baseline(args,changed,active,engine,lambda c:'fp')
            sample.write_bytes(b'tampered')
            with self.assertRaises(ValueError):b.external_baseline(args,binding,active,engine,lambda c:'fp')

    def test_cold_wait_captures_raw_temperatures_and_bounded_failure(self):
        def row(a,t):return dict(gpus=[dict(index=0,name='NVIDIA RTX A4000',temperature_c=a),dict(index=1,name='Tesla T10',temperature_c=t)],unix=123)
        class FakeTelemetry:
            errors=[]
            rows=[]
            def __init__(self,path):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            @property
            def latest(self):return self.rows.pop(0) if len(self.rows)>1 else self.rows[0]
            def summary(self):return dict(samples=2)
        FakeTelemetry.rows=[row(86,79),row(85,78)]
        result={}
        with patch.object(b,'Telemetry',FakeTelemetry),patch.object(b.time,'sleep') as sleep:
            b.wait_cold_start('unused.jsonl',result)
        self.assertEqual(result['status'],'READY');sleep.assert_called_once()
        self.assertEqual(result['first_raw_sample']['gpus'][0]['temperature_c'],86)
        self.assertEqual(result['last_raw_sample']['gpus'][1]['temperature_c'],78)
        FakeTelemetry.rows=[row(86,79)];result={}
        with patch.object(b,'Telemetry',FakeTelemetry),patch.object(b.time,'monotonic',side_effect=[0,120,120]),patch.object(b.time,'sleep') as sleep:
            with self.assertRaises(TimeoutError):b.wait_cold_start('unused.jsonl',result)
        self.assertEqual(result['status'],'TIMEOUT');self.assertEqual(result['wait_seconds'],120)
        sleep.assert_not_called()

    def test_suite_loads_baseline_then_reuses_three_gate_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            post=Path(temp)/'post';(post/'backend').mkdir(parents=True);(post/'jobs').mkdir();(post/'cache/scratch').mkdir(parents=True)
            active=post/'backend/runtime.native-active.json';active.write_text('{}')
            master=Path(temp)/'master.mov';master.write_bytes(b'original master')
            recipe=dict(episode='S01E50',master_sha256=b.digest(master),scope='production')
            cfg=dict(post_root=str(post),restoration_backend='cuda_gfpgan_fused',native_recipe='recipe',native_recipe_sha256='r')
            class Processor:
                created=[]
                def __init__(self,c):
                    self.model_load_count=2;self.model_load_seconds=dict(face=.1,rife=.2)
                    self.gate=SimpleNamespace(summary=lambda:dict(waits=0)) if c.get('resource_wait_in_process') else None
                    self.created.append(self)
            class FakeTelemetry:
                def __init__(self,path):self.errors=[]
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def summary(self):return dict(peaks={'a':dict(index=0,name='NVIDIA RTX A4000'),'b':dict(index=1,name='Tesla T10')})
            restorers=[]
            def run(master,sample,episode,c,**kwargs):
                restorers.append(kwargs['restorer']);sample.write_bytes(b'encoded')
                sample.with_name(sample.name+'.pipeline.json').write_text(json.dumps(dict(cuts=[],coverage=dict(failed_frames=0,restored_frames=1))))
                sample.with_name(sample.name+'.face-frames.jsonl').write_text('\n'.join(json.dumps(dict(source_frame=i,lookahead=i==5,restored_bgr_sha256='a'*64)) for i in range(6)))
            engine=SimpleNamespace(config=lambda p:dict(cfg,**json.loads(Path(p).read_text())),source_info=lambda p:dict(streams=[dict(codec_type='video',avg_frame_rate='25/1',width=1920,height=1080)]),source_packet_count=lambda p:10)
            modules={'enhance_backend':engine,'segmented_backend':SimpleNamespace(fingerprint=lambda c:'fp'),
                'native_face_recipe':SimpleNamespace(validate_recipe=lambda *a:recipe),
                'native_face_pipeline':SimpleNamespace(NativeFrames=Processor,run=run,validate_pipeline=lambda *a:{}),
                'face_rework_worker':SimpleNamespace(validate_video=lambda *a,**k:{}),
                'enhance_stage':SimpleNamespace(WaitGPU=type('WaitGPU',(RuntimeError,),{}),check_space=lambda *a:None),
                'storage_policy':SimpleNamespace(minimum_free_gib=lambda c:25)}
            args=SimpleNamespace(config=active,master=master,master_sha256=b.digest(master),episode='S01E50',backend_fingerprint='fp',source_start=0,source_frames=5,isolation_token='token',output=post/'cache/scratch/suite',run_id='cpu',with_audio=False,max_retries=0)
            with patch.dict(sys.modules,modules),patch.object(b,'validate_token',return_value={}),patch.object(b,'probe_compute'),patch.object(b,'release_models'),patch.object(b,'Telemetry',FakeTelemetry),patch.object(b,'media_hash',return_value=dict(frames=12,frame_hashes=['x']*12)):
                report=b.execute(args)
            self.assertEqual(len(Processor.created),2)
            self.assertIsNot(restorers[0],restorers[1]);self.assertIs(restorers[1],restorers[2]);self.assertIs(restorers[2],restorers[3])
            self.assertEqual(report['total_successful_model_loads'],4)
            self.assertEqual([c['attempts'][0]['model_loads_this_attempt'] for c in report['cases']],[2,2,0,0])
            self.assertFalse(report['production_approval'])
            self.assertGreaterEqual(report['complete_suite_wall_seconds'],sum(c['complete_wall_seconds'] for c in report['cases']))
            self.assertEqual(master.read_bytes(),b'original master');self.assertEqual(active.read_text(),'{}')
            # Selected cold cases each wait before a fresh initialization.
            args.output=post/'cache/scratch/cold';args.run_id='cpu-cold'
            args.cases=['serial','overlap1-gate'];args.cold_start_each_case=True
            def cold_wait(path,result):result.update(status='READY',wait_seconds=.1,last_raw_sample=dict(temperatures=[85,78]))
            with patch.dict(sys.modules,modules),patch.object(b,'validate_token',return_value={}),patch.object(b,'probe_compute'),patch.object(b,'release_models') as cleanup,patch.object(b,'wait_cold_start',side_effect=cold_wait) as cooling,patch.object(b,'Telemetry',FakeTelemetry),patch.object(b,'media_hash',return_value=dict(frames=12,frame_hashes=['x']*12)):
                cold_report=b.execute(args)
            self.assertEqual(cooling.call_count,2);self.assertEqual(cleanup.call_count,2)
            self.assertEqual([c['name'] for c in cold_report['cases']],['serial','overlap1-gate'])
            self.assertEqual([c['attempts'][0]['model_loads_this_attempt'] for c in cold_report['cases']],[2,2])
            self.assertTrue(all(not c['attempts'][0]['model_reused'] for c in cold_report['cases']))
            self.assertTrue(all(c['cold_start_wait']['status']=='READY' for c in cold_report['cases']))
            # A fresh-process overlap borrows the pinned serial; its loads/wall stay historical.
            args.output=post/'cache/scratch/external';args.run_id='cpu-external';args.cases=['overlap1-gate']
            args.external_serial_baseline='serial.json';args.external_baseline_sha256='sha'
            args.external_baseline_binding='binding.json';args.external_binding_sha256='binding-sha'
            borrowed=json.loads(json.dumps(cold_report['cases'][0]))
            borrowed.update(borrowed=True,externally_completed=True,not_executed_this_suite=True)
            with patch.dict(sys.modules,modules),patch.object(b,'validate_token',return_value={}),patch.object(b,'probe_compute'),patch.object(b,'release_models'),patch.object(b,'wait_cold_start',side_effect=cold_wait),patch.object(b,'external_baseline',return_value=borrowed),patch.object(b,'Telemetry',FakeTelemetry),patch.object(b,'media_hash',return_value=dict(frames=12,frame_hashes=['x']*12)):
                external_report=b.execute(args)
            self.assertEqual([c['name'] for c in external_report['cases']],['serial','overlap1-gate'])
            self.assertTrue(external_report['cases'][0]['not_executed_this_suite'])
            self.assertEqual(external_report['total_successful_model_loads'],2)
            self.assertEqual(external_report['cases'][1]['attempts'][0]['model_loads_this_attempt'],2)
            self.assertIn('exclude',external_report['external_baseline_scope'])
            args.external_serial_baseline=None;args.external_baseline_sha256=None
            args.external_baseline_binding=None;args.external_binding_sha256=None
            args.cases=None;args.cold_start_each_case=False
            # Explicit bounded retry retains both failures and records wasted wall.
            args.output=post/'cache/scratch/failed';args.run_id='cpu-failed';args.max_retries=1
            wait=modules['enhance_stage'].WaitGPU
            failed_run=Mock(side_effect=wait('thermal recovery timeout'))
            modules['native_face_pipeline'].run=failed_run
            with patch.dict(sys.modules,modules),patch.object(b,'validate_token',return_value={}),patch.object(b,'probe_compute'),patch.object(b,'release_models'),patch.object(b,'Telemetry',FakeTelemetry):
                with self.assertRaises(wait):b.execute(args)
            failed=json.loads((args.output/'result.json').read_text())
            self.assertEqual(failed_run.call_count,2)
            self.assertEqual(failed['status'],'FAILED')
            self.assertEqual(len(failed['cases'][0]['attempts']),2)
            self.assertTrue(failed['cases'][0]['attempts'][0]['retry_authorized'])
            self.assertFalse(failed['cases'][0]['attempts'][1]['retry_authorized'])
            self.assertIsNone(failed['lost_native_frames'])
            self.assertTrue((args.output/'serial-attempt0').is_dir())
            self.assertTrue((args.output/'serial-attempt1').is_dir())
            # A partial constructor has no assigned owner: never retry in-process.
            args.output=post/'cache/scratch/init-failed';args.run_id='cpu-init-failed'
            constructor=Mock(side_effect=wait('partial RIFE initialization'))
            modules['native_face_pipeline'].NativeFrames=constructor
            with patch.dict(sys.modules,modules),patch.object(b,'validate_token',return_value={}),patch.object(b,'probe_compute'),patch.object(b,'release_models') as cleanup,patch.object(b,'Telemetry',FakeTelemetry):
                with self.assertRaises(wait):b.execute(args)
                cleanup.assert_not_called()
            failed=json.loads((args.output/'result.json').read_text())
            self.assertEqual(constructor.call_count,1)
            self.assertTrue(failed['requires_process_exit'])
            self.assertTrue(failed['cases'][0]['attempts'][0]['init_failed'])
            self.assertFalse(failed['cases'][0]['attempts'][0]['retry_authorized'])
            # Cleanup failure is secondary evidence; preserve the original run error.
            args.output=post/'cache/scratch/cleanup-failed';args.run_id='cpu-cleanup-failed'
            modules['native_face_pipeline'].NativeFrames=Processor
            failed_run.reset_mock()
            with patch.dict(sys.modules,modules),patch.object(b,'validate_token',return_value={}),patch.object(b,'probe_compute'),patch.object(b,'release_models',side_effect=RuntimeError('cleanup marker')),patch.object(b,'Telemetry',FakeTelemetry):
                with self.assertRaisesRegex(wait,'thermal recovery timeout'):b.execute(args)
            failed=json.loads((args.output/'result.json').read_text())
            self.assertEqual(failed_run.call_count,1)
            self.assertIn('thermal recovery timeout',failed['error'])
            self.assertIn('cleanup marker',failed['cases'][0]['attempts'][0]['cleanup_error'])
            self.assertTrue(failed['requires_process_exit'])


if __name__=='__main__':unittest.main()
