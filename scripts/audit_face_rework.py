#!/usr/bin/env python3
"""Audit completed E49 rework receipts without GPU work or media mutation."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
import subprocess


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def require(condition,message):
    if not condition:raise ValueError(message)


def audit(work,verify_source=False):
    work=Path(work)
    manifest=json.loads((work/'full.manifest.json').read_text())
    fingerprint=json.loads((work/'plan.json').read_text())['fingerprint']
    first=manifest['start_frame'];total=manifest['frames'];size=manifest['segment_frames']
    require(type(first) is int and type(total) is int and type(size) is int
            and first>=0 and total>0 and size>0,'Invalid manifest frame bounds')
    source=Path(manifest['source'])
    if verify_source:require(digest(source)==manifest['source_sha256'],'Original source SHA changed')
    rows=[];gaps=[];gap=None;expected=first
    multi_runs=[];multi=None;multi_count=0;maximum_faces=0
    paths=[p for p in sorted((work/'segments').glob('segment-*.json'))
           if re.fullmatch(r'segment-\d{5}\.json',p.name)]
    for p in paths:
        d=json.loads(p.read_text());index=int(p.stem.split('-')[1])
        count=min(size,first+total-expected)
        require(count>0 and index==(expected-first)//size,'Missing/extra/reordered segment: '+p.name)
        require(d.get('fingerprint')==fingerprint and d.get('source_start_frame')==expected
                and d.get('frames')==count,'Segment context/frame bounds changed: '+p.name)
        media=p.with_suffix('.mkv');log=Path(d['frame_log'])
        require(not media.is_symlink() and not log.is_symlink()
                and log.parent.absolute()==(work/'segments').absolute(),'Unbounded segment/log path')
        require(digest(media)==d['validation']['sha256'],'Segment media SHA changed: '+p.name)
        require(digest(log)==d['frame_log_sha256'],'Frame log SHA changed: '+p.name)
        records=[json.loads(line) for line in log.read_bytes().splitlines()]
        require([x.get('frame') for x in records]==list(range(expected,expected+count)),
                'Missing/duplicated frame coverage: '+p.name)
        require(d.get('processed_frames')==count and d.get('failed_frames')==0
                and d.get('protected_bottom_unchanged_frames')==count
                and d['validation'].get('frames')==count,'Incomplete processing/protection/decode: '+p.name)
        for x in records:
            require(all(type(x.get(k)) is int and x[k]>=0 for k in ('faces_detected','faces_restored'))
                    and x['faces_restored']==x['faces_detected'],'Invalid face counts: '+p.name)
            detected=x['faces_detected'];maximum_faces=max(maximum_faces,detected)
            if detected>1:
                multi_count+=1
                if multi is None:multi=[x['frame'],x['frame']+1,detected]
                else:multi[1]=x['frame']+1;multi[2]=max(multi[2],detected)
            elif multi is not None:multi_runs.append(multi);multi=None
            if x['faces_detected']==0:
                if gap is None:gap=[x['frame'],x['frame']+1]
                else:gap[1]=x['frame']+1
            elif gap is not None:gaps.append(gap);gap=None
        require(sum(x['faces_restored']>0 for x in records)==d.get('restored_frames')
                and sum(x['faces_restored'] for x in records)==d.get('faces_restored')
                and sum(x['faces_detected']==0 for x in records)==d.get('no_detection_frames'),
                'Frame/receipt statistics mismatch: '+p.name)
        rows.append(dict(segment=p.stem,start=expected,frames=count,
            restored_frames=d['restored_frames'],no_detection_frames=d['no_detection_frames'],
            faces_restored=d['faces_restored'],failed_frames=d['failed_frames'],
            protected_bottom_unchanged_frames=d['protected_bottom_unchanged_frames'],
            media_sha256=d['validation']['sha256'],frame_log_sha256=d['frame_log_sha256']))
        expected+=count
    if gap is not None:gaps.append(gap)
    if multi is not None:multi_runs.append(multi)
    return dict(at=datetime.now().astimezone().isoformat(),fingerprint=fingerprint,
        scope='Completed segment SHA/frame-log coverage audit only. Prior strict decode is bound in each receipt; no repeated full media decode or visual/PCM approval.',
        source_sha_freshly_checked=verify_source,all_frames_present=expected==first+total,
        final_acceptance=False,verified_segments=len(rows),frames=expected-first,
        restored_frames=sum(x['restored_frames'] for x in rows),
        no_detection_frames=sum(x['no_detection_frames'] for x in rows),
        failed_frames=0,protected_bottom_unchanged_frames=expected-first,
        no_detection_runs_seconds=[dict(start=a/60,end=b/60,frames=b-a) for a,b in gaps if b-a>=60],
        multi_face_frames=multi_count,max_faces_detected=maximum_faces,
        multi_face_runs_seconds=[dict(start_frame=a,end_frame_exclusive=b,start=a/60,end=b/60,
            frames=b-a,max_faces=maximum) for a,b,maximum in multi_runs],
        segments=rows)


def sample_frames(work,directory,seconds,audited):
    """CPU-decode whole-second evidence frames; preserve native PNGs and hashes."""
    work=Path(work);directory=Path(directory)
    manifest=json.loads((work/'full.manifest.json').read_text())
    first=manifest['start_frame'];size=manifest['segment_frames']
    require(seconds and len(set(seconds))==len(seconds)
            and all(type(t) is int and first<=t*60<first+audited['frames'] for t in seconds),
            'Sample positions must be unique whole seconds inside the audited completed prefix')
    require(manifest.get('fps')==60 and first%60==0 and size%60==0,
            'Sampling requires the exact60fps whole-second rework clock')
    directory.mkdir(parents=True,exist_ok=False)
    from PIL import Image,ImageDraw
    contact=Image.new('RGB',(1536,456*len(seconds)));evidence=[]
    for row,t in enumerate(seconds):
        index=(t*60-first)//size;local=(t*60-first-index*size)/60
        media=work/'segments'/f'segment-{index:05d}.mkv'
        for col,(kind,path,position) in enumerate([
                ('source',Path(manifest['source']),t),('rework',media,local)]):
            png=directory/f'{t}-{kind}.png'
            command=['ffmpeg','-nostdin','-v','error','-n','-threads','2','-filter_threads','2',
                '-ss',str(position),'-i',str(path),'-frames:v','1',str(png)]
            subprocess.run(command,check=True)
            with Image.open(png) as image:
                require(image.size==(1920,1080),'Unexpected evidence-frame dimensions')
                thumbnail=image.convert('RGB').resize((768,432))
            contact.paste(thumbnail,(768*col,456*row+24))
            ImageDraw.Draw(contact).text((768*col+8,456*row+5),f'{t}s {kind}',fill='white')
            evidence.append(dict(second=t,kind=kind,path=str(png),sha256=digest(png),command=command))
    output=directory/'source-vs-rework.png';contact.save(output)
    record=dict(at=datetime.now().astimezone().isoformat(),fps=60,seconds=seconds,
        fingerprint=audited['fingerprint'],source_sha256_expected=manifest['source_sha256'],
        source_sha_freshly_checked=audited['source_sha_freshly_checked'],
        native_frames_preserved=True,decoder='FFmpeg CPU decode; no GPU/render acceptance claim',
        visual_acceptance=False,contact=str(output),contact_sha256=digest(output),captures=evidence)
    with (directory/'manifest.json').open('x') as stream:json.dump(record,stream,ensure_ascii=False,indent=2)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--verify-source',action='store_true')
    p.add_argument('--sample-seconds',type=int,nargs='+',help='Optional CPU native-frame evidence; no visual approval')
    args=p.parse_args()
    if args.report.exists():raise FileExistsError('Audit report already exists; preserve prior evidence')
    result=audit(args.work,args.verify_source)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    if args.sample_seconds:
        sample_frames(args.work,args.report.parent/'visual-samples',args.sample_seconds,result)
    print(json.dumps({k:v for k,v in result.items() if k!='segments'},ensure_ascii=False))


if __name__=='__main__':main()
