#!/usr/bin/env python3
"""Audit completed E49 rework receipts without GPU work or media mutation."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import re


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
                    and x['faces_restored']<=x['faces_detected'],'Invalid face counts: '+p.name)
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
    return dict(at=datetime.now().astimezone().isoformat(),fingerprint=fingerprint,
        scope='Completed segment SHA/frame-log coverage audit only. Prior strict decode is bound in each receipt; no repeated full media decode or visual/PCM approval.',
        source_sha_freshly_checked=verify_source,all_frames_present=expected==first+total,
        final_acceptance=False,verified_segments=len(rows),frames=expected-first,
        restored_frames=sum(x['restored_frames'] for x in rows),
        no_detection_frames=sum(x['no_detection_frames'] for x in rows),
        failed_frames=0,protected_bottom_unchanged_frames=expected-first,
        no_detection_runs_seconds=[dict(start=a/60,end=b/60,frames=b-a) for a,b in gaps if b-a>=60],
        segments=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--verify-source',action='store_true');args=p.parse_args()
    result=audit(args.work,args.verify_source)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    if args.report.exists():raise FileExistsError('Audit report already exists; preserve prior evidence')
    with args.report.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='segments'},ensure_ascii=False))


if __name__=='__main__':main()
