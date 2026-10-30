#!/usr/bin/env python3
"""Persistent E50 receipt -> technical preparation -> native stage-all owner.

Never reads an uploading file, fabricates upload/visual receipts, touches Windows
or changes reserve/quality. WAIT_DISK is retried against real filesystem capacity.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys
import time
from datetime import datetime

from enhance_stage import atomic_json, sha256, WaitDisk, WaitGPU
from storage_policy import minimum_free_gib
from queue_storage import phase_peaks,evaluate

ROOT = Path.home() / 'qingqingwu'
POST = ROOT / 'post'
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
EP = 'S01E50'
EXPECTED = '7494ca20c6dca29b8c03cd927a293278a3ce01002beef031d5044ef61ca8f725'
BYTES = 7840642589
FOLDER = POST / 'reports/S01E50-takeover'
UNIT = 'kwjd-native-production-e50.service'
SOURCE_SHA256 = sha256(Path(__file__))


def require(value, message):
    if not value:
        raise ValueError(message)


def configure_episode(episode):
    global EP,EXPECTED,BYTES,FOLDER,UNIT
    profiles={'S01E50':('7494ca20c6dca29b8c03cd927a293278a3ce01002beef031d5044ef61ca8f725',7840642589),
              'S01E51':('2eb923278d3dd7526ed7466296ad437fd19411acaf303508ee8013d1239822f1',7803409376)}
    if episode in ('S01E52','S01E53','S01E54','S01E55'):
        manifest=load_remaining_manifest()
        profile=manifest['masters'][episode]
        digest=profile.get('sha256')
        if not digest:
            receipt=json.loads((POST/'receipts'/f'{episode}.upload.json').read_text(encoding='utf-8-sig'))
            require(receipt.get('episode')==episode and receipt.get('verified') is True
                    and receipt.get('local_master_deleted') is True,'Actual Windows upload handoff required')
            digest=receipt['sha256']
        require(bool(re.fullmatch('[0-9a-f]{64}',digest)) and type(profile['bytes']) is int
                and profile['bytes']>0,'Invalid finite master profile')
        profiles[episode]=(digest,profile['bytes'])
    EP=episode;EXPECTED,BYTES=profiles[episode]
    FOLDER=POST/'reports'/f'{episode}-takeover';UNIT=f'kwjd-native-production-e{int(episode.split("E")[1])}.service'


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
    def audio(path):
        # Input seeking can round AAC packet starts differently at each window.
        # Decode the complete mono analysis track once, align to PTS0, then slice.
        raw = subprocess.check_output(['ffmpeg','-v','error','-nostdin','-i',str(path),
            '-map','0:a:0','-vn','-ac','1','-ar','8000',
            '-af','aresample=8000:async=1:first_pts=0,highpass=f=200,lowpass=f=3000','-f','f32le','-'])
        return np.frombuffer(raw,dtype='<f4')
    full_left,full_right=audio(master),audio(source)
    rows=[]
    for start in (60,900,1800,3300):
        left=full_left[start*8000:(start+12)*8000].astype(np.float64)
        right=full_right[start*8000:(start+12)*8000].astype(np.float64)
        left-=left.mean();right-=right.mean()
        require(len(left)==len(right)==96000,'Audio clock window incomplete')
        norm=float(np.linalg.norm(left)*np.linalg.norm(right));require(norm>0,'Silent clock evidence')
        corr=correlate(left,right,method='fft')/norm
        lags=correlation_lags(len(left),len(right));mask=np.abs(lags)<=4000
        index=int(np.argmax(corr[mask]));lag=int(lags[mask][index]);score=float(corr[mask][index])
        require(abs(lag)<=960 and score>=.80,'Audio program identity differs by more than120ms; independent review required')
        rows.append(dict(start_seconds=start,lag_seconds=lag/8000,normalized_correlation=score))
    def first_video(path):
        data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))
        return next(s for s in data['streams'] if s['codec_type']=='video')
    old,new=first_video(source),first_video(master)
    require(old['r_frame_rate']==new['r_frame_rate']=='25/1','Unexpected source frame clock')
    offset=round((float(new.get('start_time',0))-float(old.get('start_time',0)))*1000)
    require(-1000<=offset<=1000,'Unexpected container clock shift')
    visual=FOLDER/'video-clock-matches.json'
    if not visual.exists():
        run_logged([str(POST/'face-venv/bin/python'),str(HERE/'verify_video_clock.py'),
                    '--source',str(source),'--master',str(master),'--offset',str(offset/1000),'--output',str(visual)])
    video=json.loads(visual.read_text())
    require(video.get('verified') is True and abs(video['offset_seconds']-offset/1000)<1e-6,
            'Native frame mapping needs independent review; no guessed subtitle shift')
    return dict(master_sha256=EXPECTED,source_sha256=sha256(source),windows=rows,
                video_evidence_path=str(visual),video_evidence_sha256=sha256(visual),
                audio_lags_used_to_shift_subtitles=False,
                offset_ms=offset,source_video_start=old.get('start_time'),master_video_start=new.get('start_time'),
                basis='Native frame mapping verified across program; subtitle shift follows video origin, not AAC/mixed-PCM lag. Audio checks program identity only. No whole-film lip-sync claim.')


def prepare_inputs(master):
    srt=ROOT/'output'/f'{EP}.zh.srt';seal=json.loads(srt.with_name(srt.name+'.ready.json').read_text())
    require(seal.get('reviewed') is True and seal['sha256']==sha256(srt),'Reviewed SRT seal differs')
    template=POST/'styles/template.ass';base=json.loads(template.with_name(template.name+'.ready.json').read_text())
    require(base.get('approved') is True and base['sha256']==sha256(template),'Standing approved style changed')
    for name,digest in base['fonts'].items():
        require(Path(name).name==name and sha256(POST/'styles/fonts'/name)==digest,'Packaged font changed')
    style=POST/'styles'/f'{EP}.ass'
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
    if not evidence_path.exists():atomic_json(evidence_path,clock_evidence(master,ROOT/'input'/f'{EP}.mp4'))
    evidence=json.loads(evidence_path.read_text());require(evidence['master_sha256']==EXPECTED,'Clock evidence differs')
    timing=dict(schema_version=1,episode=EP,reviewed=True,reviewed_by='automatic_program_clock_audit',
        master_sha256=EXPECTED,srt_sha256=sha256(srt),offset_ms=evidence['offset_ms'],
        evidence_path=f'reports/{EP}-takeover/program-clock.json',evidence_sha256=sha256(evidence_path))
    timing_path=POST/'receipts'/f'{EP}.subtitle-timing.json'
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
    if EP=='S01E50':watch_cache_notice()
    if (FOLDER/'policy-maintenance.json').exists():
        set_state('STORAGE_POLICY_MAINTENANCE');return
    if EP!='S01E50':
        predecessor=f'S01E{int(EP.split("E")[1])-1:02d}'
        prior=POST/'jobs'/f'{predecessor}.json'
        if not prior.exists() or json.loads(prior.read_text()).get('state')!='READY':
            set_state('WAIT_SERIAL_PREDECESSOR',predecessor=predecessor);return
        review=POST/'reports'/f'{predecessor}.server-acceptance.json'
        cleanup=POST/'reports'/f'{predecessor}.stage-cleanup.json'
        if not review.exists() or not cleanup.exists():
            set_state('WAIT_PREDECESSOR_BOUNDED_REVIEW_AND_STAGE_RELEASE');return
        accepted=json.loads(review.read_text());released=json.loads(cleanup.read_text())
        final_report=json.loads((POST/'reports'/f'{predecessor}.json').read_text())
        require(accepted.get('server_accepted') is True and accepted.get('bounded_visual_review_pass') is True
                and accepted.get('sha256')==final_report['sha256']
                and released.get('complete') is True and released.get('sha256')==final_report['sha256'],
                'Predecessor actual review/stage release must bind its final SHA')
    existing_job=POST/'jobs'/f'{EP}.json'
    if existing_job.exists():
        previous=json.loads(existing_job.read_text())
        require(previous.get('master_sha256')==EXPECTED,'Existing queue identity differs')
        if previous.get('state')=='FAILED':
            raise ValueError('Production worker reported failure: '+previous.get('detail',''))
        if previous.get('state')=='READY':
            report=POST/'reports'/f'{EP}.json';data=json.loads(report.read_text())
            require(data.get('master_sha256')==EXPECTED and data.get('final_decode_checked') is True
                    and data.get('final_pcm_equivalent') is True,'Final report incomplete')
            notice=FOLDER/'final-confirmed.json'
            if not notice.exists():
                require(sha256(POST/'final'/f'{EP}.final-1080p60.mkv')==data['sha256'],'Final bytes changed')
                atomic_json(notice,dict(master_sha256=EXPECTED,sha256=data['sha256'],report_sha256=sha256(report)))
            set_state('FINAL_READY_PENDING_WINDOWS',report=str(report));return
        if subprocess.run(['systemctl','--user','is-active','--quiet',UNIT]).returncode==0:
            set_state('PRODUCTION_WORKER_ACTIVE',job_state=previous['state']);return
    master=POST/'incoming'/f'{EP}.clean-master.mov';receipt_path=POST/'receipts'/f'{EP}.upload.json'
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
        if EP=='S01E50':atomic_json(approval,dict(schema_version=1,approved=True,approved_by='human',
            scope='extra_tail_background_only',episode=EP,master_sha256=EXPECTED,
            expected_duration_seconds=3649.933,actual_duration_seconds=3650.440,
            human_quote='对 尾部是一点点背景 裁背景素材的时候有一点误差 属合理情况',
            reason='Explicit previously recorded human approval for this exact E50 tail'))
        source_probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format',
            '-of','json',str(ROOT/'input'/f'{EP}.mp4')],text=True))
        command=[sys.executable,str(HERE/'validate_master.py'),'--file',str(master),'--receipt',str(marker),
                 '--expected-duration',str(source_probe['format']['duration'])]
        if EP=='S01E50':command+=['--duration-approval',str(approval)]
        run_logged(command)
    validated=json.loads(marker.read_text());require(validated['sha256']==EXPECTED,'Validation belongs to another master')
    stat=master.stat();current=[stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]
    identity=FOLDER/'verified-master-identity.json'
    if identity.exists():require(json.loads(identity.read_text())['stat']==current,'Validated master changed')
    else:atomic_json(identity,{'sha256':EXPECTED,'stat':current})
    if EP in ('S01E52','S01E53','S01E54','S01E55') and existing_job.exists():
        assert_unstarted_native_submission(POST,json.loads(existing_job.read_text()),EP)
    srt,style,fonts=prepare_inputs(master)
    if EP in ('S01E52','S01E53','S01E54','S01E55'):
        reference=FOLDER/'master-reference-v1/manifest.json'
        if not reference.exists():
            set_state('PRESERVE_NATIVE_MASTER_REFERENCES')
            if reference.parent.exists():
                directory=reference.parent
                require(directory.is_dir() and not directory.is_symlink()
                    and directory.stat().st_uid==os.getuid()
                    and all(not p.is_symlink() and p.stat().st_uid==os.getuid()
                            and (p.is_file() or p.is_dir()) for p in directory.rglob('*')),
                    'Unbounded incomplete reference directory')
                archive=FOLDER/('master-reference-incomplete-'+str(time.time_ns()))
                directory.rename(archive)
                atomic_json(FOLDER/('reference-recovery-'+str(time.time_ns())+'.json'),
                    dict(master_sha256=EXPECTED,preserved_incomplete_directory=str(archive),
                         reason='Interrupted reference capture without manifest; preserved, never deleted'))
            from capture_master_review import capture
            capture(ROOT,EP,reference.parent)
        references=json.loads(reference.read_text())
        require(references['master_sha256']==EXPECTED,'Master references differ')
        for frame in references['frames']:
            image=Path(frame['path'])
            require(not image.is_symlink() and image.resolve().parent==reference.parent.resolve()
                    and sha256(image)==frame['sha256'],'Master reference image differs')
    calibration=FOLDER/'native-calibration-ready.json'
    if not calibration.exists():
        set_state('BOUNDED_CURRENT_MASTER_NATIVE_CALIBRATION')
        with (POST/'jobs/.worker.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            context=prepare(POST,EP,EXPECTED,srt,style,fonts,POST/'logs'/f'{EP}.log')
            calibrate(master,POST,EP,context,POST/'logs'/f'{EP}.log')
            atomic_json(calibration,{'master_sha256':EXPECTED,'recipe_sha256':context['recipe_sha256']})
    job_path=POST/'jobs'/f'{EP}.json'
    if not job_path.exists():
        atomic_json(job_path,dict(schema_version=1,episode=EP,state='WAIT_DISK',master='incoming/'+master.name,
            master_sha256=EXPECTED,srt=f'../output/{EP}.zh.srt',style=f'styles/{EP}.ass',
            output=f'final/{EP}.final-1080p60.mkv',created_at=datetime.now().astimezone().isoformat(),
            picture_pipeline='native_light_gfpgan_rife_ass_one_encode',detail='Persistent full-phase budget admission'))
    job=json.loads(job_path.read_text());require(job.get('master_sha256')==EXPECTED,'Existing queue identity differs')
    if EP in ('S01E52','S01E53','S01E54','S01E55'):
        adopted=adopt_native_submission(job,EP)
        if adopted!=job:
            archive=FOLDER/'submitted-job-before-native-adoption.json'
            if not archive.exists():atomic_json(archive,job)
            atomic_json(job_path,adopted);job=adopted
    if job['state']=='READY':
        set_state('FINAL_READY_PENDING_WINDOWS',report=str(POST/'reports'/f'{EP}.json'));return
    active=subprocess.check_output(['systemctl','--user','is-active',UNIT],text=True).strip() if subprocess.run(
        ['systemctl','--user','is-active','--quiet',UNIT]).returncode==0 else 'inactive'
    if active=='active':
        set_state('PRODUCTION_WORKER_ACTIVE',job_state=job['state']);return
    seconds=validated['duration_seconds'];audio=validated['audio']
    paths={'cache':POST/'cache','scratch':POST/'cache','final':POST/'final'}
    devices={key:path.stat().st_dev for key,path in paths.items()}
    free={path.stat().st_dev:shutil.disk_usage(path).free for path in paths.values()}
    peaks=phase_peaks(seconds,int(audio['sample_rate']),audio['channels'],devices)
    held={}
    gate=POST/'reports/S01E51.receive-storage-gate.json'
    if EP=='S01E50' and gate.exists():
        reservation=json.loads(gate.read_text())
        if reservation.get('upload_allowed') is True:
            allocated=0
            for name in ['S01E51.clean-master.mov','S01E51.clean-master.mov.uploading']:
                target=POST/'incoming'/name
                if target.exists():
                    stat=target.stat();allocated+=min(stat.st_size,stat.st_blocks*512)
            held[(POST/'incoming').stat().st_dev]=max(0,reservation['expected_bytes']-allocated)
    if EP in ('S01E52','S01E53','S01E54','S01E55'):
        manifest=load_remaining_manifest()
        held[(POST/'incoming').stat().st_dev]=remaining_upload_bytes(POST,EP,manifest['masters'])
    reserve=minimum_free_gib();assessment=evaluate(peaks,free,reserve,held)
    budget=dict(master_sha256=EXPECTED,reserve_gib=reserve,**assessment,
                cache=str((POST/'cache').resolve()),
                note='Per-filesystem phase maxima; allocated inputs counted once; authorized future uploads reserved. GPU episodes stay serialized.')
    atomic_json(FOLDER/'production-budget.json',budget)
    if not budget['allowed']:
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


def remaining_upload_bytes(post,current,masters):
    """Finite future uploads only; never reserve released predecessor masters again."""
    remaining=0
    for episode,profile in masters.items():
        if episode<=current:continue
        allocated=0
        for suffix in ('.mov','.mov.uploading'):
            target=Path(post)/'incoming'/(episode+'.clean-master'+suffix)
            if target.exists():
                stat=target.stat();allocated+=min(stat.st_size,stat.st_blocks*512)
        remaining+=max(0,profile['bytes']-allocated)
    return remaining


def adopt_native_submission(job,episode):
    """Redirect only a not-started generic submission, preserving custom styles."""
    require(episode in ('S01E52','S01E53','S01E54','S01E55')
            and job.get('episode')==episode,'Wrong finite queued episode')
    style=f'styles/{episode}.ass'
    if job.get('style')==style:return job
    require(job.get('style')=='styles/template.ass' and job.get('state')=='QUEUED'
            and not job.get('enhanced_sha256'),
            'Preserve custom style or already-started queued job for review')
    return dict(job,style=style,picture_pipeline='native_light_gfpgan_rife_ass_one_encode',
                detail='Adopted verified Windows submission with current-master calibrated native ASS')


def assert_unstarted_native_submission(post,job,episode):
    if job.get('style')==f'styles/{episode}.ass':return
    adopt_native_submission(job,episode)
    cache=Path(post)/'cache'/episode
    segments=cache/(episode+'.segments')
    require(not segments.exists() and not segments.is_symlink()
            and not list(cache.glob(episode+'.enhanced*'))
            and not list(cache.glob(episode+'.source*')),
            'Generic job already has production artifacts; preserve them before any active-profile change')


def validate_remaining_manifest(manifest):
    expected=[f'S01E{n}' for n in range(52,56)]
    require(manifest.get('authorized') is True and manifest.get('order')==expected
            and set(manifest.get('masters',{}))==set(expected),'Only finite authorized E52-E55 manifest allowed')
    for profile in manifest['masters'].values():
        require(type(profile.get('bytes')) is int and profile['bytes']>0,'Invalid remaining master bytes')
        digest=profile.get('sha256')
        require(digest is None or (isinstance(digest,str) and bool(re.fullmatch('[0-9a-f]{64}',digest))),
                'Invalid remaining master SHA')
    return manifest


def load_remaining_manifest():
    return validate_remaining_manifest(json.loads((POST/'backend/managed-remaining.json').read_text()))


def managed_remaining():
    """Only E52-E55; actual per-episode acceptance/cleanup gates the next episode."""
    folder=POST/'reports/managed-E52-E55';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'owner.lock').open('a+') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            current=None
            try:
                for episode in ('S01E52','S01E53','S01E54','S01E55'):
                    current=episode
                    review=POST/'reports'/f'{episode}.server-acceptance.json'
                    cleanup=POST/'reports'/f'{episode}.stage-cleanup.json'
                    technical=POST/'reports'/f'{episode}.json'
                    if review.exists() and cleanup.exists() and technical.exists():
                        a,c,t=[json.loads(p.read_text()) for p in (review,cleanup,technical)]
                        if (a.get('server_accepted') is True and a.get('bounded_visual_review_pass') is True
                                and c.get('complete') is True and a.get('sha256')==c.get('sha256')==t.get('sha256')):
                            continue
                    configure_episode(episode)
                    FOLDER.mkdir(parents=True,exist_ok=True)
                    if not (FOLDER/'progress.log').exists():(FOLDER/'progress.log').touch()
                    atomic_json(folder/'status.json',dict(episode=episode,stage='MANAGING_CURRENT_EPISODE',
                        at=datetime.now().astimezone().isoformat(),owner_pid=os.getpid(),episode_status=str(FOLDER/'status.json')))
                    with (FOLDER/'owner.lock').open('a+') as episode_owner:
                        fcntl.flock(episode_owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        tick()
                    break
                else:
                    atomic_json(folder/'status.json',dict(stage='ALL_FOUR_SERVER_ACCEPTED',
                        at=datetime.now().astimezone().isoformat(),owner_pid=os.getpid()))
                    return
            except (WaitDisk,WaitGPU,BlockingIOError) as error:
                atomic_json(folder/'status.json',dict(stage='RESOURCE_WAIT',episode=current,error=str(error),owner_pid=os.getpid()))
            except FileNotFoundError as error:
                atomic_json(folder/'status.json',dict(stage='WAIT_ACTUAL_INPUT_OR_HANDOFF',episode=current,error=str(error),owner_pid=os.getpid()))
            except Exception as error:
                atomic_json(folder/'status.json',dict(stage='NEEDS_ATTENTION',episode=current,error=repr(error),owner_pid=os.getpid()))
            time.sleep(30)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode',choices=[f'S01E{n}' for n in range(50,56)],default='S01E50')
    parser.add_argument('--managed-remaining',action='store_true',help='Manage finite E52-E55 serial queue')
    args=parser.parse_args()
    if args.managed_remaining:managed_remaining()
    else:
        configure_episode(args.episode)
        main()
