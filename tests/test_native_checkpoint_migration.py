"""CPU fixtures only: no GPU, decoder, live checkpoint or activation calls."""
import copy
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest

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


if __name__=='__main__':unittest.main()
