#!/usr/bin/env python3
"""Measure the actual resumable full video chain, retaining immutable evidence."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

from enhance_stage import atomic_json, sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config','input','output','episode','report','log'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--source-master-sha',required=True)
    parser.add_argument('--origin-seconds',type=float,required=True)
    args=parser.parse_args()
    report,log=Path(args.report),Path(args.log)
    if any(p.exists() for p in (report,log,Path(args.output))):
        raise ValueError('Benchmark evidence/output already exists; retained')
    record={'started_at':datetime.datetime.now().astimezone().isoformat(),
            'source_clip':str(Path(args.input).resolve()),'source_clip_sha256':sha256(args.input),
            'source_master_sha256':args.source_master_sha,'clip_origin_seconds':args.origin_seconds,
            'config_sha256':sha256(args.config),'output':str(Path(args.output).resolve()),
            'status':'running','scope':'Actual full restoration/interpolation/encode/segment validation/concat chain'}
    atomic_json(report,record)
    start=time.monotonic()
    with log.open('x') as handle:
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('segmented_backend.py')),
            args.input,args.output,args.episode,'--config',args.config],stdout=handle,stderr=subprocess.STDOUT)
    record['processing_seconds_including_segment_decode_validation_concat_transfer']=time.monotonic()-start
    record['exit_code']=result.returncode
    records=[]
    for line in log.read_text().splitlines():
        try:records.append(json.loads(line))
        except (ValueError,TypeError):pass
    record['segment_metrics']=[r for r in records if r.get('cuda_pipeline')]
    record['status']='completed' if result.returncode==0 else 'failed'
    if result.returncode==0:
        completed=list((Path(args.output).parent/(args.episode+'.segments')).glob('*/completed.json'))
        candidates=[json.loads(p.read_text()) for p in completed]
        matches=[r for r in candidates if r.get('sha256')==sha256(args.output)]
        if len(matches)!=1:raise ValueError('Output does not have a unique verified completion receipt')
        record.update(matches[0])
        record['source_fps']=record['input_frames']/record['processing_seconds_including_segment_decode_validation_concat_transfer']
    record['finished_at']=datetime.datetime.now().astimezone().isoformat()
    atomic_json(report,record)
    if result.returncode:raise SystemExit(result.returncode)


if __name__=='__main__':main()
