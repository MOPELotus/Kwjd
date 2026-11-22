"""Submit the fixed workflow using only a verified master and reviewed ASS.

Identity/clock/quality evidence remains mandatory. No model/device/precision or
encoding options are exposed; missing assets recover the same locked stack.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


class WaitInput(RuntimeError):pass


def load(path):return json.loads(Path(path).read_text())


def select_project(repo,episode):
    candidates=[repo/'config/project.json',*(repo/'config/projects').glob('*.json')]
    matches=[p for p in candidates if load(p).get('season')==episode[:3]]
    if len(matches)!=1:raise ValueError('Exactly one explicit season production profile is required')
    project=load(matches[0])
    if episode not in project.get('managed_post_episodes',[episode] if episode[:3]=='S01' else []):
        raise ValueError('Episode has not been admitted to this managed production scope')
    return matches[0]


def inputs(root,master,ass):
    from enhance_stage import sha256
    from subtitle_review_supervisor import validate_episode
    from subtitle_review_ass import validate_ass
    match=re.fullmatch(r'(S\d{2}E\d{2,3})\.clean-master\.mov',master.name)
    if not match:raise ValueError('Use the canonical received clean-master name')
    ep=match[1];canonical=root/'post/incoming'/master.name
    if master.resolve()!=canonical.resolve() or master.is_symlink():raise ValueError('Use the verified received master')
    receipt=load(root/'post/handoffs'/(ep+'.master-received.json'))
    digest=sha256(master)
    if (receipt.get('transfer_verified') is not True or receipt.get('episode')!=ep
            or receipt.get('sha256')!=digest or receipt.get('bytes')!=master.stat().st_size):
        raise ValueError('Master lacks its matching real handoff receipt')
    if not validate_episode(root,ep) or not validate_ass(root,ep):
        raise WaitInput('Waiting for genuinely reviewed/sealed first-episode SRT and bound ASS')
    canonical_ass=root/'output/ass'/(ep+'.ass')
    if not ass.is_file() or ass.is_symlink() or sha256(ass)!=sha256(canonical_ass):
        raise ValueError('Supplied ASS differs from reviewed published ASS')
    data=load(canonical_ass.with_suffix('.ass.ready.json'))
    per_episode=root/'post/styles'/(ep+'.ass')
    style=per_episode if per_episode.is_file() else root/'post/styles/template.ass'
    approval=load(style.with_name(style.name+'.ready.json'))
    if (approval.get('master_sha256')!=digest or approval.get('approved') is not True
            or approval.get('sha256')!=sha256(style) or approval.get('sha256')!=data['template_sha256']
            or approval.get('fonts')!=data.get('fonts')):
        raise WaitInput('Actual current-master style approval/preview required')
    data['_selected_style']=str(style)
    return ep,digest,data


def freeze_style(root,ep,subtitle):
    from enhance_stage import atomic_json,sha256
    source=Path(subtitle['_selected_style']);target=root/'post/styles'/(ep+'.ass')
    approval=load(source.with_name(source.name+'.ready.json'))
    if target.exists() and target.read_bytes()!=source.read_bytes():
        raise ValueError('Existing per-episode style differs; retained')
    if not target.exists():
        with target.open('xb') as output:output.write(source.read_bytes())
    marker=target.with_name(target.name+'.ready.json')
    if marker.exists():
        if load(marker)!=approval:raise ValueError('Existing per-episode approval differs; retained')
    else:atomic_json(marker,approval)
    if sha256(target)!=approval['sha256']:raise ValueError('Frozen style identity differs')
    return target


def ensure_timing(root,ep,master_sha,srt_sha):
    from enhance_stage import sha256,atomic_json
    from subtitle_timing import read_mapping
    post=root/'post';receipt=post/'receipts'/(ep+'.subtitle-timing.json')
    if not receipt.exists():
        evidence=post/'reports'/(ep+'.program-clock-reviewed.json');data=load(evidence)
        clock=Path(data['video_clock_evidence_path'])
        if (data.get('reviewed_video_mapping') is not True or data.get('master_sha256')!=master_sha
                or data.get('reviewed_by',{}).get('kind')!='codex'
                or data.get('source_sha256')!=sha256(root/'input'/(ep+'.mp4'))
                or not clock.resolve().is_relative_to((post/'reports').resolve())
                or clock.is_symlink() or sha256(clock)!=data.get('video_clock_evidence_sha256')):
            raise WaitInput('Actual hash-bound reviewed program clock required')
        observation=load(clock);offset=data['proposed_derived_ass_offset_ms']
        if (type(offset) is not int or not -1000<=offset<=1000 or observation.get('verified') is not True
                or abs(observation['offset_seconds']*1000-offset)>1e-6):
            raise ValueError('Program clock evidence and reviewed offset differ')
        atomic_json(receipt,dict(schema_version=1,reviewed=True,episode=ep,master_sha256=master_sha,
            srt_sha256=srt_sha,offset_ms=offset,evidence_path=str(evidence),evidence_sha256=sha256(evidence)))
    return read_mapping(post,ep,master_sha,srt_sha)


def run(root,master,ass,check_only=False):
    with (root/'post/.fixed-submit.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return submit(root,master,ass,check_only)


def submit(root,master,ass,check_only):
    repo=Path(__file__).resolve().parents[1]
    match=re.fullmatch(r'(S\d{2}E\d{2,3})\.clean-master\.mov',master.name)
    if not match:raise ValueError('Canonical received master name required')
    project=select_project(repo,match[1]);os.environ['KWJD_PROJECT']=str(project)
    ep,digest,subtitle=inputs(root,master,ass)
    timing=ensure_timing(root,ep,digest,subtitle['srt_sha256'])
    if check_only:return dict(state='INPUT_IDENTITIES_AND_CLOCK_VERIFIED',episode=ep,production_started=False,timing=timing)
    if root.resolve()!=(Path.home()/'qingqingwu').resolve():
        raise ValueError('The existing Worker requires its configured ~/qingqingwu data root')
    # Recovery is idempotent: exact packages/weights and the same recipe, never
    # a backend or tuning decision. Worker still requires actual admission.
    subprocess.run([sys.executable,str(repo/'scripts/bootstrap_face_runtime.py'),'--post',str(root/'post')],check=True)
    python=root/'post/face-venv/bin/python'
    for name,arguments in [('bootstrap_rife_onnx.py',['--post',str(root/'post')]),
                           ('bootstrap_fixed_trt.py',['--post',str(root/'post'),'--master',str(master)])]:
        subprocess.run([str(python),str(repo/'scripts'/name),*arguments],check=True)
    from native_face_job import enabled
    if not enabled(root/'post'):raise WaitInput('Fixed stack recovered; actual current-code fullchain acceptance still required')
    template=load(root/'post/backend/runtime.native-template.json')
    from fixed_workflow import validate_fixed_profile
    validate_fixed_profile(template)  # Full package lock was verified by bootstrap in its own venv.
    from enhance_stage import atomic_json,sha256
    style=freeze_style(root,ep,subtitle)
    job_path=root/'post/jobs'/(ep+'.json')
    identity=dict(schema_version=1,episode=ep,master=f'incoming/{ep}.clean-master.mov',
        style=style.relative_to(root/'post').as_posix(),output=f'final/{ep}.final-1080p60.mkv',master_sha256=digest,
        submitted_ass_sha256=sha256(ass),picture_pipeline='native_light_gfpgan_rife_ass_one_encode')
    if job_path.exists():
        prior=load(job_path)
        if any(prior.get(key)!=value for key,value in identity.items()):raise ValueError('Existing job input binding differs; retained')
        if prior.get('state') in ('READY','FAILED'):return dict(state=prior['state'],episode=ep,job_retained=True)
    else:atomic_json(job_path,{**identity,'state':'QUEUED','created_at':time.time()})
    unit='kwjd-fixed-'+ep.lower()
    active=subprocess.run(['systemctl','--user','is-active','--quiet',unit+'.service']).returncode==0
    if not active:
        subprocess.run(['systemd-run','--user','--collect','--unit',unit,
            '--property=WorkingDirectory='+str(repo),'--setenv=KWJD_PROJECT='+str(project),
            str(python),str(repo/'scripts/server_worker.py'),'--loop','--episode',ep,'--stage','all'],check=True)
    return dict(state='WORKER_UNIT_REUSED' if active else 'WORKER_UNIT_SUBMITTED',episode=ep,
                unit=unit+'.service',job=str(job_path),production_finished=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.home()/'qingqingwu')
    parser.add_argument('--master',type=Path,required=True);parser.add_argument('--ass',type=Path,required=True)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    try:print(json.dumps(run(args.root.resolve(),args.master.absolute(),args.ass.absolute(),args.check_only)))
    except (WaitInput,FileNotFoundError) as error:
        print(json.dumps(dict(state='WAIT_VERIFIED_INPUT_OR_ACCEPTANCE',reason=str(error))),flush=True)
        raise SystemExit(75)
