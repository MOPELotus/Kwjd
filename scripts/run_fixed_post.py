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


def restart_master_gate(root,episode,master,digest):
    """A retired generation cannot become eligible by reusing its old receipt."""
    from jigong_master_restart import enforce
    try:enforce(episode,digest)
    except ValueError as error:raise WaitInput(str(error)) from error
    policy=Path(root)/'.kwjd/jigong-native25-restart.json'
    if not episode.startswith('S03') or not policy.exists():return
    data=load(policy)
    if data.get('active') is not True:return
    denied=data.get('retired_master_sha256')
    if (data.get('schema_version')!=1 or data.get('required_input_fps')!=25
            or not isinstance(denied,dict) or not denied
            or any(not re.fullmatch('[0-9a-f]{64}',v) for v in denied.values())):
        raise WaitInput('Native25 restart policy requires an explicit retired-master SHA inventory')
    if digest in denied.values():
        raise WaitInput('Retired30 master SHA is rejected; fresh native25 DaVinci export required')
    from fractions import Fraction
    streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=r_frame_rate,avg_frame_rate','-of','json',str(master)]))['streams']
    if (len(streams)!=1 or Fraction(streams[0]['r_frame_rate'])!=25
            or Fraction(streams[0]['avg_frame_rate'])!=25):
        raise WaitInput('Native25 restart rejects a non25 replacement master')
    try:
        enforce(episode,digest,streams[0]['r_frame_rate'])
        enforce(episode,digest,streams[0]['avg_frame_rate'])
    except ValueError as error:raise WaitInput(str(error)) from error
    # Container rate alone does not prove native timeline/Mask provenance. Real
    # receiving, clock, style, cut review and new GPU acceptance remain required.


def retired_job(root,episode,job):
    policy=Path(root)/'.kwjd/jigong-native25-restart.json'
    if not policy.is_file():return False
    data=load(policy)
    return (data.get('active') is True and job.get('state')=='SUPERSEDED_WAIT_NATIVE25_MASTER'
        and episode in data.get('retired_master_sha256',{})
        and job.get('master_sha256')==data['retired_master_sha256'][episode])


def archive_retired_pending_report(root,episode):
    """An old publication checkpoint must not trigger new-master recovery."""
    from enhance_stage import sha256
    root=Path(root);pending=root/'post/reports'/(episode+'.pending.json')
    if not pending.exists():return False
    data=load(pending)
    if not retired_job(root,episode,dict(state='SUPERSEDED_WAIT_NATIVE25_MASTER',
            master_sha256=data.get('master_sha256'))):return False
    if pending.is_symlink() or data.get('episode')!=episode:
        raise WaitInput('Invalid retired publication checkpoint; retain for inspection')
    final=root/'post/final'/(episode+'.final-1080p60.mkv')
    partial=final.with_name(final.stem+'.partial'+final.suffix)
    if final.exists() or partial.exists():
        raise WaitInput('Retired publication still has media; explicit retirement required')
    digest=sha256(pending)
    archive=root/'.kwjd/retired/jigong-cfr30-20261125/reports'
    archive.mkdir(parents=True,exist_ok=True)
    saved=archive/(pending.name+'.'+digest)
    if not saved.exists():
        with saved.open('xb') as output:output.write(pending.read_bytes())
    if saved.is_symlink() or sha256(saved)!=digest or sha256(pending)!=digest:
        raise ValueError('Retired publication archive differs; retain checkpoint')
    pending.unlink()
    return True


def archive_retired_artifact_registry(root,episode):
    """Preserve the old cleanup inventory before registering a replacement."""
    from enhance_stage import sha256
    root=Path(root);registry=root/'.kwjd/artifacts'/(episode+'.linux.json')
    if not registry.exists():return False
    data=load(registry)
    masters=[row.get('fingerprint',{}).get('sha256') for row in data.get('artifacts',[])
             if row.get('relative')==f'incoming/{episode}.clean-master.mov']
    if len(masters)!=1 or not retired_job(root,episode,dict(
            state='SUPERSEDED_WAIT_NATIVE25_MASTER',master_sha256=masters[0])):return False
    if registry.is_symlink() or data.get('episode')!=episode or data.get('role')!='linux':
        raise WaitInput('Invalid retired artifact registry; retain for inspection')
    digest=sha256(registry);archive=root/'.kwjd/retired/jigong-cfr30-20261125/artifacts'
    archive.mkdir(parents=True,exist_ok=True);saved=archive/(registry.name+'.'+digest)
    if not saved.exists():
        with saved.open('xb') as output:output.write(registry.read_bytes())
    if saved.is_symlink() or sha256(saved)!=digest or sha256(registry)!=digest:
        raise ValueError('Retired artifact archive differs; retain registry')
    registry.unlink()
    return True


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
    restart_master_gate(root,ep,master,digest)
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
    from jigong_quality import valid_reviewer
    post=root/'post';receipt=post/'receipts'/(ep+'.subtitle-timing.json')
    if not receipt.exists():
        evidence=post/'reports'/(ep+'.program-clock-reviewed.json');data=load(evidence)
        clock=Path(data['video_clock_evidence_path'])
        if (data.get('reviewed_video_mapping') is not True or data.get('master_sha256')!=master_sha
                or not valid_reviewer(data.get('reviewed_by',{}),ep)
                or data.get('source_sha256')!=sha256(root/'input'/(ep+'.mp4'))
                or not clock.resolve().is_relative_to((post/'reports').resolve())
                or clock.is_symlink() or sha256(clock)!=data.get('video_clock_evidence_sha256')):
            raise WaitInput('Actual hash-bound reviewed program clock required')
        observation=load(clock);offset=data['proposed_derived_ass_offset_ms']
        if (type(offset) is not int or not -1000<=offset<=1000 or observation.get('verified') is not True
                or abs(observation['offset_seconds']*1000-offset)>1e-6):
            raise ValueError('Program clock evidence and reviewed offset differ')
        receipt.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(receipt,dict(schema_version=1,reviewed=True,episode=ep,master_sha256=master_sha,
            srt_sha256=srt_sha,offset_ms=offset,evidence_path=str(evidence),evidence_sha256=sha256(evidence)))
    return read_mapping(post,ep,master_sha,srt_sha)


def serial_gate(root,episode):
    """A new chapter needs the previous real final QC, not its download."""
    if not episode.startswith('S03'):return
    for path in (root/'post/jobs').glob('S03E*.json'):
        prior=load(path)
        if path.stem!=episode and prior.get('state')!='READY' and not retired_job(root,path.stem,prior):
            raise WaitInput('Another cloud episode still owns the serial slot: '+path.stem)
    number=int(episode[4:])
    if number>2:
        previous=f'S03E{number-1:02d}'
        path=root/'post/jobs'/(previous+'.json')
        if not path.exists() or load(path).get('state')!='READY':
            raise WaitInput('Previous chapter media must finish first')
        from jigong_quality import verify_quality
        quality=verify_quality(root,episode=previous)
        manifest=load(root/'post/handoffs'/(previous+'.cloud-final.json'))
        if (manifest.get('server_quality_checked') is not True
                or manifest.get('sha256')!=quality['final_sha256']
                or manifest.get('quality_evidence_sha256')!=quality['quality_sha256']):
            raise WaitInput('Previous chapter actual final QC must be published first')


def retire_idle_worker(root,episode,unit):
    """Reuse the same S03 unit; never leave an older scoped loop alongside it."""
    if not episode.startswith('S03'):return
    active=subprocess.run(['systemctl','--user','is-active','--quiet',unit+'.service']).returncode==0
    if not active:return
    pid=int(subprocess.check_output(['systemctl','--user','show',unit+'.service','--property=MainPID','--value'],text=True))
    args=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    if b'--episode' not in args:raise WaitInput('Unexpected Worker scope; retain process')
    owner=args[args.index(b'--episode')+1].decode()
    if owner==episode:return
    if load(root/'post/jobs'/(owner+'.json')).get('state')!='READY':
        raise WaitInput('Previous Worker still owns media work')
    with (root/'post/jobs/.worker.lock').open('a') as lease:
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise WaitInput('Worker is busy; retain process and retry')
        subprocess.run(['systemctl','--user','stop',unit+'.service'],check=True)


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
    serial_gate(root,ep)
    if check_only:return dict(state='INPUT_IDENTITIES_AND_CLOCK_VERIFIED',episode=ep,production_started=False,timing=timing)
    clean_report=root/'post/reports/fixed-clean-recovery.json'
    if clean_report.exists() and load(clean_report).get('state','').startswith('RUNNING_'):
        raise WaitInput('First clean-deployment self-test still running; wait before sharing its GPUs')
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
    from jigong_master_restart import enforce
    enforce(ep,digest,str(template.get('input_frame_rate',25))+'/1')
    from fixed_workflow import validate_fixed_profile
    validate_fixed_profile(template)  # Full package lock was verified by bootstrap in its own venv.
    from enhance_stage import atomic_json,sha256
    style=freeze_style(root,ep,subtitle)
    archive_retired_pending_report(root,ep)
    archive_retired_artifact_registry(root,ep)
    unit='kwjd-fixed-s03e02' if ep.startswith('S03') else 'kwjd-fixed-'+ep.lower()
    retire_idle_worker(root,ep,unit)
    job_path=root/'post/jobs'/(ep+'.json')
    identity=dict(schema_version=1,episode=ep,master=f'incoming/{ep}.clean-master.mov',
        style=style.relative_to(root/'post').as_posix(),output=f'final/{ep}.final-1080p60.mkv',master_sha256=digest,
        submitted_ass_sha256=sha256(ass),picture_pipeline='native_light_gfpgan_rife_ass_one_encode')
    if job_path.exists():
        prior=load(job_path)
        if retired_job(root,ep,prior):
            # New master/ASS/clock/style and actual backend acceptance have all
            # passed above; preserve the retired generation before fresh queueing.
            archive=root/'.kwjd/retired/jigong-cfr30-20261125/jobs'
            archive.mkdir(parents=True,exist_ok=True)
            saved=archive/(ep+'.'+prior['master_sha256']+'.json')
            if saved.exists() and load(saved)!=prior:raise ValueError('Retired job archive changed; retained')
            if not saved.exists():atomic_json(saved,prior)
            job_path.unlink()
        elif any(prior.get(key)!=value for key,value in identity.items()):raise ValueError('Existing job input binding differs; retained')
        if prior.get('state') in ('READY','FAILED'):return dict(state=prior['state'],episode=ep,job_retained=True)
    if not job_path.exists():
        job_path.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(job_path,{**identity,'state':'QUEUED','created_at':time.time()})
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
