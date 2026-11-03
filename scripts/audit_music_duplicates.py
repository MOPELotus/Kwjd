"""Read-only full-track encoded/decoded audio duplicate audit; no source deletion."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from triage_mihoyo_music import read, atomic


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def decode(job):
    row, receipt, ffmpeg = job
    before=Path(row['file']).stat()
    if sha(row['file'])!=row['sha256']:raise ValueError('Source SHA mismatch')
    result=subprocess.run([ffmpeg,'-v','error','-xerror','-i',row['file'],'-map','0:a:0','-vn',
                           '-c:a','pcm_s32le','-f','hash','-hash','sha256','-'],capture_output=True,text=True,
                          timeout=max(120,min(1800,row['duration']*2)),check=True)
    match=re.fullmatch(r'SHA256=([a-f0-9]{64})\s*',result.stdout)
    if not match or result.stderr.strip():raise ValueError('Decode hash validation failed')
    after=Path(row['file']).stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed')
    stream=receipt['streams'][0]
    return dict(source_sha256=row['sha256'],pcm_sha256=match[1],sample_rate=int(stream['sample_rate']),
                channels=int(stream['channels']),format='native-rate native-channels signed-32-bit PCM',
                full_decode_ok=True,source_bytes=after.st_size,source_mtime_ns=after.st_mtime_ns)


def pcm_identity(r):
    return r['sample_rate'],r['channels'],r['pcm_sha256']


def main(args):
    selection=args.root/'.kwjd/mihoyo-music/selection'
    directory=selection/'dedup-v1';directory.mkdir(parents=True,exist_ok=True)
    rows=read(selection/'catalog.json')['tracks']
    unique={r['sha256']:r for r in rows};results={};jobs=[]
    for digest,row in unique.items():
        dest=directory/'receipts'/(digest+'.json')
        if dest.exists():
            r=read(dest);stat=Path(row['file']).stat()
            if r.get('source_sha256')==digest and r.get('source_bytes')==stat.st_size and r.get('source_mtime_ns')==stat.st_mtime_ns:
                results[digest]=r;continue
        receipt=read(args.root/'.kwjd/mihoyo-music/download/receipts'/(row['key']+'.json'))
        jobs.append((row,receipt,args.ffmpeg))
    started=time.monotonic();errors=0
    atomic(directory/'runner.json',dict(pid=os.getpid(),state='RUNNING',workers=args.workers))
    def checked(job):
        try:return job[0]['sha256'],decode(job),None
        except Exception as e:return job[0]['sha256'],None,type(e).__name__
    selected=jobs[:args.limit] if args.limit else jobs
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index,(digest,result,error) in enumerate(pool.map(checked,selected)):
            if error:
                errors+=1;atomic(directory/'errors'/(digest+'.json'),dict(source_sha256=digest,type=error))
            else:
                results[digest]=result;atomic(directory/'receipts'/(digest+'.json'),result)
            atomic(directory/'status.json',dict(state='RUNNING',verified=len(results),total=len(unique),errors=errors,
                        processed_this_run=index+1,elapsed_seconds=round(time.monotonic()-started,1)))
    if args.limit and len(results)<len(unique):
        atomic(directory/'runner.json',dict(pid=None,state='BOUNDED_RUN_COMPLETE'))
        return
    groups=defaultdict(list)
    for r in rows:
        if r['sha256'] in results:
            identity=(r['game'],)+pcm_identity(results[r['sha256']])
            groups[identity].append(dict(key=r['key'],title=r['title'],album=r['album'],sha256=r['sha256'],file=r['file']))
    duplicates=[dict(game=k[0],sample_rate=k[1],channels=k[2],pcm_sha256=k[3],members=v)
                for k,v in groups.items() if len(v)>1]
    state='COMPLETE' if len(results)==len(unique) and not errors else 'INCOMPLETE'
    output=dict(state=state,tracks=len(rows),encoded_unique=len(unique),decoded_unique=len(groups),
                duplicate_groups=len(duplicates),extra_duplicate_entries=sum(len(g['members'])-1 for g in duplicates),
                verified=len(results),errors=errors,groups=duplicates,
                note='Exact full decoded PCM at matching native rate/channels. Different performances/masters are not collapsed by title. Sources are retained.')
    atomic(directory/'duplicates.json',output)
    atomic(directory/'status.json',{k:v for k,v in output.items() if k!='groups'})
    atomic(directory/'runner.json',dict(pid=None,state=state))
    print(json.dumps({k:v for k,v in output.items() if k!='groups'}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('D:/qingqingwu'))
    p.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe')
    p.add_argument('--workers',type=int,choices=range(1,9),default=8)
    p.add_argument('--limit',type=int,default=0)
    main(p.parse_args())
