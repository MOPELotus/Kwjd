#!/usr/bin/env python3
"""Read-only persistent post-processing heartbeat and physical-GPU telemetry."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from datetime import datetime,timezone
from enhance_stage import atomic_json


def segment_progress(post, job, production):
    """Report bound completion receipts without hashing media or running backend."""
    result={'status':'unknown','not_a_completion_claim':True,
            'verification':'receipt-reported progress; media hash not re-read',
            'verified_segments':None,'source_frames_complete':None,
            'source_frames_total':None,'percent':None}
    try:
        ep=job.get('episode');digest=job.get('master_sha256');fp=production.get('backend_fingerprint')
        if (not isinstance(ep,str) or not re.fullmatch(r'S01E\d{2,3}',ep)
                or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)
                or not isinstance(fp,str) or not re.fullmatch('[0-9a-f]{64}',fp)
                or production.get('master_sha256')!=digest):
            raise ValueError('No matching active production fingerprint/master binding')
        # server_worker writes <cache>/<ep>/<ep>.enhanced.partial.mkv; the
        # wrapper creates this exact sibling directory, not arbitrary glob hits.
        directory=post/'cache'/ep/(ep+'.segments')/(digest[:16]+'-'+fp[:16])
        header=directory/'context.json'
        if header.is_symlink():raise ValueError('Symlink context retained without trust')
        data=json.loads(header.read_text())
        if not isinstance(data,dict):raise ValueError('Context JSON is not an object')
        keys=('episode','master_sha256','backend_fingerprint','input_frames','output_frames','segment_source_frames')
        if (data.get('episode')!=ep or data.get('master_sha256')!=digest
                or data.get('backend_fingerprint')!=fp):
            raise ValueError('Segment context differs from active job/backend')
        count=data.get('input_frames');size=data.get('segment_source_frames')
        if (type(count) is not int or count<=0 or type(size) is not int or size<5 or size>1500 or size%5
                or type(data.get('output_frames')) is not int
                or data['output_frames']!=(count*12+4)//5):
            raise ValueError('Invalid exact-frame segment context')
        identity={k:data[k] for k in keys}
        result.update(context=str(header),source_frames_total=count,backend_fingerprint=fp)
        total_segments=(count+size-1)//size
        completed=directory/'completed.json'
        def whole_completion():
            if completed.is_symlink():raise ValueError('Symlink completion receipt')
            record=json.loads(completed.read_text())
            if (not isinstance(record,dict) or any(type(record.get(k)) is not type(v) or record.get(k)!=v
                                                  for k,v in identity.items())
                    or not isinstance(record.get('sha256'),str)
                    or not re.fullmatch('[0-9a-f]{64}',record['sha256'])):
                raise ValueError('Whole enhancement completion receipt differs')
            # Verified concat replaces segment files; their normal release must
            # not make telemetry regress to zero or trigger media revalidation.
            result.update(status='known',basis='whole enhancement completion receipt; segments may be released',
                          verified_segments=total_segments,source_frames_complete=count,percent=100.0)
            return result
        if completed.exists():
            return whole_completion()
        frames=0;segments=0
        for receipt in directory.glob('[0-9]*.ready.json'):
            if receipt.is_symlink() or not re.fullmatch(r'\d{6}\.ready\.json',receipt.name):
                raise ValueError('Unexpected segment receipt name/type')
            record=json.loads(receipt.read_text());index=int(receipt.name[:6]);start=index*size
            if not isinstance(record,dict):raise ValueError('Segment receipt JSON is not an object')
            selected=min(size,count-start)
            binding={**identity,'index':index,'source_start':start,'source_frames':selected,
                     'frames':(selected*12+4)//5}
            if (not 0<=index<total_segments or any(type(record.get(k)) is not type(v) or record.get(k)!=v
                                                   for k,v in binding.items())
                    or not isinstance(record.get('sha256'),str)
                    or not re.fullmatch('[0-9a-f]{64}',record['sha256'])):
                raise ValueError('Segment receipt differs from exact context/index/frame binding')
            media=directory/f'{index:06d}.mkv'
            info=media.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size<=0:
                raise ValueError('Receipt media missing, empty, or not a regular file')
            frames+=selected;segments+=1
        # Catch ordinary stage cleanup/publication races instead of reporting a
        # spurious zero when the wrapper has just released all segment media.
        if completed.exists():return whole_completion()
        if json.loads(header.read_text())!=data:
            raise ValueError('Segment context changed while sampling progress')
        result.update(status='known',basis='bound segment receipts with present nonempty media',
                      verified_segments=segments,source_frames_complete=frames,percent=round(100*frames/count,3))
    except (OSError,ValueError,KeyError,TypeError) as error:
        result.update(status='unknown',reason=str(error))
    return result


def snapshot(post):
    post=Path(post)
    try:
        production=json.loads((post/'backend/production-ready.json').read_text())
        if not isinstance(production,dict):production={}
    except (OSError,ValueError):production={}
    jobs=[]
    for file in (post/'jobs').glob('S01E*.json'):
        try:
            job=json.loads(file.read_text(encoding='utf-8-sig'))
            if not isinstance(job,dict):raise ValueError('Job JSON is not an object')
            item={k:job.get(k) for k in ('episode','state','detail','updated_at')}
            if job.get('state') in ('ENHANCING','ENHANCED_READY','PROCESSING','READY','WAIT_DISK','WAIT_GPU'):
                item['segment_progress']=segment_progress(post,job,production)
        except (OSError,ValueError) as error:
            item={'job':str(file),'state':'unknown','reason':str(error)}
        jobs.append(item)
    fields='index,name,temperature.gpu,clocks.sm,utilization.gpu,memory.used,memory.free,power.draw,clocks_event_reasons.sw_thermal_slowdown'
    try:
        raw=subprocess.check_output(['nvidia-smi','--query-gpu='+fields,'--format=csv,noheader,nounits'],text=True)
        gpu={'gpu_csv_rows':raw.strip().splitlines()}
    except (OSError,subprocess.CalledProcessError) as error:
        gpu={'gpu_csv_rows':[],'gpu_status':'unknown','gpu_reason':str(error)}
    disks={}
    for zone in ('cache','final'):
        path=post/zone
        try:
            fs=os.statvfs(path)
            disks[str(path.resolve())]={'free_bytes':fs.f_bavail*fs.f_frsize}
        except OSError as error:
            disks[str(path)]={'status':'unknown','reason':str(error)}
    return {'captured_at':datetime.now(timezone.utc).isoformat(),'jobs':jobs,'gpu_csv_fields':fields,
            **gpu,'filesystems':disks,'monitor_pid':os.getpid()}


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
