"""Queue integration helpers for explicitly enabled one-encode native face mode."""
from argparse import Namespace
import hashlib
import json
import os
import math
from pathlib import Path
import subprocess

from enhance_stage import atomic_json,sha256,WaitDisk,WaitGPU
from subtitle_timing import read_mapping,apply_offset
from native_face_recipe import create_recipe,validate_recipe


def enabled(post):
    marker=Path(post)/'backend/native-enabled.json'
    if not marker.exists():return False
    data=json.loads(marker.read_text())
    if data.get('enabled') is not True:return False
    template=Path(post)/'backend/runtime.native-template.json'
    proof=Path(data['validation_report'])
    if (marker.is_symlink() or data.get('template_sha256')!=sha256(template)
            or data.get('validation_report_sha256')!=sha256(proof)
            or data.get('launcher_sha256')!=sha256(Path(post)/'backend/enhance.sh')):
        raise ValueError('Native mode activation proof/template changed')
    validation=json.loads(proof.read_text())
    if validation.get('technical_checked') is not True or validation.get('visual_checked') is not True:
        raise ValueError('Native mode has no actual technical and visual verification')
    return True


def prepare(post,episode,master_sha256,srt,style,font_hashes,log):
    """Prepare immutable approved ASS and current single-job runtime before NN."""
    from kwjd import cmd_make_ass
    from font_preflight import preflight
    post=Path(post);srt=Path(srt);style=Path(style)
    timing=read_mapping(post,episode,master_sha256,sha256(srt))
    identity=dict(master=master_sha256,srt=sha256(srt),style=sha256(style),fonts=font_hashes,timing=timing)
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    directory=post/'cache'/episode/'native-style'/key
    directory.mkdir(parents=True,exist_ok=True)
    ass=directory/(episode+'.ass')
    if not ass.exists():
        temp=directory/(episode+f'.{os.getpid()}.candidate.ass')
        if temp.exists():raise FileExistsError('Owned ASS candidate from prior attempt retained')
        cmd_make_ass(Namespace(srt=str(srt),template=str(style),output=str(temp)))
        audit=apply_offset(temp,timing['offset_ms'])
        atomic_json(directory/'subtitle-timing.applied.json',dict(mapping=timing,**audit))
        os.link(temp,ass);temp.unlink()
    font_evidence=preflight(style,post/'styles/fonts',directory,log)
    recipe_path=directory/'native.recipe.json'
    recipe=create_recipe(recipe_path,post=post,episode=episode,master_sha256=master_sha256,
        ass=ass,srt=srt,style=style,fonts=font_hashes,timing=timing)
    template=post/'backend/runtime.native-template.json'
    cfg=json.loads(template.read_text())
    cfg.update(native_recipe=str(recipe_path.absolute()),native_recipe_sha256=sha256(recipe_path))
    serialized=(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n').encode()
    config_hash=hashlib.sha256(serialized).hexdigest()
    archive=post/'backend/native-profiles';archive.mkdir(exist_ok=True)
    immutable=archive/(config_hash+'.json')
    if immutable.exists() and immutable.read_bytes()!=serialized:raise ValueError('Native profile archive conflict')
    if not immutable.exists():immutable.write_bytes(serialized)
    active=post/'backend/runtime.native-active.json'
    atomic_json(active,cfg)
    from enhance_backend import config
    tested=config(active)
    return dict(ass=ass,recipe=recipe,recipe_path=recipe_path,timing=timing,
        recipe_sha256=sha256(recipe_path),font_evidence=font_evidence,config_path=active,cfg=tested)


def check_checkpoint(receipt,context):
    if (receipt.get('native_recipe')!=str(context['recipe_path'])
            or receipt.get('native_recipe_sha256')!=context['recipe_sha256']):
        raise ValueError('Fused recipe publication binding changed')
    recipe=validate_recipe(context['recipe_path'],context['recipe_sha256'])
    expected={key:recipe[key] for key in ['master_sha256','srt_sha256','style_sha256','fonts','subtitle_timing','ass_sha256']}
    if (receipt.get('output_kind')!='ass_styled_video' or receipt.get('video_encodes')!=1
            or receipt.get('fused_subtitle_binding')!=expected):
        raise ValueError('Fused checkpoint belongs to different approvals or is not one-encode styled video')


def recover_context(receipt):
    path=Path(receipt['native_recipe']);digest=receipt['native_recipe_sha256']
    recipe=validate_recipe(path,digest)
    context=dict(recipe_path=path,recipe_sha256=digest,recipe=recipe,
        ass=Path(recipe['ass_path']),timing=recipe['subtitle_timing'])
    check_checkpoint(receipt,context)
    return context


def final_command(enhanced,mixed_audio,offset,temp):
    """Only stream copy: ASS was rendered after RIFE in the sole video encode."""
    return ['ffmpeg','-hide_banner','-n','-nostdin','-v','warning','-i',str(enhanced),
        '-itsoffset',str(offset),'-i',str(mixed_audio),'-map','0:v:0','-map','1:a:0',
        '-c','copy','-map_metadata','-1',str(temp)]


def calibrate(master,post,episode,context,log):
    """Actual bounded current-master test; exit75/76 preserves normal wait states."""
    cfg=context['cfg'];post=Path(post)
    ready=post/'backend/production-ready.json'
    backend=post/'backend/enhance.sh'
    plan=json.loads(subprocess.check_output([str(backend),'--plan',str(master)],text=True))
    target=Path(cfg['scratch'])/'native-calibration'/episode/plan['backend_fingerprint']
    target.parent.mkdir(parents=True,exist_ok=True)
    if (target/'result.json').exists():
        result=json.loads((target/'result.json').read_text())
        if result.get('backend_fingerprint')!=plan['backend_fingerprint'] or result.get('technical_checked') is not True:
            raise ValueError('Existing native calibration differs')
        if sha256(target/'sample.mkv')!=result['sample_sha256']:raise ValueError('Calibration media changed')
    else:
        command=[str(post/'face-venv/bin/python'),str(Path(__file__).with_name('benchmark_native_face.py')),
            '--master',str(master),'--config',str(context['config_path']),'--output',str(target),
            '--backend-fingerprint',plan['backend_fingerprint']]
        with Path(log).open('a') as stream:
            proc=subprocess.run(command,stdout=stream,stderr=stream)
        if proc.returncode==75:raise WaitDisk('Native per-master calibration waits for disk')
        if proc.returncode==76:raise WaitGPU('Native per-master calibration waits for shared GPU')
        if proc.returncode:raise RuntimeError('Native per-master calibration failed; see log')
        result=json.loads((target/'result.json').read_text())
    from face_rework_worker import validate_video
    if (result.get('master_sha256')!=context['recipe']['master_sha256']
            or result.get('backend_fingerprint')!=plan['backend_fingerprint']
            or result.get('technical_checked') is not True
            or not isinstance(result.get('source_fps'),(int,float))
            or not math.isfinite(result['source_fps']) or result['source_fps']<=0
            or sha256(target/'sample.mkv')!=result.get('sample_sha256')
            or result.get('validation',{}).get('sha256')!=result.get('sample_sha256')):
        raise ValueError('Native calibration result is not bound to this master/backend/media')
    validate_video(target/'sample.mkv',result['output_frames'],target)
    acceptance=dict(master_sha256=context['recipe']['master_sha256'],
        backend_fingerprint=plan['backend_fingerprint'],technical_checked=True,
        performance_acceptable=True,measured_source_fps=result['source_fps'],
        sample_report=str(target/'result.json'),sample_report_sha256=sha256(target/'result.json'),
        gfpgan_exercised=result.get('gfpgan_exercised',False),
        scope='Actual current-master bounded model/filter/RIFE/ASS/encode plus full sample decode; not full-episode acceptance')
    if ready.exists() and json.loads(ready.read_text())==acceptance:return
    # Keep the previous readiness report as history, never relabel its master.
    if ready.exists():
        archive=post/'backend/readiness-history';archive.mkdir(exist_ok=True)
        digest=sha256(ready);saved=archive/(digest+'.json')
        if not saved.exists():saved.write_bytes(ready.read_bytes())
    atomic_json(ready,acceptance)
