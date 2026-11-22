"""Persist honest waiting state without starting uploads, OCR or GPU workers."""
import argparse
import json
from pathlib import Path
import time

from jigong_post_supervisor import EPISODE,MASTER,quota,save,sha


def master_ready(root):
    master=root/'post/incoming'/(EPISODE+'.clean-master.mov')
    receipt=root/'post/handoffs'/(EPISODE+'.master-received.json')
    if not master.is_file() or not receipt.is_file():return False
    if master.is_symlink() or receipt.is_symlink():raise ValueError('Unexpected master/receipt symlink')
    data=json.loads(receipt.read_text())
    return (data.get('episode')==EPISODE and data.get('sha256')==MASTER
            and data.get('transfer_verified') is True and data.get('bytes')==10302910705
            and master.stat().st_size==10302910705 and sha(master)==MASTER)


def wait(root,kind,poll=30,timeout=0):
    progress=root/'post/managed-S03E02/progress.json'
    began=time.monotonic();last_quota=-float('inf')
    while True:
        if time.monotonic()-last_quota>=120:
            remaining=quota();last_quota=time.monotonic()
            if remaining<5:
                data=json.loads(progress.read_text()) if progress.exists() else {}
                data.update(stage='WAIT_QUOTA_RECOVERY',quota_remaining_percent=remaining,updated_at=time.time())
                save(progress,data);return 75
        if kind=='master':ready=master_ready(root)
        else:
            from subtitle_review_supervisor import validate_episode
            ready=validate_episode(root,EPISODE)
        data=json.loads(progress.read_text()) if progress.exists() else {}
        data.update(episode=EPISODE,updated_at=time.time(),quota_remaining_percent=remaining,
                    input_wait={'kind':kind,'ready':ready,'checked_at':time.time()})
        if kind=='master':
            partial=root/'post/incoming'/(EPISODE+'.clean-master.mov.uploading')
            data['input_wait']['upload_bytes_observed']=partial.stat().st_size if partial.is_file() else None
        if not ready:data['stage']='WAIT_ATOMIC_MASTER_HANDOFF' if kind=='master' else 'WAIT_SEALED_SRT'
        save(progress,data)
        if ready:return 0
        if timeout and time.monotonic()-began>=timeout:return 44
        time.sleep(poll)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--kind',choices=['master','sealed-srt'],required=True)
    parser.add_argument('--poll',type=int,default=30)
    parser.add_argument('--timeout',type=int,default=0)
    args=parser.parse_args()
    if not 10<=args.poll<=60:parser.error('Poll interval must be10..60seconds')
    raise SystemExit(wait(args.root.resolve(strict=True),args.kind,args.poll,args.timeout))
