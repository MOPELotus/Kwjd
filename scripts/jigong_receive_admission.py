"""Refresh a native25 upload lease while preserving the active production peak.

Pure tools only: never start a transfer, media Worker, or model conversation.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from enhance_stage import atomic_json
from jigong_master_restart import enforce
from queue_storage import evaluate, phase_peaks


def transfer_assessment(peaks, free, incoming_device, size, present):
    if not 0 <= present <= size:raise ValueError('Invalid incoming partial size')
    peaks=dict(peaks);peaks.setdefault(incoming_device,0)
    return evaluate(peaks,free,5,{incoming_device:size-present})


def snapshot(root, episode, production_root=None):
    root=Path(root);post=root/'post'
    production_post=Path(production_root or root)/'post'
    intake=json.loads((post/f'managed-{episode}/native25-intake.json').read_text())
    digest=intake['expected_master_sha256'];size=intake['expected_master_bytes']
    if not re.fullmatch(r'S03E\d{2}',episode) or type(size) is not int or size<=0:
        raise ValueError('Invalid native25 intake identity')
    enforce(episode,digest,'25/1')
    if intake.get('upload_paused_by_user') is True:
        return dict(episode=episode,expected_master_sha256=digest,expected_master_bytes=size,
            checked_at=time.time(),remote_root=str(root),production_admitted=False,
            admitted=False,assessment={'allowed':False,'reason':'User paused upload; explicit local/user resumption required'},
            state='USER_PAUSED_UPLOAD')
    incoming=post/'incoming';incoming.mkdir(parents=True,exist_ok=True)
    target=incoming/(episode+'.clean-master.mov');partial=target.with_suffix('.mov.uploading')
    receipt=post/'handoffs'/(episode+'.master-received.json')
    record=json.loads(receipt.read_text()) if receipt.exists() else {}
    received=(record.get('transfer_verified') is True and record.get('episode')==episode
        and record.get('sha256')==digest and record.get('bytes')==size
        and record.get('master_frame_rate')=='25/1' and target.is_file()
        and target.stat().st_size==size)
    base=dict(episode=episode,expected_master_sha256=digest,expected_master_bytes=size,
              checked_at=time.time(),remote_root=str(root),production_admitted=False,
              production_root=str(Path(production_root or root)))
    if received:
        return {**base,'admitted':False,'assessment':{'allowed':False},
                'state':'MATCHING_RECEIPT_REQUIRES_MAIN_VERIFICATION'}
    if target.exists():raise ValueError('Unmatched canonical mother retained for inspection')
    jobs=[]
    for path in (production_post/'jobs').glob('S03E*.json'):
        job=json.loads(path.read_text())
        if path.stem!=episode and job.get('state') not in ['READY','SUPERSEDED_WAIT_NATIVE25_MASTER']:
            jobs.append(job)
    if len(jobs)>1:raise ValueError('More than one prior production owner; retain capacity')
    paths={'cache':production_post/'cache','scratch':production_post/'cache','final':production_post/'final'}
    devices={k:p.stat().st_dev for k,p in paths.items()}
    free={p.stat().st_dev:shutil.disk_usage(p).free for p in [*paths.values(),incoming]}
    peaks={}
    if jobs:
        job=jobs[0];ep=job['episode'];mother=production_post/'incoming'/(ep+'.clean-master.mov')
        received_master=json.loads((production_post/'handoffs'/(ep+'.master-received.json')).read_text())
        if received_master.get('sha256')!=job['master_sha256']:
            raise ValueError('Active production master receipt identity differs')
        audio=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0',
            '-show_entries','stream=sample_rate,channels','-of','json',str(mother)]))['streams'][0]
        peaks=phase_peaks(received_master['duration_seconds'],int(audio['sample_rate']),audio['channels'],devices)
    assessment=transfer_assessment(peaks,free,incoming.stat().st_dev,size,
                                   partial.stat().st_size if partial.exists() else 0)
    return {**base,'admitted':assessment['allowed'],'assessment':assessment,
            'state':'UPLOAD_CAPACITY_ADMITTED' if assessment['allowed'] else 'WAIT_ACTIVE_PRODUCTION_PEAK',
            'active_production_episodes':[j['episode'] for j in jobs],
            'scope':'Full conservative active phase peaks plus remaining incoming bytes and5GiB per filesystem; actual receipt still needs main verification'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--episode',required=True)
    parser.add_argument('--production-root',type=Path,
                        help='Actual sole Worker data root when receiving on another disk')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    # Validate before constructing any episode-dependent output path.
    if not re.fullmatch(r'S03E\d{2}',args.episode):parser.error('Invalid episode')
    admission=args.root/'post/handoffs'/(args.episode+'.upload-admission.json')
    status=args.root/'post'/('managed-'+args.episode)/'native25-receive-capacity.json'
    while True:
        try:data=snapshot(args.root,args.episode,args.production_root)
        except Exception as error:
            # Expire any old positive lease on errors; never keep stale admission.
            old=json.loads(admission.read_text()) if admission.exists() else {}
            data={**old,'checked_at':time.time(),'admitted':False,
                  'assessment':{'allowed':False,'reason':str(error)},'state':'WAIT_VERIFIABLE_CAPACITY'}
        atomic_json(admission,data);atomic_json(status,data)
        if args.once or data['state']=='MATCHING_RECEIPT_REQUIRES_MAIN_VERIFICATION':return
        time.sleep(60)


if __name__=='__main__':main()
