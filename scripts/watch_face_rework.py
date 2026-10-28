#!/usr/bin/env python3
"""Read-only inference observer; persistent metrics and sparse progress events.

Does not start/kill inference, clean media or declare visual acceptance.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time


def snapshot(folder,service):
    state_path=folder/'status.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'stage':'WAITING_FOR_WORKER'}
    info=subprocess.check_output(['systemctl','--user','show',service,
        '-p','ActiveState','-p','SubState','-p','NRestarts','-p','Result','-p','ExecMainStatus'],text=True)
    unit=dict(line.split('=',1) for line in info.splitlines() if '=' in line)
    gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,temperature.gpu,memory.used,memory.free,utilization.gpu',
        '--format=csv,noheader'],text=True).strip()
    disk=shutil.disk_usage(folder)
    done=state.get('frames_completed',0);total=state.get('total_frames',0)
    receipts=[]
    for p in sorted((folder/'segments').glob('segment-*.json')):
        if '.pending.' in p.name:continue
        try:receipts.append(json.loads(p.read_text()))
        except (OSError,json.JSONDecodeError):continue
    verified=sum(x.get('frames',0) for x in receipts)
    seconds=sum(x.get('pipeline_seconds',0) for x in receipts)
    speed=verified/seconds if seconds else None
    return dict(at=datetime.now().astimezone().isoformat(),state=state,service=unit,
        gpu=gpu,free_gib=disk.free/2**30,verified_segments=len(receipts),verified_frames=verified,
        progress_percent=100*done/total if total else 0,pipeline_fps=speed,
        estimated_remaining_inference_seconds=(total-done)/speed if speed else None,
        eta_scope='Verified segment processing only; initialization, pauses, final mux/QC/visual review additional.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--service',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args();previous=None;last_summary=0
    while True:
        try:
            data=snapshot(args.output,args.service)
            temporary=args.output/'monitor.tmp'
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
            temporary.replace(args.output/'monitor.json')
            with (args.output/'metrics.jsonl').open('a') as stream:stream.write(json.dumps(data,ensure_ascii=False)+'\n')
            event=(data['state']['stage'],data['service'].get('ActiveState'),data['service'].get('NRestarts'))
            if event!=previous or time.monotonic()-last_summary>300:
                message=f"{data['at']} MONITOR stage={event[0]} service={event[1]} restarts={event[2]} progress={data['progress_percent']:.2f}% verified={data['verified_frames']} home_free={data['free_gib']:.2f}GiB fps={data['pipeline_fps']} GPU={data['gpu'].replace(chr(10),' | ')}"
                with (args.output/'progress.log').open('a') as stream:stream.write(message+'\n')
                if event!=previous:
                    with (args.output/'events.jsonl').open('a') as stream:stream.write(json.dumps(data,ensure_ascii=False)+'\n')
                previous=event;last_summary=time.monotonic()
            if data['service'].get('ActiveState')=='failed':
                with (args.output/'ALERT.json').open('w') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
        except Exception as error:
            with (args.output/'monitor_errors.log').open('a') as stream:
                stream.write(datetime.now().astimezone().isoformat()+' '+repr(error)+'\n')
        if args.once:return
        time.sleep(30)


if __name__=='__main__':main()
