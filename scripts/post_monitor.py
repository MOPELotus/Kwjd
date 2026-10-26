#!/usr/bin/env python3
"""Read-only persistent post-processing heartbeat and physical-GPU telemetry."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime,timezone
from enhance_stage import atomic_json


def snapshot(post):
    jobs=[]
    for file in (post/'jobs').glob('S01E*.json'):
        job=json.loads(file.read_text());item={k:job.get(k) for k in ('episode','state','detail','updated_at')}
        context=post/'cache'/job['episode']/(job['episode']+'.segments')
        # Discover actual wrapper directories rather than manufacture completion.
        segment_dirs=list((post/'cache'/job['episode']).glob('*.segments/*/context.json'))
        for header in segment_dirs:
            data=json.loads(header.read_text())
            if data.get('master_sha256')!=job.get('master_sha256'):continue
            receipts=list(header.parent.glob('[0-9]*.ready.json'))
            # Progress is explicitly telemetry only; never a completion seal.
            count=sum(json.loads(r.read_text()).get('source_frames',0) for r in receipts)
            item['segment_progress']={'context':str(header),'receipts':len(receipts),
                'source_frames_in_receipts':count,'source_frames_total':data.get('input_frames'),
                'not_a_completion_claim':True}
        jobs.append(item)
    fields='index,name,temperature.gpu,clocks.sm,utilization.gpu,memory.used,memory.free,power.draw,clocks_event_reasons.sw_thermal_slowdown'
    raw=subprocess.check_output(['nvidia-smi','--query-gpu='+fields,'--format=csv,noheader,nounits'],text=True)
    disks={str((post/x).resolve()):{'free_bytes':os.statvfs(post/x).f_bavail*os.statvfs(post/x).f_frsize} for x in ('cache','final')}
    return {'captured_at':datetime.now(timezone.utc).isoformat(),'jobs':jobs,'gpu_csv_fields':fields,
            'gpu_csv_rows':raw.strip().splitlines(),'filesystems':disks,'monitor_pid':os.getpid()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--post',default=str(Path.home()/'qingqingwu/post'))
    p.add_argument('--poll',type=int,default=30);p.add_argument('--once',action='store_true');args=p.parse_args()
    post=Path(args.post);(post/'reports').mkdir(parents=True,exist_ok=True)
    while True:
        try:
            record=snapshot(post);atomic_json(post/'reports/live_status.json',record)
            with (post/'logs/telemetry.jsonl').open('a') as out:out.write(json.dumps(record,ensure_ascii=False)+'\n')
        except Exception as error:
            print(str(error),flush=True)
            if args.once:raise
        if args.once:return
        time.sleep(max(15,args.poll))

if __name__=='__main__':main()
