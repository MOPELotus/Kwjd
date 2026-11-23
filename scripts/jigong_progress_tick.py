"""One scheduled pure-tool status check; optionally queue the existing main chat.

Never starts another model supervisor, seals subtitles, approves quality or
signals/kills media processes. A model wakeup requires fresh real quota >=2%.
"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from enhance_stage import atomic_json


def read(path):
    path=Path(path)
    return json.loads(path.read_text()) if path.is_file() else {}


def quota():
    result=subprocess.run([str(Path.home()/'.local/bin/codex-quota-status')],
        capture_output=True,text=True,check=True,timeout=45)
    values=re.findall(r'([0-9]+(?:[.][0-9]+)?)%',result.stdout)
    if not values:raise ValueError('Account service returned no real quota percentage')
    return min(float(value) for value in values)


def additional_master_observations(root):
    """Observe registered receiving roots; presence only requests main review."""
    result=[]
    for episode,binding in read(root/'.kwjd/queued-master-storage.json').get('episodes',{}).items():
        if not re.fullmatch(r'S03E\d{2}',episode):raise ValueError('Invalid queued episode')
        receiving=Path(binding['remote_root_for_existing_uploader'])
        handoff=receiving/'post/handoffs'/(episode+'.master-received.json')
        if not receiving.is_absolute() or str(handoff)!=binding['staged_real_handoff']:
            raise ValueError('Receiving root differs from its registered handoff')
        progress=read(root/'post'/('managed-'+episode)/'progress.json')
        result.append(dict(episode=episode,handoff_path=str(handoff),
            handoff_present=handoff.is_file(),master_verified=progress.get('master_handoff_verified') is True))
    return result


def event(snapshot,previous):
    job=snapshot['job'];state=job.get('state');previous_job=previous.get('job',{})
    clean=snapshot.get('clean_recovery_state')
    if clean=='FAILED_RETAINED_FOR_INSPECTION':return 'FIXED_CLEAN_RECOVERY_TEST_NEEDS_ATTENTION'
    if snapshot['review_ready'] and not job:return 'FIRST_SRT_READY_START_PRODUCTION'
    if clean=='PASSED' and not snapshot.get('clean_recovery_acknowledged',False):return 'FIXED_CLEAN_RECOVERY_TEST_PASSED'
    following=snapshot.get('following_episode',{})
    if following.get('handoff_present') and not following.get('master_verified'):
        return 'FOLLOWING_MASTER_HANDOFF_REQUIRES_VERIFICATION:S03E03'
    for queued in snapshot.get('additional_masters',[]):
        if queued['handoff_present'] and not queued['master_verified']:
            return 'FOLLOWING_MASTER_HANDOFF_REQUIRES_VERIFICATION:'+queued['episode']
    if state in ('FAILED','WAIT_DISK','WAIT_GPU','WAIT_BACKEND_TECHNICAL','WAIT_STYLE_APPROVAL'):
        return 'PRODUCTION_NEEDS_ATTENTION:'+state
    if state=='READY' and not snapshot['cloud_final_exists']:return 'FINAL_READY_REQUIRES_ACTUAL_CODEX_QC'
    if (state=='READY' and snapshot['cloud_final_exists'] and following.get('master_verified')
            and following.get('review_ready') and not following.get('job_exists')):
        return 'FOLLOWING_EPISODE_READY_FOR_SERIAL_PRODUCTION:S03E03'
    if snapshot['windows_online'] and not snapshot['windows_rule_acknowledged']:
        return 'ORIGINAL_WINDOWS_ONLINE_RULE_HANDOFF_PENDING'
    if snapshot['receipt_exists'] and not snapshot['both_cleaned']:return 'WINDOWS_RECEIPT_REQUIRES_VERIFICATION'
    if state in ('ENHANCING','PROCESSING') and snapshot['media_log_age_seconds']>3600:
        return 'PRODUCTION_LOG_UNCHANGED_OVER_ONE_HOUR'
    if not snapshot.get('review_checkpoint_complete',False) and snapshot.get('review_checkpoint_age_seconds',0)>3600:
        return 'SUBTITLE_REVIEW_CHECKPOINT_UNCHANGED_OVER_ONE_HOUR'
    if snapshot['both_cleaned'] and not previous.get('both_cleaned'):return 'DELIVERY_AND_CLEANUP_COMPLETED'
    if state and state!=previous_job.get('state') and state not in ('ENHANCING','PROCESSING','WAIT_SUBTITLE'):
        return 'PRODUCTION_STAGE_CHANGED:'+state
    return None


def tick(config,wake=False):
    config=read(config);root=Path(config['root']).resolve(strict=True)
    thread=config['thread_id']
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',thread):
        raise ValueError('Exact existing main thread UUID required')
    project=root/'repo/config/projects/jigong-s03.json'
    policy=read(project)['cloud_supervision'];threshold=policy['model_pause_below_remaining_percent']
    if threshold!=2 or policy.get('continue_media_processes_when_model_paused') is not True:
        raise ValueError('Current explicit cloud-only quota/media policy required')
    area=root/'post/managed-S03E02';area.mkdir(parents=True,exist_ok=True)
    with (area/'progress-timer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return inspect(root,area,config,thread,threshold,wake)


def inspect(root,area,config,thread,threshold,wake):
    previous=read(area/'timer-status.json');job=read(root/'post/jobs/S03E02.json')
    log=root/'post/logs/S03E02.log';now=time.time()
    from subtitle_review_supervisor import validate_episode
    review_ready=validate_episode(root,'S03E02')
    review_path=root/'ocr/subtitle-review/progress.json';review=read(review_path)
    target=read(area/'windows-target.json');online=False;network_error=None
    try:
        data=json.loads(subprocess.check_output([str(Path.home()/'.local/bin/tailscale'),
            'status','--json'],text=True,timeout=15))
        online=any(peer.get('HostName','').casefold()==target.get('device_name','lotuspc').casefold()
                   and peer.get('Online') is True for peer in data.get('Peer',{}).values())
    except Exception as error:network_error=str(error)
    clean_report=read(root/'post/reports/fixed-clean-recovery.json')
    snapshot=dict(schema_version=1,checked_at=now,main_thread_id=thread,job=job,
        clean_recovery_state=clean_report.get('state'),clean_recovery_acknowledged=clean_report.get('main_acknowledged') is True,
        review_ready=review_ready,media_log_age_seconds=now-log.stat().st_mtime if log.exists() else 1e10,
        review_checkpoint_age_seconds=now-review_path.stat().st_mtime if review_path.exists() else 1e10,
        review_checkpoint_complete=review.get('complete') is True and len(review.get('completed_episodes',[]))==13,
        subtitle_episode=review.get('episode'),subtitle_stage=review.get('stage'),
        subtitle_text_cues_checked=len(review.get('text_reviewed_original_cue_ids',[])),
        cloud_final_exists=(root/'post/handoffs/S03E02.cloud-final.json').is_file(),
        receipt_exists=(root/'post/receipts/S03E02.json').is_file(),
        both_cleaned=read(area/'progress.json').get('stage')=='DELIVERED_AND_GENERATED_ARTIFACTS_CLEANED',
        windows_online=online,network_error=network_error,
        windows_rule_acknowledged=target.get('cloud_rule_acknowledged') is True,
        media_processes_untouched=True)
    following=read(root/'post/managed-S03E03/progress.json')
    if following.get('serial_after')=='S03E02':
        snapshot['following_episode']=dict(episode='S03E03',
            handoff_present=(root/'post/handoffs/S03E03.master-received.json').is_file(),
            master_verified=following.get('master_handoff_verified') is True,
            review_ready=validate_episode(root,'S03E03'),
            job_exists=(root/'post/jobs/S03E03.json').is_file())
    snapshot['additional_masters']=additional_master_observations(root)
    try:snapshot['quota_remaining_percent']=quota()
    except Exception as error:snapshot.update(quota_remaining_percent=None,quota_error=str(error))
    kind=event(snapshot,previous);snapshot['attention_event']=kind
    remaining=snapshot['quota_remaining_percent']
    snapshot['model_state']='WAIT_QUOTA_READ' if remaining is None else ('PAUSED_BELOW_2_PERCENT' if remaining<threshold else 'QUOTA_AVAILABLE')
    last=read(area/'timer-last-dispatch.json')
    # Stable event keys prevent repeatedly queuing the same unresolved request.
    signature=hashlib.sha256(json.dumps([kind,job.get('updated_at') if kind and kind.startswith('PRODUCTION_STAGE_CHANGED') else None,
        snapshot['clean_recovery_state'] if kind=='FIRST_SRT_READY_START_PRODUCTION' else None],sort_keys=True).encode()).hexdigest()
    snapshot['dispatch']='NO_ATTENTION_EVENT' if not kind else 'MODEL_WAKE_NOT_REQUESTED'
    atomic_json(area/'timer-status.json',snapshot)
    if kind and last.get('signature')==signature and previous.get('attention_event')==kind:
        snapshot['dispatch']='ALREADY_QUEUED_EVENT'
    elif kind and wake and remaining is not None and remaining>=threshold:
        message=('【云端半小时巡检】事件 '+kind+'。读取 post/managed-S03E02/timer-status.json 与 progress.json，'
            '接续同一济公传任务，不重复原生字幕子代理/生产Worker，不启动codex exec监督器。'
            '第一回真实SRT封存后先绑定实际母版时钟再生产；实际质量证据与lotuspc收据才交付清理。'
            '第二回观察post/managed-S03E03/progress.json；真实到货须核验，逐回绑定字幕/母版时钟/样式，首回后串行推进，不等本地再次发命令。'
            '另盘接收根读取.kwjd/queued-master-storage.json和对应managed进度；第三回接在第二回后，禁止再次清理已开始制作的新一回Windows缓存。'
            '真实额度低于2%保存并暂停模型，后台实际媒体继续。只在需要动作/新结果时回复；处理后交回定时巡检。')
        command=[config.get('codex_binary',str(Path.home()/'.local/bin/codex')),'queue','--thread',thread,'--message',message,'--resume']
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=60)
            dispatch=dict(signature=signature,event=kind,at=time.time(),returncode=result.returncode,
                stdout=result.stdout[-2000:],stderr=result.stderr[-2000:],
                state='QUEUED_RESUME_REQUESTED' if result.returncode==0 else 'QUEUE_FAILED')
            if result.returncode==0 and ('warning' in result.stderr.casefold() or 'failed' in result.stderr.casefold()):
                dispatch['state']='QUEUE_SUCCEEDED_RESUME_UNCONFIRMED'
        except subprocess.TimeoutExpired:
            dispatch=dict(signature=signature,event=kind,at=time.time(),state='QUEUE_RESULT_UNCERTAIN_NO_DUPLICATE_RETRY')
        if dispatch.get('returncode')==0 or 'UNCERTAIN' in dispatch['state']:
            atomic_json(area/'timer-last-dispatch.json',dispatch)
        atomic_json(area/'timer-dispatch-attempt.json',dispatch);snapshot['dispatch']=dispatch['state']
    atomic_json(area/'timer-status.json',snapshot)
    with (area/'timer-history.jsonl').open('a') as stream:
        stream.write(json.dumps(snapshot,ensure_ascii=False)+'\n')
    print(json.dumps(snapshot,ensure_ascii=False))
    return snapshot


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--wake',action='store_true')
    args=parser.parse_args();tick(args.config,args.wake)
