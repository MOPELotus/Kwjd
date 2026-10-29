#!/usr/bin/env python3
"""Candidate-only native checkpoint provenance migration; never activates a backend.

Consumes an external root-controller authorization by caller-pinned SHA. This is
local controller authorization, not a cryptographic signature. Old assets and
receipts remain byte-identical. Prior segment full-decode/frame-count acceptance
is inherited from hash-pinned producer receipts; no fresh prefix decode or claim
of full-prefix PTS/visual acceptance is made. Bounded benchmark evidence is checked
separately. Existing native validators intentionally cannot adopt these receipts
without a future explicitly reviewed provenance-validation branch.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat


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


def verify_authorization(path,digest):
    path=bound(path,digest);info=path.stat()
    if info.st_uid!=os.getuid() or info.st_mode&0o022:
        raise ValueError('Controller authorization must be same-user and not group/world writable')
    proof=load(path)
    if (proof.get('schema_version')!=1 or proof.get('kind')!='native-checkpoint-migration-authorization'
            or proof.get('issuer')!='root-controller' or proof.get('approved') is not True
            or proof.get('activation') is not False or proof.get('quality_scope')!='Guo only'):
        raise ValueError('External root-controller candidate-only authorization required')
    for key in ('old_backend_fingerprint','new_backend_fingerprint','master_sha256','recipe_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',proof.get(key,'')):raise ValueError('Invalid fingerprint/SHA')
    if proof['old_backend_fingerprint']==proof['new_backend_fingerprint']:raise ValueError('Distinct fingerprints required')
    if not re.fullmatch(r'S01E\d{2,3}',proof.get('episode','')):raise ValueError('Invalid episode')
    source=Path(proof['source_context']);target=Path(proof['target_context']);post=Path(proof['post_root'])
    if not all(p.is_absolute() for p in (source,target,post)):raise ValueError('Absolute context paths required')
    logical_scratch=post/'cache/scratch';scratch=logical_scratch.resolve()
    resolved_target=target.resolve()
    relative=(target.relative_to(logical_scratch) if target.is_relative_to(logical_scratch)
              else target.relative_to(scratch) if target.is_relative_to(scratch) else None)
    current=scratch;linked=False
    for part in (() if relative is None else relative.parts):
        current=current/part;linked=linked or current.is_symlink()
    if (target.exists() or target.is_symlink() or '..' in target.parts
            or relative is None or linked
            or not resolved_target.is_relative_to(scratch) or resolved_target==scratch):
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


def migrate(authorization,authorization_sha256):
    proof,context,recipe,evidence=verify_authorization(authorization,authorization_sha256)
    pins=proof['segments']
    if not pins or [p['index'] for p in pins]!=list(range(len(pins))):
        raise ValueError('Explicit contiguous completed prefix required')
    records=[verify_segment(proof,context,recipe,pin) for pin in pins]
    target=Path(proof['_resolved_target_context'])
    # Verify one filesystem before any target creation; preserve original hardlinks.
    parent=target.parent
    while not parent.exists():parent=parent.parent
    if any(media.stat().st_dev!=parent.stat().st_dev for media,*_ in records):
        raise ValueError('Candidate hardlink migration requires the same filesystem')
    if sha(authorization)!=authorization_sha256:raise ValueError('Authorization raced verification')
    verify_resolved_bindings(proof)
    target.mkdir(parents=True,exist_ok=False)
    root=dict(schema_version=1,kind='native-checkpoint-migration-candidate',activation=False,
        production_approval=False,authorization_path=str(authorization),authorization_sha256=authorization_sha256,
        original_context_path=proof['source_context'],original_context_sha256=proof['source_context_sha256'],
        canonical_source_context=proof['_resolved_source_context'],canonical_target_context=str(target),
        canonical_master_path=proof['_resolved_master_path'],canonical_recipe_path=proof['_resolved_recipe_path'],
        producer_backend_fingerprint=proof['old_backend_fingerprint'],target_backend_fingerprint=proof['new_backend_fingerprint'],
        source_acceptance='Inherited producer full-decode/frame-count acceptance; media/receipt/pipeline/frame-log hashes and native frame order rechecked, no repeated prefix decode',
        validator_adoption_supported=False,**evidence)
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
            'candidate_only':True,'activation':False,'recomputed':False}
        publish(target/ready.name,migrated)
    bound(authorization,authorization_sha256)
    verify_resolved_bindings(proof)
    bound(Path(proof['source_context'])/'context.json',proof['source_context_sha256'])
    from native_face_recipe import validate_recipe
    validate_recipe(proof['recipe_path'],proof['recipe_sha256'])
    publish(target/'migration.complete.json',dict(activation=False,production_approval=False,
        migrated_segments=len(records),migration_provenance_sha256=root_sha,
        limitation='Candidate assets only. Existing validator must reject; no production/final approval or full-prefix new PTS/visual acceptance.'))
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorization',type=Path,required=True)
    parser.add_argument('--authorization-sha256',required=True)
    args=parser.parse_args()
    print(migrate(args.authorization,args.authorization_sha256))


if __name__=='__main__':main()
