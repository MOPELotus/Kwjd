#!/usr/bin/env python3
"""Explicitly authorized native checkpoint provenance migration; no backend launch.

Consumes an external root-controller authorization by caller-pinned SHA. This is
local controller authorization, not a cryptographic signature. Old assets and
receipts remain byte-identical. Prior segment full-decode/frame-count acceptance
is inherited from hash-pinned producer receipts; no fresh prefix decode or claim
of full-prefix PTS/visual acceptance is made. Bounded benchmark evidence is checked
separately. Scratch candidates remain non-adoptable. A separate production
authorization plus CLI opt-in permits ordinary validators to adopt hash-identical
pipeline/log hardlinks while retaining the original producer fingerprint.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

CORE_SOURCES=('native_face_pipeline.py','native_frame_queue.py','native_resource_gate.py',
    'face_frame_processor.py','native_face_recipe.py','cuda_trt.py','cuda_pipeline.py',
    'enhance_backend.py','enhance_stage.py','segmented_backend.py','concat_transfer.py',
    'render_face_samples.py','face_rework_worker.py','storage_policy.py','master_policy.py',
    'linux_stage_handoff.py','benchmark_native_optimization.py')
PROVENANCE_SOURCES=tuple(sorted(set(CORE_SOURCES)|{
    'native_face_job.py','benchmark_native_face.py','server_worker.py','subtitle_timing.py',
    'font_preflight.py','prepare_native_face_backend.py','stage_storage.py'}))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def regular(path):
    path=Path(path)
    # Deployment intentionally symlinks post/cache and post/incoming. Resolve
    # those existing parents explicitly, while refusing leaf link substitutions.
    if not path.is_absolute() or '..' in path.parts or path.is_symlink():
        raise ValueError('Evidence must use absolute regular-file paths')
    path=path.resolve(strict=True);info=path.stat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('Evidence is not a regular file')
    return path


def bound(path,digest):
    path=regular(path)
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or sha(path)!=digest:
        raise ValueError('Evidence SHA changed: '+str(path))
    return path


def load(path):return json.loads(Path(path).read_text())


def output_count(count):return (count*12+4)//5


def integer(value,minimum=0):
    if type(value) is not int or value<minimum:raise ValueError('Invalid integer clock/count')
    return value


def verify_benchmark(proof):
    report=load(bound(proof['benchmark_path'],proof['benchmark_sha256']))
    if (report.get('episode')!=proof['episode'] or report.get('master_sha256')!=proof['master_sha256']
            or report.get('status')!='TECHNICAL_BENCHMARK_COMPLETE_VISUAL_REVIEW_PENDING'
            or report.get('lost_native_frames')!=0):
        raise ValueError('Benchmark is not complete/current-master evidence')
    cases={c['name']:c for c in report['cases']}
    selected=[cases[name] for name in proof['benchmark_cases']]
    if len(selected)!=2 or selected[0] is selected[1]:raise ValueError('Two explicit comparison cases required')
    raw=[];cuts=[];pcm=[]
    frames=integer(report['clocks']['output_frames'],1)
    for case in selected:
        if case.get('backend_fingerprint')!=proof['benchmark_case_fingerprints'][case['name']]:
            raise ValueError('Benchmark case fingerprint differs')
        validation=case['validation']
        error=validation.get('max_video_grid_error')
        if (validation.get('frames')!=frames or type(error) not in (float,int)
                or not math.isfinite(error) or not 0<=error<=.0011):
            raise ValueError('Benchmark lacks full-decode/PTS clock evidence')
        attempts=case['attempts'];checked=[a for a in attempts if a.get('status')=='CHECKED']
        if len(checked)!=1:raise ValueError('Benchmark case lacks unique completed attempt')
        media=bound(checked[0]['output'],validation['sha256'])
        logs=media.with_name(media.name+'.face-frames.jsonl')
        bound(logs,case['frame_evidence']['sha256'])
        rows=[json.loads(line) for line in logs.read_text().splitlines()]
        count=integer(report['clocks']['input_frames'],1);lookahead=integer(report['clocks']['lookahead_frames'])
        start=integer(report['source_start'])
        if (count%5 or start%5 or lookahead not in (0,1) or frames!=output_count(count)
                or [r['source_frame'] for r in rows]!=list(range(start,start+count+lookahead))
                or [r.get('lookahead') for r in rows]!=[i>=count for i in range(len(rows))]):
            raise ValueError('Benchmark native raw evidence has frame loss')
        hashes=[r.get('restored_bgr_sha256') for r in rows]
        if (not hashes or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes)
                or hashes!=case['frame_evidence'].get('raw_native_hashes')):
            raise ValueError('Benchmark native hashes missing or changed')
        video=case['video_hashes'];bound(video['path'],video['sha256'])
        if video.get('frames')!=frames:raise ValueError('Benchmark decoded hash count differs')
        if case.get('gfpgan_exercised') is not True:raise ValueError('Benchmark did not exercise GFPGAN')
        mix=case.get('pcm_sha256')
        if not isinstance(mix,str) or not re.fullmatch(r'SHA256=[0-9a-f]{64}',mix):
            raise ValueError('Benchmark native PCM equivalence evidence missing')
        audio=case.get('audio_validation',{})
        audio_error=audio.get('max_video_grid_error')
        if (audio.get('frames')!=frames or type(audio_error) not in (int,float)
                or not math.isfinite(audio_error) or not 0<=audio_error<=.0011):
            raise ValueError('Benchmark audio mux decode/PTS evidence missing')
        bound(case['continuous_sample'],audio['sha256'])
        if regular(media.parent/'mux-pcm.sha256').read_text().strip()!=mix:
            raise ValueError('Benchmark PCM digest artifact differs')
        raw.append(hashes);cuts.append(case['pipeline']['cuts']);pcm.append(mix)
    if raw[0]!=raw[1] or cuts[0]!=cuts[1] or pcm[0]!=pcm[1]:
        raise ValueError('Benchmark raw/cut/PCM equivalence failed')
    review=load(bound(proof['quality_review_path'],proof['quality_review_sha256']))
    if (review.get('issuer')!='root-controller' or review.get('scope')!='Guo only'
            or review.get('passed') is not True or review.get('benchmark_sha256')!=proof['benchmark_sha256']):
        raise ValueError('Explicit Guo-only controller quality review required')
    return dict(benchmark_sha256=proof['benchmark_sha256'],quality_scope='Guo only',
        equivalence_scope='Bounded benchmark native raw hashes/cuts/full decode/PTS/PCM; not full-prefix or final visual acceptance')


def new_config_identity(path):
    import enhance_backend
    from segmented_backend import fingerprint
    cfg=enhance_backend.config(path)
    return cfg,fingerprint(cfg)


def verify_config_equivalence(proof,production_raw,trial_binding):
    """Allow only diagnostic hash disablement and explicitly named repo metadata.

    Assets, installed package .py pins, numerical settings and every other key
    remain identical. The actual 17 used source bytes must equal the trial.
    """
    variant=bound(trial_binding['variant'],trial_binding['variant_sha256'])
    if variant.stat().st_mode&0o222:raise ValueError('Trial variant must remain immutable')
    trial_raw=load(variant)
    current_root=Path(__file__).resolve().parents[1]
    expected={str(Path('scripts')/name) for name in CORE_SOURCES}
    sources=trial_binding['source_files']
    aggregate=hashlib.sha256(json.dumps(sources,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if set(sources)!=expected or aggregate!=trial_binding['source_sha256']:
        raise ValueError('Trial core source manifest/aggregate differs')
    for name,digest in sources.items():bound(current_root/name,digest)
    if (trial_raw.get('benchmark_native_frame_sha256') is not True
            or production_raw.get('benchmark_native_frame_sha256') is not False):
        raise ValueError('Only trial native hashing True to production False is permitted')
    allowed=set()
    roots={current_root,Path(trial_binding['source_root']).resolve(strict=True)}
    if any(not (root/'.git').exists() for root in roots):raise ValueError('Core provenance roots must be actual repositories')
    if proof.get('legacy_repo_root'):
        legacy=Path(proof['legacy_repo_root']).resolve(strict=True)
        if not (legacy/'.git').exists():raise ValueError('Legacy provenance source root is not a repository')
        roots.add(legacy)
    possible={str(root/'scripts'/name) for root in roots for name in PROVENANCE_SOURCES}
    possible|={str(root/'kwjd.py') for root in roots}
    for path in proof.get('approved_repo_source_provenance_paths',[]):
        if path not in possible:raise ValueError('Provenance exception is not an exact approved repo core source path')
        allowed.add(path)
    oldpins=trial_raw.get('file_sha256');newpins=production_raw.get('file_sha256')
    if not isinstance(oldpins,dict) or not isinstance(newpins,dict):raise ValueError('Both raw file SHA maps required')
    if ({p:h for p,h in oldpins.items() if p not in allowed}
            !={p:h for p,h in newpins.items() if p not in allowed}):
        raise ValueError('Model/asset/installed-package SHA pins changed')
    differences=[]
    for path in sorted(set(oldpins)|set(newpins)):
        if oldpins.get(path)!=newpins.get(path):
            differences.append(dict(key='file_sha256',path=path,trial=oldpins.get(path),production=newpins.get(path),
                reason='Explicit repository provenance metadata only; actual used 17 core sources separately SHA-equal to trial'))
    ignored={'benchmark_native_frame_sha256','file_sha256'}
    for key in sorted((set(trial_raw)|set(production_raw))-ignored):
        old=trial_raw.get(key);new=production_raw.get(key)
        if key in ('resource_wait_poll_seconds','resource_wait_timeout_seconds') and key not in trial_raw:
            default=1.5 if key=='resource_wait_poll_seconds' else 120
            if type(new) in (int,float) and new==default:
                differences.append(dict(key=key,trial='implicit default',production=new,reason='Explicit unchanged pipeline default'))
                continue
        if key not in trial_raw or key not in production_raw or type(old)!=type(new) or old!=new:
            raise ValueError('Production configuration drift outside allowlist: '+key)
    differences.append(dict(key='benchmark_native_frame_sha256',trial=True,production=False,
        reason='Disable diagnostic BGR hashing only; no pixel/model/frame/clock change'))
    return dict(trial_variant_path=str(variant),trial_variant_sha256=trial_binding['variant_sha256'],
        actual_trial_backend_fingerprint=trial_binding['backend_fingerprint'],
        actual_production_backend_fingerprint=proof['new_backend_fingerprint'],
        used_core_source_sha256=aggregate,config_equivalence_differences=differences)


def verify_production(proof,recipe):
    config=bound(proof['new_config_path'],proof['new_config_sha256'])
    if config.parent!=(Path(proof['post_root'])/'backend').resolve(strict=True):
        raise ValueError('New config is outside the bound post/backend')
    cfg,fp=new_config_identity(config)
    binding={key:recipe[key] for key in ['master_sha256','srt_sha256','style_sha256','fonts','subtitle_timing','ass_sha256']}
    if (fp!=proof['new_backend_fingerprint'] or cfg.get('restoration_backend')!='cuda_gfpgan_fused'
            or Path(cfg['post_root']).resolve()!=Path(proof['post_root']).resolve()
            or cfg.get('native_recipe_sha256')!=proof['recipe_sha256']
            or Path(cfg['native_recipe']).resolve()!=Path(proof['recipe_path']).resolve()
            or cfg.get('fused_subtitle_binding')!=binding
            or cfg.get('segment_source_frames',250)!=proof['segment_source_frames']):
        raise ValueError('New production config/fingerprint/recipe/clock differs')
    trial=load(bound(proof['long_trial_path'],proof['long_trial_sha256']))
    b=trial['binding'];segments=trial['segments'];union=trial['union']
    if (trial.get('status')!='TECHNICAL_LONG_TRIAL_COMPLETE_VISUAL_REVIEW_PENDING'
            or b.get('episode')!=proof['episode'] or b.get('master_sha256')!=proof['master_sha256']
            or not re.fullmatch('[0-9a-f]{64}',b.get('backend_fingerprint','')) or b.get('recipe_sha256')!=proof['recipe_sha256']
            or len(segments)<2
            or union.get('lost_primary_frames')!=0 or union.get('duplicate_primary_frames')!=0):
        raise ValueError('Long trial is incomplete or bound to another production context')
    equivalence=verify_config_equivalence(proof,load(config),b)
    primaries=[];last=None
    for segment in segments:
        count=integer(segment['input_frames'],1);start=integer(segment['source_start']);frames=output_count(count)
        v=segment['validation'];err=v.get('max_video_grid_error')
        if (segment.get('status')!='TECHNICALLY_CHECKED' or segment.get('output_frames')!=frames
                or v.get('frames')!=frames or type(err) not in (int,float)
                or not math.isfinite(err) or not 0<=err<=.0011):
            raise ValueError('Long trial segment lacks full-decode/PTS acceptance')
        media=bound(segment['output'],v['sha256'])
        log=bound(media.with_name(media.name+'.face-frames.jsonl'),segment['frame_evidence']['sha256'])
        rows=[json.loads(line) for line in log.read_text().splitlines()]
        if (rows!=segment['frame_rows'] or [r['source_frame'] for r in rows]!=list(range(start,start+count+1))
                or [r.get('lookahead') for r in rows]!=[i==count for i in range(count+1)]
                or [r.get('restored_bgr_sha256') for r in rows]!=segment['frame_evidence']['raw_native_hashes']):
            raise ValueError('Long trial native rows/hashes/order differ')
        if last is not None and (last['source_frame']!=rows[0]['source_frame']
                or last['restored_bgr_sha256']!=rows[0]['restored_bgr_sha256']):
            raise ValueError('Long trial boundary lookahead equivalence failed')
        last=rows[-1];primaries.extend(r['source_frame'] for r in rows[:-1])
        from native_face_pipeline import validate_pipeline
        validate_pipeline(media,cfg,start,count,proof['input_frames'])
        audio=segment['mux_validation'];aerr=audio.get('max_video_grid_error')
        if (audio.get('frames')!=frames or type(aerr) not in (int,float)
                or not math.isfinite(aerr) or not 0<=aerr<=.0011
                or segment.get('mixed_pcm_equal') is not True
                or segment.get('source_pcm_sha256')!=segment.get('mux_pcm_sha256')):
            raise ValueError('Long trial PCM/mux acceptance failed')
        bound(segment['continuous_sample'],audio['sha256'])
        source_pcm=bound(segment['source_pcm_evidence_path'],segment['source_pcm_evidence_sha256'])
        if (source_pcm.read_text().strip()!=segment['source_pcm_sha256']
                or regular(media.parent/'mux-pcm.sha256').read_text().strip()!=segment['mux_pcm_sha256']):
            raise ValueError('Long trial PCM digest artifact changed')
    if (not primaries or primaries!=list(range(primaries[0],primaries[0]+len(primaries)))
            or union.get('unique_primary_count')!=len(primaries)):
        raise ValueError('Long trial union count/order differs')
    collection=load(bound(proof['physical_collection_path'],proof['physical_collection_sha256']))
    if (not isinstance(collection.get('renderer'),str) or 'NVIDIA' not in collection['renderer']
            or any(name in collection['renderer'].lower() for name in ('llvmpipe','lavapipe','swiftshader','software'))
            or collection.get('hwdec') not in ('nvdec','nvdec-copy')
            or collection.get('file_identity_unchanged') is not True or not collection.get('screenshots')):
        raise ValueError('Actual physical NVIDIA playback collection required')
    positions=collection.get('realtime_playback',{}).get('positions',[])
    if len(positions)<2 or positions[-1]['position']<=positions[0]['position']:
        raise ValueError('Physical collection lacks advancing actual playback evidence')
    if collection.get('sha256') not in {s['mux_validation']['sha256'] for s in segments}:
        raise ValueError('Physical playback collection belongs to another sample')
    for shot in collection['screenshots']:bound(shot['image'],shot['sha256'])
    review=load(bound(proof['production_quality_review_path'],proof['production_quality_review_sha256']))
    if (review.get('issuer')!='root-controller' or review.get('scope')!='Guo only'
            or review.get('passed') is not True or review.get('long_trial_sha256')!=proof['long_trial_sha256']
            or review.get('physical_collection_sha256')!=proof['physical_collection_sha256']):
        raise ValueError('Explicit actual Guo-only root review of this trial/collection required')
    return dict(new_config_path=str(config),new_config_sha256=proof['new_config_sha256'],
        long_trial_sha256=proof['long_trial_sha256'],physical_collection_sha256=proof['physical_collection_sha256'],
        production_quality_review_sha256=proof['production_quality_review_sha256'],**equivalence)


def verify_authorization(path,digest,*,allow_production=False):
    path=bound(path,digest);info=path.stat()
    if info.st_uid!=os.getuid() or info.st_mode&0o022:
        raise ValueError('Controller authorization must be same-user and not group/world writable')
    proof=load(path)
    production=proof.get('activation') is True
    if production and not allow_production:raise ValueError('Explicit production migration CLI opt-in required')
    kind='native-checkpoint-production-migration-authorization' if production else 'native-checkpoint-migration-authorization'
    if (proof.get('schema_version')!=1 or proof.get('kind')!=kind
            or proof.get('issuer')!='root-controller' or proof.get('approved') is not True
            or type(proof.get('activation')) is not bool or proof.get('quality_scope')!='Guo only'):
        raise ValueError('External root-controller scoped migration authorization required')
    for key in ('old_backend_fingerprint','new_backend_fingerprint','master_sha256','recipe_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',proof.get(key,'')):raise ValueError('Invalid fingerprint/SHA')
    if proof['old_backend_fingerprint']==proof['new_backend_fingerprint']:raise ValueError('Distinct fingerprints required')
    if not re.fullmatch(r'S01E\d{2,3}',proof.get('episode','')):raise ValueError('Invalid episode')
    source=Path(proof['source_context']);target=Path(proof['target_context']);post=Path(proof['post_root'])
    if not all(p.is_absolute() for p in (source,target,post)):raise ValueError('Absolute context paths required')
    logical_scratch=post/'cache/scratch';scratch=logical_scratch.resolve()
    resolved_target=target.resolve()
    if production:
        expected=post/'cache'/proof['episode']/(proof['episode']+'.segments')/(proof['master_sha256'][:16]+'-'+proof['new_backend_fingerprint'][:16])
        if target!=expected:raise ValueError('Production target must be the exact new backend context')
        base=(post/'cache').resolve();relative=target.relative_to(post/'cache');current=base
    else:
        relative=(target.relative_to(logical_scratch) if target.is_relative_to(logical_scratch)
              else target.relative_to(scratch) if target.is_relative_to(scratch) else None)
        base=scratch;current=scratch
    linked=False
    for part in (() if relative is None else relative.parts):
        current=current/part;linked=linked or current.is_symlink()
    if (target.exists() or target.is_symlink() or '..' in target.parts
            or relative is None or linked
            or not resolved_target.is_relative_to(base) or resolved_target==base):
        raise ValueError('Target must be a NEW isolated candidate under post/cache/scratch')
    source=source.resolve(strict=True)
    if not source.is_relative_to((post/'cache').resolve(strict=True)):
        raise ValueError('Source context escaped the canonical cache')
    proof['_resolved_source_context']=str(source)
    proof['_resolved_target_context']=str(resolved_target)
    context=load(bound(source/'context.json',proof['source_context_sha256']))
    expected=dict(episode=proof['episode'],master_sha256=proof['master_sha256'],
        backend_fingerprint=proof['old_backend_fingerprint'],input_frames=proof['input_frames'],
        output_frames=output_count(integer(proof['input_frames'],1)),segment_source_frames=proof['segment_source_frames'])
    size=integer(proof['segment_source_frames'],5)
    if size%5 or context!=expected:raise ValueError('Source context/clock differs')
    master=bound(proof['master_path'],proof['master_sha256'])
    if not master.is_relative_to((post/'incoming').resolve(strict=True)):
        raise ValueError('Master escaped the canonical incoming directory')
    proof['_resolved_master_path']=str(master)
    proof['_resolved_recipe_path']=str(bound(proof['recipe_path'],proof['recipe_sha256']))
    from native_face_recipe import validate_recipe
    recipe=validate_recipe(proof['recipe_path'],proof['recipe_sha256'])
    if (recipe['episode']!=proof['episode'] or recipe['master_sha256']!=proof['master_sha256']
            or recipe.get('scope')!='production' or recipe.get('program_origin_seconds')!=0):
        raise ValueError('Production master/recipe clock differs')
    evidence=verify_benchmark(proof)
    if production:evidence.update(verify_production(proof,recipe))
    return proof,context,recipe,evidence


def verify_segment(proof,context,recipe,pin):
    source=Path(proof['_resolved_source_context']);index=integer(pin['index']);size=context['segment_source_frames']
    if Path(proof['source_context']).resolve(strict=True)!=source:
        raise ValueError('Source context symlink changed after authorization')
    start=index*size;count=min(size,context['input_frames']-start)
    if count<=0:raise ValueError('Segment outside master')
    media=bound(source/f'{index:06d}.mkv',pin['media_sha256'])
    ready=bound(source/f'{index:06d}.ready.json',pin['receipt_sha256']);record=load(ready)
    expected={**context,'index':index,'source_start':start,'source_frames':count,'frames':output_count(count),'sha256':pin['media_sha256']}
    if any(record.get(k)!=v for k,v in expected.items()):raise ValueError('Producer ready binding differs')
    pipeline=bound(record['pipeline_receipt_path'],pin['pipeline_sha256'])
    if (pipeline.parent!=source or pipeline.name!=f'{index:06d}.partial.mkv.pipeline.json'
            or record['pipeline_receipt_sha256']!=pin['pipeline_sha256']):
        raise ValueError('Original pipeline path/hash differs')
    data=load(pipeline);lookahead=int(start+count<context['input_frames']);coverage=data['coverage']
    if (data.get('status')!='PROCESSED_PENDING_DECODE' or data.get('episode')!=proof['episode']
            or data.get('source_start')!=start or data.get('input_frames')!=count
            or data.get('output_frames')!=output_count(count) or data.get('lookahead_frames')!=lookahead
            or data.get('native_recipe_sha256')!=proof['recipe_sha256'] or data.get('ass_sha256')!=recipe['ass_sha256']
            or data.get('video_encodes')!=1 or data.get('ass_after_rife') is not True
            or coverage.get('failed_frames')!=0 or coverage.get('processed_frames')!=count+lookahead
            or coverage.get('restored_frames',-1)+coverage.get('no_detection_frames',-1)!=count+lookahead):
        raise ValueError('Original pipeline coverage/ASS/clock differs')
    log=pipeline.with_name(pipeline.name.replace('.pipeline.json','.face-frames.jsonl'))
    bound(log,pin['frame_log_sha256'])
    if data['frame_log_sha256']!=pin['frame_log_sha256']:raise ValueError('Frame log producer hash differs')
    rows=[json.loads(line) for line in log.read_text().splitlines()]
    if [row['source_frame'] for row in rows]!=list(range(start,start+count+lookahead)):
        raise ValueError('Producer frame order/count differs')
    if any(row.get('faces_detected')!=row.get('faces_restored') for row in rows):
        raise ValueError('Producer per-frame face coverage differs')
    return media,ready,record,pipeline,log


def publish(path,data):
    # Exclusive creation only; interrupted candidate remains evidence, no overwrite.
    with Path(path).open('x') as out:json.dump(data,out,indent=2,sort_keys=True);out.write('\n')


def verify_resolved_bindings(proof):
    for literal,canonical in [('source_context','_resolved_source_context'),
                              ('target_context','_resolved_target_context'),
                              ('master_path','_resolved_master_path'),
                              ('recipe_path','_resolved_recipe_path')]:
        if str(Path(proof[literal]).resolve(strict=literal!='target_context'))!=proof[canonical]:
            raise ValueError('Authorized evidence symlink resolution changed: '+literal)


def migrate(authorization,authorization_sha256,*,allow_production=False):
    proof,context,recipe,evidence=verify_authorization(authorization,authorization_sha256,allow_production=allow_production)
    production=proof['activation']
    pins=proof['segments']
    if not pins or [p['index'] for p in pins]!=list(range(len(pins))):
        raise ValueError('Explicit contiguous completed prefix required')
    records=[verify_segment(proof,context,recipe,pin) for pin in pins]
    target=Path(proof['_resolved_target_context'])
    # Verify one filesystem before any target creation; preserve original hardlinks.
    parent=target.parent
    while not parent.exists():parent=parent.parent
    hardlinks=[row[index] for row in records for index in ((0,3,4) if production else (0,))]
    if any(path.stat().st_dev!=parent.stat().st_dev for path in hardlinks):
        raise ValueError('Candidate hardlink migration requires the same filesystem')
    if sha(authorization)!=authorization_sha256:raise ValueError('Authorization raced verification')
    verify_resolved_bindings(proof)
    target.mkdir(parents=True,exist_ok=False)
    root=dict(schema_version=1,kind='native-checkpoint-production-migration' if production else 'native-checkpoint-migration-candidate',activation=production,
        production_approval=False,authorization_path=str(authorization),authorization_sha256=authorization_sha256,
        production_migration_authorized=production,backend_activation_performed=False,
        original_context_path=proof['source_context'],original_context_sha256=proof['source_context_sha256'],
        canonical_source_context=proof['_resolved_source_context'],canonical_target_context=str(target),
        canonical_master_path=proof['_resolved_master_path'],canonical_recipe_path=proof['_resolved_recipe_path'],
        producer_backend_fingerprint=proof['old_backend_fingerprint'],target_backend_fingerprint=proof['new_backend_fingerprint'],
        source_acceptance='Inherited producer full-decode/frame-count acceptance; media/receipt/pipeline/frame-log hashes and native frame order rechecked, no repeated prefix decode',
        validator_adoption_supported=production,**evidence)
    root_path=target/'migration.provenance.json';publish(root_path,root);root_sha=sha(root_path)
    publish(target/'context.json',{**context,'backend_fingerprint':proof['new_backend_fingerprint']})
    for pin,(media,ready,record,pipeline,log) in zip(pins,records):
        # Recheck mutable originals immediately before exposing each hardlink.
        verify_segment(proof,context,recipe,pin)
        link=target/media.name;os.link(media,link)
        if sha(link)!=pin['media_sha256']:raise ValueError('Media raced hardlink publication; candidate retained')
        migrated={**record,'backend_fingerprint':proof['new_backend_fingerprint'],
            'producer_backend_fingerprint':proof['old_backend_fingerprint'],
            'migration_provenance_path':str(root_path),'migration_provenance_sha256':root_sha,
            'original_ready_path':str(ready),'original_ready_sha256':pin['receipt_sha256'],
            'original_ready_literal_path':str(Path(proof['source_context'])/ready.name),
            'original_pipeline_literal_path':record['pipeline_receipt_path'],
            'original_frame_log_path':str(log),'original_frame_log_sha256':pin['frame_log_sha256'],
            'candidate_only':not production,'activation':production,'recomputed':False}
        if production:
            for original in (pipeline,log):
                copied=target/original.name;os.link(original,copied)
                if sha(copied)!=sha(original):raise ValueError('Pipeline/log changed during hardlink publication')
            migrated['pipeline_receipt_path']=str(Path(proof['target_context'])/pipeline.name)
        publish(target/ready.name,migrated)
    bound(authorization,authorization_sha256)
    verify_resolved_bindings(proof)
    bound(Path(proof['source_context'])/'context.json',proof['source_context_sha256'])
    from native_face_recipe import validate_recipe
    validate_recipe(proof['recipe_path'],proof['recipe_sha256'])
    if production:bound(proof['new_config_path'],proof['new_config_sha256'])
    publish(target/'migration.complete.json',dict(activation=production,production_approval=False,
        migrated_segments=len(records),migration_provenance_sha256=root_sha,
        limitation=('Production migration explicitly authorized; launcher/profile unchanged; original producer retained. '
            if production else 'Candidate assets only; existing validator must reject. ')
            +'No final approval or full-prefix new PTS/visual acceptance.'))
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorization',type=Path,required=True)
    parser.add_argument('--authorization-sha256',required=True)
    parser.add_argument('--allow-production-migration',action='store_true',help='Requires separate root-controller production authorization and actual long trial/physical review')
    args=parser.parse_args()
    print(migrate(args.authorization,args.authorization_sha256,allow_production=args.allow_production_migration))


if __name__=='__main__':main()
