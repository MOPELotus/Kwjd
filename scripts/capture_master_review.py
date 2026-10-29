#!/usr/bin/env python3
"""Keep hash-bound native master reference frames before stagewise release."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

from storage_policy import minimum_free_gib
from enhance_stage import check_space


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def capture(root,episode,destination):
    root=root.resolve();post=root/'post';master=post/'incoming'/f'{episode}.clean-master.mov'
    if master.with_name(master.name+'.uploading').exists():raise ValueError('Upload is still in flight')
    receipt=json.loads((post/'receipts'/f'{episode}.upload.json').read_text(encoding='utf-8-sig'))
    if receipt.get('episode')!=episode or receipt.get('verified') is not True or receipt.get('local_master_deleted') is not True:
        raise ValueError('Actual upload/release handoff required')
    destination=destination.resolve()
    if not destination.is_relative_to((post/'reports').resolve()):raise ValueError('Reference evidence must stay in post/reports')
    check_space(destination,256*1024**2,minimum_free_gib())
    before=master.stat();digest=sha(master)
    if digest!=receipt['sha256']:raise ValueError('Master differs from actual receipt')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(master)]))
    duration=float(probe['format']['duration']);video=next(s for s in probe['streams'] if s['codec_type']=='video')
    if video['r_frame_rate']!='25/1' or float(video.get('start_time',0))!=0:raise ValueError('Unexpected master frame clock')
    count=round(duration*25)
    frames=sorted({round(t*25) for t in [0,30,60,100,300,600,900,1200,1500,1800,2100,2400,2700,3000,3300]
                   if t<duration}|{max(0,count-250),count-1})
    destination.mkdir(parents=True,exist_ok=False);rows=[]
    for index in frames:
        path=destination/f'{index:06d}.png';command=['ffmpeg','-v','info','-nostdin','-threads','2',
            '-copyts','-ss',f'{index/25:.6f}','-i',str(master),'-frames:v','1',
            '-filter_threads','1','-vf','showinfo','-fps_mode','passthrough','-compression_level','3','-n',str(path)]
        result=subprocess.run(command,capture_output=True,text=True,check=True)
        matches=re.findall(r'\bn:\s*0\s+pts:.*?pts_time:([\d.e+-]+)',result.stderr)
        if not path.exists() or not matches or abs(float(matches[0])-index/25)>.0001:
            raise ValueError('Decoded reference does not prove requested native frame')
        rows.append(dict(frame=index,pts_seconds=float(matches[0]),path=str(path),sha256=sha(path)))
    after=master.stat()
    if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('Source changed during capture; references not accepted')
    report=dict(at=datetime.now().astimezone().isoformat(),episode=episode,master_sha256=digest,
                source_bytes=before.st_size,frame_count=count,frames=rows,
                scope='CPU-decoded native1920x1080 reference images with actual source PTS; no generative model or media mutation',
                full_frame_human_review=False,visual_acceptance=False)
    (destination/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return dict(episode=episode,images=len(rows),manifest=str(destination/'manifest.json'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.home()/'qingqingwu')
    parser.add_argument('--episode',choices=['S01E50','S01E51'],required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(capture(args.root,args.episode,args.destination),indent=2))
