#!/usr/bin/env python3
"""Prepare pinned native face mode; activation needs real bounded acceptance.

Does not download models, change environments, launch GPU work or start jobs.
"""
from storage_policy import minimum_free_gib
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shlex

from enhance_stage import atomic_json,sha256
from face_frame_processor import WEIGHTS
from cuda_trt import validate_assets

SOURCES=['enhance_backend.py','enhance_stage.py','segmented_backend.py','concat_transfer.py',
    'linux_stage_handoff.py','cuda_trt.py','native_face_pipeline.py','native_face_recipe.py',
    'native_frame_queue.py','native_resource_gate.py',
    'native_face_job.py','benchmark_native_face.py','face_frame_processor.py',
    'render_face_samples.py','server_worker.py','subtitle_timing.py','font_preflight.py','prepare_native_face_backend.py','storage_policy.py','stage_storage.py']


def template(post):
    post=Path(post).absolute();base=json.loads((post/'backend/runtime.trt.json').read_text())
    _,_,provenance=validate_assets(base)
    paths=[base[k] for k in ['rife_trt_engine','rife_trt_model','rife_trt_provenance','rife_onnx']]
    paths += [provenance[key] for key in ['source_model','conversion_recipe','build_recipe','benchmark_report','conversion_report']]
    weights=post/'tools/face-candidates/weights'
    for name,expected in WEIGHTS.items():
        if sha256(weights/name)!=expected:raise ValueError('Changed official face weight '+name)
        paths.append(str(weights/name))
    paths += [str(Path(__file__).with_name(name).resolve()) for name in SOURCES]
    paths.append(str(Path(__file__).parent.parent/'kwjd.py'))
    packages=['torch','torchvision','basicsr','facexlib','gfpgan','numpy','opencv-python-headless']
    versions={p:importlib.metadata.version(p) for p in packages}
    for package in ['basicsr','facexlib','gfpgan']:
        location=Path(next(iter(importlib.util.find_spec(package).submodule_search_locations)))
        paths += [str(p) for p in location.rglob('*.py')]
    cfg={key:value for key,value in base.items() if not key.startswith('srvgg_')}
    cfg.update(restoration_backend='cuda_gfpgan_fused',restore=True,face_weights=str(weights),
        face_versions=versions,restore_gpu=0,restore_gpu_name='NVIDIA RTX A4000',
        restore_memory_gib=2.5,restore_free_reserve_gib=3.0,light_filter='clarity',max_video_mbps=16,
        segment_source_frames=250,pipeline_prefetch=False,
        scratch=str(post/'cache/scratch'),minimum_free_gib=minimum_free_gib(),
        file_sha256={p:sha256(p) for p in sorted(set(paths))})
    cfg.pop('native_recipe',None);cfg.pop('native_recipe_sha256',None)
    return cfg


def prepare(post,recipe=None,validation=None,activate=False):
    post=Path(post).absolute();cfg=template(post)
    output=post/'backend/runtime.native-template.json'
    if output.exists() and json.loads(output.read_text())!=cfg:
        archive=post/'backend/native-template-history';archive.mkdir(exist_ok=True)
        saved=archive/(sha256(output)+'.json')
        if not saved.exists():saved.write_bytes(output.read_bytes())
    atomic_json(output,cfg)
    source=Path(__file__).with_name('segmented_backend.py').resolve()
    launcher=('#!/bin/sh\nset -eu\nexec '+shlex.quote(str(post/'face-venv/bin/python'))+' '+
        shlex.quote(str(source))+' "$@" --config '+shlex.quote(str(post/'backend/runtime.native-active.json'))+'\n')
    candidate=post/'backend/enhance.native.sh'
    if candidate.exists() and candidate.read_text()!=launcher:raise ValueError('Existing native launcher differs')
    if not candidate.exists():candidate.write_text(launcher);candidate.chmod(0o700)
    result=dict(template=str(output),template_sha256=sha256(output),launcher=str(candidate),activated=False)
    if recipe:
        from native_face_recipe import validate_recipe
        data=validate_recipe(recipe,sha256(recipe))
        cfg.update(native_recipe=str(Path(recipe).absolute()),native_recipe_sha256=sha256(recipe))
        if data['scope']=='bounded_test':cfg['native_test_only']=True
        active=post/'backend/runtime.native-active.json';atomic_json(active,cfg)
        result['candidate_config']=str(active)
    if activate:
        # New defaults apply only to fresh jobs. Do not strand an in-flight
        # legacy checkpoint by replacing its validated launcher/fingerprint.
        for job_path in (post/'jobs').glob('S01E*.json'):
            job=json.loads(job_path.read_text(encoding='utf-8-sig'))
            if job.get('state')=='READY':continue
            episode=job_path.stem
            for saved in [post/'cache'/episode/(episode+'.source.ready.json'),
                    post/'cache'/episode/(episode+'.enhanced.mkv.ready.json')]:
                if saved.exists():
                    old=json.loads(saved.read_text());old=old.get('enhanced_checkpoint',old)
                    if old.get('output_kind')!='ass_styled_video':
                        raise ValueError('Unfinished legacy job must retain its backend before native activation: '+episode)
        if not validation:raise ValueError('Actual technical and visual acceptance report required')
        proof=json.loads(Path(validation).read_text())
        expected={name:sha256(Path(__file__).with_name(name)) for name in SOURCES}
        if (proof.get('technical_checked') is not True or proof.get('visual_checked') is not True
                or proof.get('pipeline')!='native_light_gfpgan_rife_ass_one_encode'
                or proof.get('code_sha256')!=expected):
            raise ValueError('Actual native pipeline proof is missing/stale')
        main=post/'backend/enhance.sh';archive=post/'backend/native-activation-history';archive.mkdir(exist_ok=True)
        if main.exists():
            saved=archive/(sha256(main)+'.sh')
            if not saved.exists():saved.write_bytes(main.read_bytes());saved.chmod(0o700)
        temporary=post/'backend/enhance.native.activate.tmp';temporary.write_text(launcher);temporary.chmod(0o700)
        os.replace(temporary,main)
        atomic_json(post/'backend/native-enabled.json',dict(enabled=True,template_sha256=sha256(output),
            validation_report=str(Path(validation).absolute()),validation_report_sha256=sha256(validation),
            launcher_sha256=sha256(main),pipeline=proof['pipeline']))
        result['activated']=True
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',type=Path,default=Path.home()/'qingqingwu/post')
    parser.add_argument('--recipe',type=Path)
    parser.add_argument('--validation',type=Path)
    parser.add_argument('--activate',action='store_true')
    args=parser.parse_args();print(json.dumps(prepare(args.post,args.recipe,args.validation,args.activate)))
