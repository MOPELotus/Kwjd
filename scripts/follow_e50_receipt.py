#!/usr/bin/env python3
"""Persistent E50 receipt -> technical preparation -> native stage-all owner.

Never reads an uploading file, fabricates upload/visual receipts, touches Windows
or changes reserve/quality. WAIT_DISK is retried against real filesystem capacity.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime

from enhance_stage import atomic_json, sha256, WaitDisk, WaitGPU

ROOT = Path.home() / 'qingqingwu'
POST = ROOT / 'post'
HERE = Path(__file__).resolve().parent
EP = 'S01E50'
EXPECTED = '7494ca20c6dca29b8c03cd927a293278a3ce01002beef031d5044ef61ca8f725'
BYTES = 7840642589
FOLDER = POST / 'reports/S01E50-takeover'
UNIT = 'kwjd-native-production-e50.service'
SOURCE_SHA256 = sha256(Path(__file__))


def require(value, message):
    if not value:
        raise ValueError(message)


def receipt_ready(master, receipt):
    return (master.is_file() and not master.with_name(master.name + '.uploading').exists()
            and receipt.get('episode') == EP and receipt.get('sha256') == EXPECTED
            and receipt.get('verified') is True and receipt.get('local_master_deleted') is True)


def set_state(stage, **details):
    previous = json.loads((FOLDER/'status.json').read_text()) if (FOLDER/'status.json').exists() else {}
    record = dict(at=datetime.now().astimezone().isoformat(), episode=EP, stage=stage,
                  owner_pid=os.getpid(), owner_source_sha256=SOURCE_SHA256, **details)
    atomic_json(FOLDER/'status.json', record)
    if previous.get('stage') != stage or time.time() - (FOLDER/'progress.log').stat().st_mtime > 300:
        with (FOLDER/'progress.log').open('a') as stream:
            stream.write(record['at']+' '+stage+' '+json.dumps(details,ensure_ascii=False)+'\n')


def run_logged(arguments):
    with (FOLDER/'commands.log').open('a') as stream:
        stream.write(repr(arguments)+'\n');stream.flush()
        subprocess.run(arguments, stdout=stream, stderr=stream, check=True)


def clock_evidence(master, source):
    import numpy as np
    from scipy.signal import correlate, correlation_lags
    def audio(path, start):
        raw = subprocess.check_output(['ffmpeg','-v','error','-nostdin','-ss',str(start),
            '-i',str(path),'-t','12','-map','0:a:0','-vn','-ac','1','-ar','8000',
            '-af','highpass=f=200,lowpass=f=3000','-f','f32le','-'])
        values=np.frombuffer(raw,dtype='<f4').astype(np.float64);return values-values.mean()
    rows=[]
    for start in (60,900,1800,3300):
        left,right=audio(master,start),audio(source,start)
        require(len(left)==len(right)==96000,'Audio clock window incomplete')
        norm=float(np.linalg.norm(left)*np.linalg.norm(right));require(norm>0,'Silent clock evidence')
        corr=correlate(left,right,method='fft')/norm
        lags=correlation_lags(len(left),len(right));mask=np.abs(lags)<=4000
        index=int(np.argmax(corr[mask]));lag=int(lags[mask][index]);score=float(corr[mask][index])
        require(abs(lag)<=16 and score>=.80,'Audio program clocks need independent review; no guessed offset')
        rows.append(dict(start_seconds=start,lag_seconds=lag/8000,normalized_correlation=score))
    def first_video(path):
        data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))
        return next(s for s in data['streams'] if s['codec_type']=='video')
    old,new=first_video(source),first_video(master)
    require(old['r_frame_rate']==new['r_frame_rate']=='25/1','Unexpected source frame clock')
    offset=round((float(new.get('start_time',0))-float(old.get('start_time',0)))*1000)
    require(-1000<=offset<=1000,'Unexpected container clock shift')
    return dict(master_sha256=EXPECTED,source_sha256=sha256(source),windows=rows,
                offset_ms=offset,source_video_start=old.get('start_time'),master_video_start=new.get('start_time'),
                basis='Automatic same-program audio/PTS audit under standing processing authorization; not manual lip-sync or whole-film visual approval')


def prepare_inputs(master):
    srt=ROOT/'output/S01E50.zh.srt';seal=json.loads(srt.with_name(srt.name+'.ready.json').read_text())
    require(seal.get('reviewed') is True and seal['sha256']==sha256(srt),'Reviewed SRT seal differs')
    template=POST/'styles/template.ass';base=json.loads(template.with_name(template.name+'.ready.json').read_text())
    require(base.get('approved') is True and base['sha256']==sha256(template),'Standing approved style changed')
    for name,digest in base['fonts'].items():
        require(Path(name).name==name and sha256(POST/'styles/fonts'/name)==digest,'Packaged font changed')
    style=POST/'styles/S01E50.ass'
    if style.exists():require(style.read_bytes()==template.read_bytes(),'Per-master template differs')
    else:
        with style.open('xb') as stream:stream.write(template.read_bytes())
    approval=dict(base,master_sha256=EXPECTED,approved_by='codex_under_standing_user_authorization',
                  basis='Unchanged approved MiSans84 defaults; user authorized subsequent episodes to run directly after technical validation',
                  actual_new_master_human_visual_review_claimed=False)
    approval_path=style.with_name(style.name+'.ready.json')
    if approval_path.exists():require(json.loads(approval_path.read_text())==approval,'Per-master style approval differs')
    else:atomic_json(approval_path,approval)
    evidence_path=FOLDER/'program-clock.json'
    if not evidence_path.exists():atomic_json(evidence_path,clock_evidence(master,ROOT/'input/S01E50.mp4'))
    evidence=json.loads(evidence_path.read_text());require(evidence['master_sha256']==EXPECTED,'Clock evidence differs')
    timing=dict(schema_version=1,episode=EP,reviewed=True,reviewed_by='automatic_program_clock_audit',
        master_sha256=EXPECTED,srt_sha256=sha256(srt),offset_ms=evidence['offset_ms'],
        evidence_path='reports/S01E50-takeover/program-clock.json',evidence_sha256=sha256(evidence_path))
    timing_path=POST/'receipts/S01E50.subtitle-timing.json'
    if timing_path.exists():require(json.loads(timing_path.read_text())==timing,'Existing reviewed timing differs; preserve it')
    else:atomic_json(timing_path,timing)
    return srt,style,base['fonts']


def cache_release_ready(data):
    return (data.get('episode')=='S01E51'
            and data.get('master_sha256')=='2eb923278d3dd7526ed7466296ad437fd19411acaf303508ee8013d1239822f1'
            and data.get('state')=='LOCAL_ANCHORED_CACHE_CLEANED'
            and data.get('master_retained') is True)


def watch_cache_notice():
    path=POST/'receipts/S01E51.cache-release.json';once=FOLDER/'e51-cache-notice.json'
    if not path.exists() or once.exists():return
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,json.JSONDecodeError):return  # A small in-flight SCP is not a failure.
    if not cache_release_ready(data):return
    notice=dict(episode='S01E51',master_sha256=data['master_sha256'],receipt_path=str(path),
        receipt_sha256=sha256(path),message='第三回缓存已清，可开第四回',
        state='PENDING_WINDOWS_CHAT_NOTIFICATION',delivered=False,
        note='Local durable queue and SSH progress notice only; this Linux environment exposes no cross-thread send tool.')
    directory=POST/'reports/notifications';directory.mkdir(exist_ok=True)
    atomic_json(directory/'S01E51.cache-released.json',notice);atomic_json(once,notice)
    with (FOLDER/'progress.log').open('a') as stream:
        stream.write(datetime.now().astimezone().isoformat()+' NOTICE 第三回缓存已清，可开第四回 receipt_sha256='+notice['receipt_sha256']+'\n')


def tick():
    watch_cache_notice()
    existing_job=POST/'jobs/S01E50.json'
    if existing_job.exists():
        previous=json.loads(existing_job.read_text())
        require(previous.get('master_sha256')==EXPECTED,'Existing queue identity differs')
        if previous.get('state')=='FAILED':
            raise ValueError('Production worker reported failure: '+previous.get('detail',''))
        if previous.get('state')=='READY':
            report=POST/'reports/S01E50.json';data=json.loads(report.read_text())
            require(data.get('master_sha256')==EXPECTED and data.get('final_decode_checked') is True
                    and data.get('final_pcm_equivalent') is True,'Final report incomplete')
            notice=FOLDER/'final-confirmed.json'
            if not notice.exists():
                require(sha256(POST/'final/S01E50.final-1080p60.mkv')==data['sha256'],'Final bytes changed')
                atomic_json(notice,dict(master_sha256=EXPECTED,sha256=data['sha256'],report_sha256=sha256(report)))
            set_state('FINAL_READY_PENDING_WINDOWS',report=str(report));return
        if subprocess.run(['systemctl','--user','is-active','--quiet',UNIT]).returncode==0:
            set_state('PRODUCTION_WORKER_ACTIVE',job_state=previous['state']);return
    master=POST/'incoming/S01E50.clean-master.mov';receipt_path=POST/'receipts/S01E50.upload.json'
    upload=json.loads(receipt_path.read_text(encoding='utf-8-sig')) if receipt_path.exists() else {}
    if not receipt_ready(master,upload):
        set_state('WAIT_ATOMIC_MASTER_AND_REAL_UPLOAD_RECEIPT');return
    from native_face_job import enabled,prepare,calibrate
    require(enabled(POST),'Native activation invalid')
    marker=FOLDER/'master-validated.json'
    if not marker.exists():
        set_state('FRESH_MASTER_SHA_AND_TECHNICAL_VALIDATION')
        require(master.stat().st_size==BYTES and sha256(master)==EXPECTED,'Published E50 master differs')
        # The human accepted only this SHA-bound extra tail-background exception.
        approval=FOLDER/'duration-approval.json'
        atomic_json(approval,dict(schema_version=1,approved=True,approved_by='human',
            scope='extra_tail_background_only',episode=EP,master_sha256=EXPECTED,
            expected_duration_seconds=3649.933,actual_duration_seconds=3650.440,
            human_quote='对 尾部是一点点背景 裁背景素材的时候有一点误差 属合理情况',
            reason='Explicit previously recorded human approval for this exact E50 tail'))
        run_logged([sys.executable,str(HERE/'validate_master.py'),'--file',str(master),'--receipt',str(marker),
                    '--expected-duration','3649.933','--duration-approval',str(approval)])
    validated=json.loads(marker.read_text());require(validated['sha256']==EXPECTED,'Validation belongs to another master')
    stat=master.stat();current=[stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]
    identity=FOLDER/'verified-master-identity.json'
    if identity.exists():require(json.loads(identity.read_text())['stat']==current,'Validated master changed')
    else:atomic_json(identity,{'sha256':EXPECTED,'stat':current})
    srt,style,fonts=prepare_inputs(master)
    calibration=FOLDER/'native-calibration-ready.json'
    if not calibration.exists():
        set_state('BOUNDED_CURRENT_MASTER_NATIVE_CALIBRATION')
        with (POST/'jobs/.worker.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            context=prepare(POST,EP,EXPECTED,srt,style,fonts,POST/'logs/S01E50.log')
            calibrate(master,POST,EP,context,POST/'logs/S01E50.log')
            atomic_json(calibration,{'master_sha256':EXPECTED,'recipe_sha256':context['recipe_sha256']})
    job_path=POST/'jobs/S01E50.json'
    if not job_path.exists():
        atomic_json(job_path,dict(schema_version=1,episode=EP,state='WAIT_DISK',master='incoming/'+master.name,
            master_sha256=EXPECTED,srt='../output/S01E50.zh.srt',style='styles/S01E50.ass',
            output='final/S01E50.final-1080p60.mkv',created_at=datetime.now().astimezone().isoformat(),
            picture_pipeline='native_light_gfpgan_rife_ass_one_encode',detail='Persistent full-phase budget admission'))
    job=json.loads(job_path.read_text());require(job.get('master_sha256')==EXPECTED,'Existing queue identity differs')
    if job['state']=='READY':
        set_state('FINAL_READY_PENDING_WINDOWS',report=str(POST/'reports/S01E50.json'));return
    active=subprocess.check_output(['systemctl','--user','is-active',UNIT],text=True).strip() if subprocess.run(
        ['systemctl','--user','is-active','--quiet',UNIT]).returncode==0 else 'inactive'
    if active=='active':
        set_state('PRODUCTION_WORKER_ACTIVE',job_state=job['state']);return
    seconds=validated['duration_seconds'];audio=validated['audio']
    video=int(seconds*18_000_000/8);pcm=int(seconds*int(audio['sample_rate'])*audio['channels']*4)
    required=max(2*video+512*1024**2,2*video+2*pcm+1024**3)
    free=shutil.disk_usage(master).free
    budget=dict(master_sha256=EXPECTED,free_bytes=free,additional_peak_bytes=required,reserve_gib=25,
                shortfall_bytes=max(0,required+25*1024**3-free),cache=str((POST/'cache').resolve()),
                note='All input bytes already occupy this filesystem; no incoming double-counting. Native encoding and final mux phases use max, not sum.')
    atomic_json(FOLDER/'production-budget.json',budget)
    if budget['shortfall_bytes']:
        set_state('WAIT_FULL_PRODUCTION_DISK',**budget);return
    subprocess.run(['systemctl','--user','start',UNIT],check=True)
    set_state('PRODUCTION_WORKER_STARTED',master_sha256=EXPECTED)


def main():
    FOLDER.mkdir(parents=True,exist_ok=True);(FOLDER/'progress.log').touch(exist_ok=True)
    with (FOLDER/'owner.lock').open('a+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:tick()
            except (WaitDisk,WaitGPU,BlockingIOError) as error:set_state('RESOURCE_WAIT',reason=str(error))
            except Exception as error:
                set_state('NEEDS_ATTENTION',reason=repr(error))
                atomic_json(FOLDER/'ALERT.json',dict(error=repr(error),at=datetime.now().astimezone().isoformat()))
            time.sleep(30)


if __name__=='__main__':main()
