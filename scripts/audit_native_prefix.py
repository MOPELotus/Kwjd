#!/usr/bin/env python3
"""Read-only native segment/pipeline/frame-log audit during E50–E55 production.

No inference, repeated decode, media changes or final acceptance declaration.
Counts primary native frames separately from intentional RIFE lookahead repeats.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def require(condition,message):
    if not condition:raise ValueError(message)


def audit(context):
    context=context.absolute();identity=json.loads((context/'context.json').read_text())
    require(identity['episode'] in ('S01E50','S01E51','S01E52','S01E53','S01E54','S01E55'),
            'Outside authorized native episodes')
    total=identity['input_frames'];size=identity['segment_source_frames'];expected=0;rows=[]
    source_restored=source_noface=lookahead=output=0;seconds=0.0
    ready=sorted(p for p in context.iterdir() if re.fullmatch(r'\d{6}\.ready\.json',p.name))
    for path in ready:
        receipt=json.loads(path.read_text());index=int(path.name[:6]);count=min(size,total-expected)
        require(index==len(rows) and count>0,'Missing/extra/reordered segment')
        require(all(receipt.get(k)==v for k,v in identity.items()),'Segment context changed')
        require(receipt['source_start']==expected and receipt['source_frames']==count,'Source coverage changed')
        media=context/f'{index:06d}.mkv';pipeline=Path(receipt['pipeline_receipt_path'])
        require(not media.is_symlink() and not pipeline.is_symlink() and pipeline.parent==context,
                'Unbounded segment evidence')
        require(digest(media)==receipt['sha256'],'Segment SHA changed')
        require(digest(pipeline)==receipt['pipeline_receipt_sha256'],'Pipeline receipt SHA changed')
        details=json.loads(pipeline.read_text());extra=int(expected+count<total)
        frames=Path(str(pipeline).removesuffix('.pipeline.json')+'.face-frames.jsonl')
        require(not frames.is_symlink() and digest(frames)==details['frame_log_sha256'],'Frame-log SHA changed')
        data=[json.loads(line) for line in frames.read_bytes().splitlines()]
        require([d['source_frame'] for d in data]==list(range(expected,expected+count+extra)),
                'Duplicate/missing source frames')
        require(all(d['faces_detected']==d['faces_restored'] for d in data),'Detected face not restored')
        require(details['coverage']['processed_frames']==count+extra
                and details['coverage']['failed_frames']==0
                and details['lookahead_frames']==extra and details['video_encodes']==1
                and details['ass_after_rife'] is True,'Incomplete or wrong-order processing')
        out=(count*12+4)//5
        require(receipt['frames']==details['output_frames']==out,'Wrong25-to60 frame count')
        restored=sum(d['faces_restored']>0 for d in data[:count])
        source_restored+=restored;source_noface+=count-restored;lookahead+=extra;output+=out
        seconds+=details['pipeline_seconds']
        rows.append(dict(index=index,start=expected,native_frames=count,output_frames=out,
                         primary_restored_frames=restored,lookahead_frames=extra,
                         media_sha256=receipt['sha256'],pipeline_sha256=receipt['pipeline_receipt_sha256']))
        expected+=count
    return dict(at=datetime.now().astimezone().isoformat(),episode=identity['episode'],
        master_sha256=identity['master_sha256'],backend_fingerprint=identity['backend_fingerprint'],
        scope='Snapshot of immutable completed prefix only; no repeated decode or whole-final acceptance',
        verified_segments=len(rows),native_frames=expected,total_native_frames=total,output_frames=output,
        primary_restored_frames=source_restored,primary_no_detection_frames=source_noface,
        lookahead_processing_frames=lookahead,failed_frames=0,pipeline_seconds=seconds,
        measured_source_fps=expected/seconds if seconds else None,rows=rows,final_acceptance=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();result=audit(args.context)
    with args.report.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:result[k] for k in ['episode','verified_segments','native_frames','output_frames',
          'primary_restored_frames','lookahead_processing_frames','measured_source_fps','final_acceptance']}))
