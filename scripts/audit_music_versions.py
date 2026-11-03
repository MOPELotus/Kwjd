"""Full Chromaprint comparisons for same-title/album/duration recordings.

Protects vocal/instrumental/live/remix variants. No file removal or retagging.
"""
import argparse
from array import array
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import subprocess
import unicodedata

from triage_mihoyo_music import read,atomic


def english(text):
    return ' '.join(re.findall('[a-z0-9]+',unicodedata.normalize('NFKD',text).casefold()))


def variant(text):
    s=unicodedata.normalize('NFKC',text).casefold()
    return tuple(name for name,words in [('backing',('和声伴奏','伴唱')),('instrumental',('伴奏','instrumental','off vocal','off-vocal')),
                ('live',('现场','live ver','live version','concert')),('remix',('remix','重混'))] if any(w in s for w in words))


def candidates(rows):
    groups=defaultdict(list)
    for r in rows:
        title=english(r['title'])
        if len(title)>=4:groups[(r['game'],english(r['album']),title,variant(r['title']))].append(r)
    return [g for g in groups.values() if len(g)>1 and max(r['duration'] for r in g)-min(r['duration'] for r in g)<=2]


def fingerprint(job):
    row,ffmpeg=job;path=Path(row['file']);before=path.stat()
    with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=row['sha256']:raise ValueError('Source SHA mismatch')
    r=subprocess.run([ffmpeg,'-v','error','-xerror','-i',str(path),'-map','0:a:0','-vn','-ac','1','-ar','11025',
                      '-f','chromaprint','-fp_format','raw','-'],capture_output=True,check=True,
                     timeout=max(120,min(1800,row['duration']*2)))
    if r.stderr.strip() or len(r.stdout)%4:raise ValueError('Invalid fingerprint decode')
    data=array('I');data.frombytes(r.stdout)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed')
    return dict(source_sha256=digest,fingerprint=list(data),source_bytes=after.st_size,source_mtime_ns=after.st_mtime_ns,
                algorithm=1,scope='Full-track Chromaprint, metadata-matched recording candidate; not bit-identical PCM proof')


def compare(a,b):
    if min(len(a),len(b))<30 or min(len(set(a)),len(set(b)))<12:return dict(match=False,reason='insufficient_variation')
    best=None
    for shift in range(-16,17):
        left=a[max(shift,0):];right=b[max(-shift,0):];size=min(len(left),len(right))
        if size/min(len(a),len(b))<.98:continue
        distances=[(x^y).bit_count()/32 for x,y in zip(left[:size],right[:size])]
        mean=sum(distances)/size
        thirds=[sum(distances[size*i//3:size*(i+1)//3])/len(distances[size*i//3:size*(i+1)//3]) for i in range(3)]
        if best is None or mean<best['distance']:
            best=dict(distance=mean,thirds=thirds,shift_frames=shift,coverage=size/min(len(a),len(b)))
    if best is None:return dict(match=False,reason='duration_or_alignment_difference')
    best['match']=best['distance']<=.025 and max(best['thirds'])<=.04
    return best


def main(args):
    s=args.root/'.kwjd/mihoyo-music/selection';folder=s/'dedup-v1/recording-versions'
    rows=read(s/'catalog.json')['tracks'];groups=candidates(rows)
    unique={r['sha256']:r for g in groups for r in g};results={};pending=[]
    for digest,row in unique.items():
        path=folder/'fingerprints'/(digest+'.json')
        if path.exists():
            result=read(path);stat=Path(row['file']).stat()
            if result.get('source_bytes')==stat.st_size and result.get('source_mtime_ns')==stat.st_mtime_ns:
                results[digest]=result;continue
        pending.append(row)
    errors=0
    def run(row):
        try:return row['sha256'],fingerprint((row,args.ffmpeg)),None
        except Exception as e:return row['sha256'],None,type(e).__name__
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i,(digest,result,error) in enumerate(pool.map(run,pending)):
            if error:errors+=1;atomic(folder/'errors'/(digest+'.json'),dict(type=error))
            else:results[digest]=result;atomic(folder/'fingerprints'/(digest+'.json'),result)
            atomic(folder/'status.json',dict(state='RUNNING',done=len(results),total=len(unique),errors=errors))
    pairs=[];different=[]
    for g in groups:
        for a,b in combinations(g,2):
            if a['sha256'] not in results or b['sha256'] not in results:continue
            comparison=compare(results[a['sha256']]['fingerprint'],results[b['sha256']]['fingerprint'])
            item=dict(keys=[a['key'],b['key']],source_sha256=[a['sha256'],b['sha256']],**comparison)
            (pairs if comparison['match'] else different).append(item)
    report=dict(state='COMPLETE' if not errors else 'INCOMPLETE',candidate_groups=len(groups),unique_audio=len(unique),
                errors=errors,matching_recording_pairs=pairs,different_or_unconfirmed_pairs=different,
                note='Only same game, full English title, album, protected version markers and <=2s duration; strict full-track fingerprint and each-third agreement. Native sources retained.')
    atomic(folder/'results.json',report)
    atomic(folder/'status.json',{k:v for k,v in report.items() if k not in ['matching_recording_pairs','different_or_unconfirmed_pairs']})
    print(json.dumps(dict(state=report['state'],candidate_groups=len(groups),matches=len(pairs),not_merged=len(different),errors=errors)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('D:/qingqingwu'))
    p.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe');p.add_argument('--workers',type=int,default=8)
    main(p.parse_args())
