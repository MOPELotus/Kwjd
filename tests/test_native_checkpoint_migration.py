"""CPU fixtures only: no GPU, decoder, live checkpoint or activation calls."""
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import native_checkpoint_migration as migration


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name)/'post';self.old=self.post/'cache/old-context';self.old.mkdir(parents=True)
        self.target=self.post/'cache/scratch/isolated/new-context'
        self.master=self.post/'incoming/master.mov';self.master.parent.mkdir();self.master.write_bytes(b'unmodified native master')
        self.master_sha=migration.sha(self.master);self.old_fp='a'*64;self.new_fp='b'*64
        self.ass=self.post/'cache/native-style/S01E50.ass';self.ass.parent.mkdir();self.ass.write_bytes(b'approved derived ASS')
        srt=self.post/'S01E50.srt';srt.write_bytes(b'original subtitle')
        style=self.post/'styles/template.ass';style.parent.mkdir();style.write_bytes(b'approved style')
        font=self.post/'styles/fonts/subtitle.ttf';font.parent.mkdir();font.write_bytes(b'font')
        fonts={font.name:migration.sha(font)}
        write(srt.with_name(srt.name+'.ready.json'),dict(reviewed=True,sha256=migration.sha(srt)))
        write(style.with_name(style.name+'.ready.json'),dict(approved=True,sha256=migration.sha(style),master_sha256=self.master_sha,fonts=fonts))
        self.recipe_path=self.post/'cache/native-style/recipe.json'
        from native_face_recipe import create_recipe
        create_recipe(self.recipe_path,post=self.post,episode='S01E50',master_sha256=self.master_sha,
            ass=self.ass,srt=srt,style=style,fonts=fonts,timing={'offset_ms':0,'receipt_sha256':None})
        context=dict(episode='S01E50',master_sha256=self.master_sha,backend_fingerprint=self.old_fp,
                     input_frames=10,output_frames=24,segment_source_frames=5)
        write(self.old/'context.json',context)
        pins=[]
        for index in range(2):
            media=self.old/f'{index:06d}.mkv';media.write_bytes(('original compressed media '+str(index)).encode())
            log=self.old/f'{index:06d}.partial.mkv.face-frames.jsonl'
            count=6 if index==0 else 5
            log.write_text(''.join(json.dumps(dict(source_frame=index*5+i,faces_detected=1,faces_restored=1))+'\n' for i in range(count)))
            pipeline=self.old/f'{index:06d}.partial.mkv.pipeline.json'
            data=dict(status='PROCESSED_PENDING_DECODE',episode='S01E50',source_start=index*5,input_frames=5,
                output_frames=12,lookahead_frames=int(index==0),native_recipe_sha256=migration.sha(self.recipe_path),
                ass_sha256=migration.sha(self.ass),video_encodes=1,ass_after_rife=True,frame_log_sha256=migration.sha(log),
                coverage=dict(processed_frames=count,failed_frames=0,restored_frames=count,no_detection_frames=0))
            write(pipeline,data)
            ready=self.old/f'{index:06d}.ready.json'
            write(ready,{**context,'index':index,'source_start':index*5,'source_frames':5,'frames':12,
                'sha256':migration.sha(media),'pipeline_receipt_path':str(pipeline),'pipeline_receipt_sha256':migration.sha(pipeline)})
            pins.append(dict(index=index,media_sha256=migration.sha(media),receipt_sha256=migration.sha(ready),
                pipeline_sha256=migration.sha(pipeline),frame_log_sha256=migration.sha(log)))
        cases=[]
        for name in ('serial','overlap1-gate'):
            folder=self.post/'reports/bench'/name;folder.mkdir(parents=True)
            sample=folder/'sample.mkv';sample.write_bytes(('bounded media '+name).encode())
            log=sample.with_name(sample.name+'.face-frames.jsonl')
            hashes=['c'*64]*6
            log.write_text(''.join(json.dumps(dict(source_frame=100+i,restored_bgr_sha256=h,lookahead=i>=5))+'\n' for i,h in enumerate(hashes)))
            decoded=folder/'decoded.sha256';decoded.write_text('actual decoded-frame digest fixture')
            mux=folder/'sample-with-native-mix.mkv';mux.write_bytes(b'verified same native PCM mux')
            (folder/'mux-pcm.sha256').write_text('SHA256='+'f'*64)
            cases.append(dict(name=name,backend_fingerprint='d'*64 if name=='serial' else 'e'*64,
                attempts=[dict(status='CHECKED',output=str(sample))],
                validation=dict(frames=12,max_video_grid_error=.000334,sha256=migration.sha(sample)),
                frame_evidence=dict(sha256=migration.sha(log),raw_native_hashes=hashes),
                video_hashes=dict(path=str(decoded),sha256=migration.sha(decoded),frames=12),
                continuous_sample=str(mux),audio_validation=dict(frames=12,max_video_grid_error=.000334,sha256=migration.sha(mux)),
                gfpgan_exercised=True,pcm_sha256='SHA256='+'f'*64,pipeline=dict(cuts=[103])))
        self.benchmark=self.post/'reports/benchmark.json'
        self.report=dict(episode='S01E50',master_sha256=self.master_sha,
            status='TECHNICAL_BENCHMARK_COMPLETE_VISUAL_REVIEW_PENDING',lost_native_frames=0,
            source_start=100,clocks=dict(input_frames=5,lookahead_frames=1,output_frames=12),cases=cases)
        write(self.benchmark,self.report)
        self.review=self.post/'reports/guo-review.json'
        self.proof=dict(schema_version=1,kind='native-checkpoint-migration-authorization',issuer='root-controller',
            approved=True,activation=False,quality_scope='Guo only',episode='S01E50',master_sha256=self.master_sha,
            master_path=str(self.master),old_backend_fingerprint=self.old_fp,new_backend_fingerprint=self.new_fp,
            source_context=str(self.old),source_context_sha256=migration.sha(self.old/'context.json'),
            target_context=str(self.target),post_root=str(self.post),input_frames=10,segment_source_frames=5,
            recipe_path=str(self.recipe_path),recipe_sha256=migration.sha(self.recipe_path),segments=pins,
            benchmark_path=str(self.benchmark),benchmark_cases=['serial','overlap1-gate'],
            benchmark_case_fingerprints={c['name']:c['backend_fingerprint'] for c in cases},quality_review_path=str(self.review))
        self.authorization=self.post/'reports/root-migration-approval.json'
        self.seal()

    def seal(self):
        self.proof['benchmark_sha256']=migration.sha(self.benchmark)
        write(self.review,dict(issuer='root-controller',scope='Guo only',passed=True,benchmark_sha256=self.proof['benchmark_sha256']))
        self.proof['quality_review_sha256']=migration.sha(self.review)
        write(self.authorization,self.proof)
        self.authorization.chmod(0o600)
        self.auth_sha=migration.sha(self.authorization)

    def run_migration(self):return migration.migrate(self.authorization,self.auth_sha)

    def assert_rejected(self):
        with self.assertRaises((ValueError,KeyError)):self.run_migration()
        self.assertFalse(self.target.exists())

    def test_hardlinks_preserve_every_old_asset_and_producer_identity(self):
        originals={p:p.read_bytes() for p in self.old.iterdir()}
        self.assertEqual(self.run_migration(),self.target)
        for path,data in originals.items():self.assertEqual(path.read_bytes(),data)
        for index in range(2):
            old=self.old/f'{index:06d}.mkv';new=self.target/old.name
            self.assertEqual((old.stat().st_dev,old.stat().st_ino),(new.stat().st_dev,new.stat().st_ino))
            receipt=migration.load(self.target/f'{index:06d}.ready.json')
            self.assertEqual(receipt['producer_backend_fingerprint'],self.old_fp)
            self.assertEqual(receipt['backend_fingerprint'],self.new_fp)
            self.assertEqual(receipt['pipeline_receipt_path'],str(self.old/f'{index:06d}.partial.mkv.pipeline.json'))
            self.assertFalse(receipt['activation']);self.assertFalse(receipt['recomputed'])
            self.assertEqual(migration.sha(receipt['migration_provenance_path']),receipt['migration_provenance_sha256'])
        root=migration.load(self.target/'migration.provenance.json')
        self.assertFalse(root['validator_adoption_supported']);self.assertFalse(root['production_approval'])
        self.assertEqual(root['quality_scope'],'Guo only')

    def test_existing_candidate_is_never_overwritten(self):
        self.run_migration();before=(self.target/'context.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'NEW isolated'):self.run_migration()
        self.assertEqual((self.target/'context.json').read_bytes(),before)

    def test_external_authorization_hash_and_issuer_are_required(self):
        self.authorization.write_text('{}');self.assert_rejected()
        self.proof['issuer']='self-approval';self.seal();self.assert_rejected()

    def test_activation_or_unscoped_quality_is_rejected(self):
        for field,value in [('activation',True),('quality_scope','all faces'),('approved',False)]:
            original=self.proof[field];self.proof[field]=value;self.seal();self.assert_rejected();self.proof[field]=original

    def test_target_cannot_be_live_context_or_symlink(self):
        self.proof['target_context']=str(self.post/'cache/S01E50/new-context');self.seal();self.assert_rejected()
        self.proof['target_context']=str(self.target)
        self.target.parent.mkdir(parents=True);self.target.symlink_to(self.old,target_is_directory=True);self.seal()
        with self.assertRaises(ValueError):self.run_migration()
        self.assertTrue(self.target.is_symlink())

    def test_changed_media_or_context_never_adopted(self):
        media=self.old/'000000.mkv';original=media.read_bytes();media.write_bytes(b'changed')
        self.assert_rejected();media.write_bytes(original)
        (self.old/'context.json').write_text('{}');self.assert_rejected()

    def test_master_ass_and_pipeline_tampering_rejected(self):
        for path in (self.master,self.ass,self.old/'000000.partial.mkv.pipeline.json'):
            original=path.read_bytes();path.write_bytes(b'changed');self.assert_rejected();path.write_bytes(original)

    def test_frame_order_checked_even_when_hashes_are_reauthorized(self):
        log=self.old/'000000.partial.mkv.face-frames.jsonl'
        rows=[json.loads(line) for line in log.read_text().splitlines()];rows[1]['source_frame']=0
        log.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        pipeline=self.old/'000000.partial.mkv.pipeline.json';data=migration.load(pipeline)
        data['frame_log_sha256']=migration.sha(log);write(pipeline,data)
        ready=self.old/'000000.ready.json';data=migration.load(ready)
        data['pipeline_receipt_sha256']=migration.sha(pipeline);write(ready,data)
        self.proof['segments'][0].update(frame_log_sha256=migration.sha(log),pipeline_sha256=migration.sha(pipeline),receipt_sha256=migration.sha(ready))
        self.seal();self.assert_rejected()

    def test_prefix_indices_must_be_explicit_contiguous(self):
        self.proof['segments']=self.proof['segments'][1:];self.seal();self.assert_rejected()

    def test_benchmark_raw_cut_pcm_or_pts_mismatch_rejected(self):
        baseline=copy.deepcopy(self.report)
        for change in ('raw','cuts','pcm','pts'):
            self.report=copy.deepcopy(baseline);case=self.report['cases'][1]
            if change=='raw':case['frame_evidence']['raw_native_hashes'][0]='0'*64
            if change=='cuts':case['pipeline']['cuts']=[]
            if change=='pcm':case['pcm_sha256']='SHA256='+'0'*64
            if change=='pts':case['validation']['max_video_grid_error']=.2
            write(self.benchmark,self.report);self.seal()
            with self.subTest(change=change):self.assert_rejected()

    def test_authorization_permissions_cannot_allow_other_users_to_write(self):
        self.authorization.chmod(0o666);self.assert_rejected()

    def deployment_symlinks(self):
        physical=Path(self.tmp.name)/'var-tmp/post/cache';physical.parent.mkdir(parents=True)
        shutil.move(str(self.post/'cache'),str(physical))
        (self.post/'cache').symlink_to(physical,target_is_directory=True)
        incoming=Path(self.tmp.name)/'post-home/incoming';incoming.parent.mkdir()
        shutil.move(str(self.post/'incoming'),str(incoming))
        (self.post/'incoming').symlink_to(incoming,target_is_directory=True)
        return physical

    def test_real_cache_and_incoming_symlink_layout_preserves_literal_provenance(self):
        physical=self.deployment_symlinks()
        result=self.run_migration()
        self.assertEqual(result,self.target.resolve())
        self.assertTrue(result.is_relative_to(physical/'scratch'))
        record=migration.load(result/'000000.ready.json')
        self.assertEqual(record['pipeline_receipt_path'],str(self.old/'000000.partial.mkv.pipeline.json'))
        self.assertEqual(record['original_pipeline_literal_path'],record['pipeline_receipt_path'])
        self.assertEqual(record['original_ready_literal_path'],str(self.old/'000000.ready.json'))
        self.assertEqual((result/'000000.mkv').stat().st_ino,(self.old/'000000.mkv').stat().st_ino)

    def test_pipeline_symlink_parent_escape_rejected_even_with_equal_bytes(self):
        outside=Path(self.tmp.name)/'unrelated';outside.mkdir()
        pipeline=self.old/'000000.partial.mkv.pipeline.json'
        shutil.copyfile(pipeline,outside/pipeline.name)
        alias=self.post/'cache/unrelated-link';alias.symlink_to(outside,target_is_directory=True)
        ready=self.old/'000000.ready.json';data=migration.load(ready)
        data['pipeline_receipt_path']=str(alias/pipeline.name);write(ready,data)
        self.proof['segments'][0]['receipt_sha256']=migration.sha(ready);self.seal()
        self.assert_rejected()

    def test_cache_symlink_substitution_after_verification_is_rejected(self):
        physical=self.deployment_symlinks()
        proof,context,recipe,_=migration.verify_authorization(self.authorization,self.auth_sha)
        other=Path(self.tmp.name)/'replacement-cache';shutil.copytree(physical,other)
        (self.post/'cache').unlink();(self.post/'cache').symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink changed'):
            migration.verify_segment(proof,context,recipe,proof['segments'][0])

    def test_target_cannot_use_symlink_below_canonical_scratch(self):
        self.deployment_symlinks()
        scratch=(self.post/'cache/scratch');scratch.mkdir()
        (scratch/'other').mkdir();(scratch/'isolated').symlink_to(scratch/'other',target_is_directory=True)
        self.assert_rejected()

    def production_fixture(self):
        self.target=self.post/'cache/S01E50/S01E50.segments'/(self.master_sha[:16]+'-'+self.new_fp[:16])
        self.proof.update(kind='native-checkpoint-production-migration-authorization',activation=True,target_context=str(self.target))
        recipe=migration.load(self.recipe_path)
        self.cfg=dict(post_root=str(self.post),restoration_backend='cuda_gfpgan_fused',native_recipe=str(self.recipe_path),
            native_recipe_sha256=migration.sha(self.recipe_path),segment_source_frames=5,
            benchmark_native_frame_sha256=False,file_sha256={},
            fused_subtitle_binding={k:recipe[k] for k in ['master_sha256','srt_sha256','style_sha256','fonts','subtitle_timing','ass_sha256']})
        cfgpath=self.post/'backend/runtime.promoted.json';write(cfgpath,self.cfg)
        self.proof.update(new_config_path=str(cfgpath),new_config_sha256=migration.sha(cfgpath))
        variant=self.post/'backend/runtime.trial.json'
        write(variant,{**self.cfg,'benchmark_native_frame_sha256':True});variant.chmod(0o444)
        source_root=Path(migration.__file__).resolve().parents[1]
        sources={str(Path('scripts')/name):migration.sha(source_root/'scripts'/name) for name in migration.CORE_SOURCES}
        source_sha=hashlib.sha256(json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        segments=[]
        for index in range(2):
            folder=self.post/f'reports/long-trial/segment-{index}';folder.mkdir(parents=True)
            media=folder/'styled-video.mkv';media.write_bytes(f'trial segment{index}'.encode())
            start=index*2;count=2;frames=5
            rows=[dict(source_frame=start+i,lookahead=i==count,restored_bgr_sha256='c'*64) for i in range(3)]
            log=media.with_name(media.name+'.face-frames.jsonl');log.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            pipeline=dict(status='PROCESSED_PENDING_DECODE',source_start=start,input_frames=count,lookahead_frames=1,
                native_recipe_sha256=migration.sha(self.recipe_path),ass_sha256=migration.sha(self.ass),video_encodes=1,ass_after_rife=True,
                coverage=dict(processed_frames=3,failed_frames=0,restored_frames=3,no_detection_frames=0),frame_log_sha256=migration.sha(log))
            write(media.with_name(media.name+'.pipeline.json'),pipeline)
            pcm=folder/'source-mix-pcm.sha256';pcm.write_text('SHA256='+'f'*64)
            (folder/'mux-pcm.sha256').write_text(pcm.read_text())
            mux=folder/'sample-with-native-mix.mkv';mux.write_bytes(f'physical trial mux{index}'.encode())
            segments.append(dict(source_start=start,input_frames=count,output_frames=frames,status='TECHNICALLY_CHECKED',output=str(media),
                validation=dict(frames=frames,max_video_grid_error=.000334,sha256=migration.sha(media)),frame_rows=rows,
                frame_evidence=dict(sha256=migration.sha(log),raw_native_hashes=['c'*64]*3),
                mux_validation=dict(frames=frames,max_video_grid_error=.000334,sha256=migration.sha(mux)),
                continuous_sample=str(mux),mixed_pcm_equal=True,source_pcm_sha256=pcm.read_text(),mux_pcm_sha256=pcm.read_text(),
                source_pcm_evidence_path=str(pcm),source_pcm_evidence_sha256=migration.sha(pcm)))
        self.trial=dict(status='TECHNICAL_LONG_TRIAL_COMPLETE_VISUAL_REVIEW_PENDING',
            binding=dict(episode='S01E50',master_sha256=self.master_sha,backend_fingerprint='9'*64,
                recipe_sha256=migration.sha(self.recipe_path),variant=str(variant),variant_sha256=migration.sha(variant),
                source_root=str(source_root),source_files=sources,source_sha256=source_sha),segments=segments,
            union=dict(lost_primary_frames=0,duplicate_primary_frames=0,unique_primary_count=4))
        self.trial_path=self.post/'reports/long-trial/result.json';write(self.trial_path,self.trial)
        image=self.post/'reports/long-trial/guo.png';image.write_bytes(b'CPU fixture for viewed image identity')
        self.collection=dict(renderer='NVIDIA RTX A4000/PCIe/SSE2',hwdec='nvdec-copy',file_identity_unchanged=True,
            sha256=segments[0]['mux_validation']['sha256'],screenshots=[dict(image=str(image),sha256=migration.sha(image))],
            realtime_playback=dict(positions=[dict(position=0),dict(position=1)]))
        self.collection_path=self.post/'reports/long-trial/collection.json';write(self.collection_path,self.collection)
        self.production_review=self.post/'reports/long-trial/root-guo-review.json'
        self.seal_production()

    def seal_production(self):
        self.proof.update(long_trial_path=str(self.trial_path),long_trial_sha256=migration.sha(self.trial_path),
            physical_collection_path=str(self.collection_path),physical_collection_sha256=migration.sha(self.collection_path))
        write(self.production_review,dict(issuer='root-controller',scope='Guo only',passed=True,
            long_trial_sha256=self.proof['long_trial_sha256'],physical_collection_sha256=self.proof['physical_collection_sha256']))
        self.proof.update(production_quality_review_path=str(self.production_review),production_quality_review_sha256=migration.sha(self.production_review))
        self.seal()

    def migrate_production(self,fp=None):
        with patch.object(migration,'new_config_identity',return_value=(self.cfg,fp or self.new_fp)):
            return migration.migrate(self.authorization,self.auth_sha,allow_production=True)

    def test_production_requires_separate_authorization_kind_and_cli_opt_in(self):
        self.production_fixture()
        with self.assertRaisesRegex(ValueError,'CLI opt-in'):self.run_migration()
        self.proof['kind']='native-checkpoint-migration-authorization';self.seal()
        with self.assertRaises(ValueError):self.migrate_production()
        self.assertFalse(self.target.exists())

    def test_production_hardlinks_adopt_with_normal_native_validator_preserving_old_producer(self):
        self.production_fixture();old_bytes={p:p.read_bytes() for p in self.old.iterdir()}
        result=self.migrate_production()
        from native_face_pipeline import validate_pipeline
        for index in range(2):
            ready=migration.load(result/f'{index:06d}.ready.json')
            partial=self.target/f'{index:06d}.partial.mkv'
            evidence=validate_pipeline(partial,self.cfg,index*5,5,10)
            self.assertTrue(all(ready.get(k)==v for k,v in evidence.items()))
            self.assertEqual(ready['producer_backend_fingerprint'],self.old_fp)
            self.assertEqual(ready['backend_fingerprint'],self.new_fp)
            self.assertFalse(ready['recomputed']);self.assertTrue(ready['activation'])
            for suffix in ('.mkv','.partial.mkv.pipeline.json','.partial.mkv.face-frames.jsonl'):
                a=self.old/f'{index:06d}{suffix}';b=result/a.name
                self.assertEqual(a.stat().st_ino,b.stat().st_ino)
        for path,data in old_bytes.items():self.assertEqual(path.read_bytes(),data)
        self.assertFalse(migration.load(result/'migration.provenance.json')['backend_activation_performed'])
        provenance=migration.load(result/'migration.provenance.json')
        self.assertEqual(provenance['actual_trial_backend_fingerprint'],'9'*64)
        self.assertEqual(provenance['actual_production_backend_fingerprint'],self.new_fp)

    def test_production_symlink_layout_keeps_new_logical_pipeline_path(self):
        self.production_fixture();self.deployment_symlinks()
        result=self.migrate_production()
        from native_face_pipeline import validate_pipeline
        evidence=validate_pipeline(self.target/'000000.partial.mkv',self.cfg,0,5,10)
        self.assertEqual(migration.load(result/'000000.ready.json')['pipeline_receipt_path'],evidence['pipeline_receipt_path'])

    def test_production_rejects_wrong_config_fingerprint_or_target(self):
        self.production_fixture()
        with self.assertRaises(ValueError):self.migrate_production(fp='0'*64)
        self.proof['target_context']=str(self.post/'cache/wrong-context');self.seal()
        with self.assertRaisesRegex(ValueError,'exact new backend context'):self.migrate_production()

    def test_production_rejects_failed_long_trial_or_changed_pcm(self):
        self.production_fixture();original=copy.deepcopy(self.trial)
        for change in ('failed','pcm'):
            self.trial=copy.deepcopy(original)
            if change=='failed':self.trial['status']='FAILED'
            else:self.trial['segments'][0]['mixed_pcm_equal']=False
            write(self.trial_path,self.trial);self.seal_production()
            with self.subTest(change=change),self.assertRaises(ValueError):self.migrate_production()

    def test_production_rejects_unreviewed_or_software_physical_collection(self):
        self.production_fixture()
        data=migration.load(self.production_review);data['passed']=False;write(self.production_review,data)
        self.proof['production_quality_review_sha256']=migration.sha(self.production_review);self.seal()
        with self.assertRaisesRegex(ValueError,'root review'):self.migrate_production()
        self.collection['renderer']='llvmpipe';write(self.collection_path,self.collection);self.seal_production()
        with self.assertRaisesRegex(ValueError,'physical NVIDIA'):self.migrate_production()

    def test_production_rejects_any_non_allowlisted_raw_config_drift(self):
        self.production_fixture()
        for key,value in [('light_filter','changed'),('pipeline_prefetch',True),('unknown_extra_setting',1)]:
            original=copy.deepcopy(self.cfg);self.cfg[key]=value
            write(Path(self.proof['new_config_path']),self.cfg)
            self.proof['new_config_sha256']=migration.sha(self.proof['new_config_path']);self.seal()
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'drift outside allowlist'):self.migrate_production()
            self.cfg=original

    def test_production_rejects_changed_model_or_installed_package_pins(self):
        self.production_fixture();self.cfg['file_sha256']={'/models/official.pth':'a'*64,'/site-packages/model.py':'b'*64}
        write(Path(self.proof['new_config_path']),self.cfg);self.proof['new_config_sha256']=migration.sha(self.proof['new_config_path']);self.seal()
        with self.assertRaisesRegex(ValueError,'Model/asset/installed-package'):self.migrate_production()

    def test_production_rejects_changed_used_core_source_even_with_resealed_trial(self):
        self.production_fixture();b=self.trial['binding'];b['source_files']['scripts/native_face_pipeline.py']='0'*64
        b['source_sha256']=hashlib.sha256(json.dumps(b['source_files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        write(self.trial_path,self.trial);self.seal_production()
        with self.assertRaisesRegex(ValueError,'Evidence SHA changed'):self.migrate_production()

    def test_config_equivalence_allows_only_explicit_repo_provenance_and_default_additions(self):
        self.production_fixture();b=self.trial['binding'];variant=Path(b['variant']);raw=migration.load(variant)
        source=str(Path(migration.__file__).resolve().parent/'native_face_pipeline.py')
        raw['file_sha256']={source:'0'*64};variant.chmod(0o644);write(variant,raw);variant.chmod(0o444)
        b['variant_sha256']=migration.sha(variant)
        self.proof['approved_repo_source_provenance_paths']=[source]
        self.cfg['file_sha256']={source:migration.sha(source)}
        self.cfg.update(resource_wait_poll_seconds=1.5,resource_wait_timeout_seconds=120)
        write(Path(self.proof['new_config_path']),self.cfg);self.proof['new_config_sha256']=migration.sha(self.proof['new_config_path'])
        write(self.trial_path,self.trial);self.seal_production()
        result=self.migrate_production();root=migration.load(result/'migration.provenance.json')
        changes=root['config_equivalence_differences']
        self.assertEqual({c['key'] for c in changes},{'benchmark_native_frame_sha256','file_sha256','resource_wait_poll_seconds','resource_wait_timeout_seconds'})

    def test_config_provenance_exceptions_cannot_hide_installed_package_source(self):
        self.production_fixture();self.proof['approved_repo_source_provenance_paths']=['/site-packages/native_face_pipeline.py'];self.seal()
        with self.assertRaisesRegex(ValueError,'exact approved repo'):self.migrate_production()


if __name__=='__main__':unittest.main()
