"""Receive a technically validated master without claiming enhancement is ready."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from jigong_master_restart import enforce


def incoming_budget(incoming, total_bytes, partial=None, free_bytes=None):
    """Reserve only incoming's remaining bytes; production has separate gates."""
    present = partial.stat().st_size if partial is not None and partial.is_file() else 0
    if present > total_bytes:
        raise ValueError('Partial longer than master; retain for inspection')
    remaining = total_bytes - present
    reserve = 5 * 1024**3
    free = shutil.disk_usage(incoming).free if free_bytes is None else free_bytes
    if free < reserve + remaining:
        raise RuntimeError('Incoming needs remaining master bytes plus 5GiB reserve')
    return dict(scope='incoming_transfer_only_not_production_admission',
                remaining_master_bytes=remaining, reserve_bytes=reserve, free_bytes=free,
                downstream_budget='Worker checks cache/final peak on their real filesystems before production')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--episode',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--bytes',type=int,required=True)
    parser.add_argument('--expected-duration',type=float,required=True)
    parser.add_argument('--finalize',action='store_true')
    parser.add_argument('--expected-frame-rate',choices=['25/1','30/1'],default='25/1')
    parser.add_argument('--partial-info',action='store_true',help='Read-only prefix identity for safe resume')
    args=parser.parse_args()
    if not re.fullmatch(r'S0[1-4]E\d{2,3}',args.episode) or not re.fullmatch('[a-f0-9]{64}',args.sha256) or args.bytes<=0:
        parser.error('Invalid master identity')
    # Reject superseded identity/rate even at upload preflight or prefix-resume;
    # no directory creation, file rename or receipt publication has happened.
    enforce(args.episode,args.sha256,args.expected_frame_rate)
    post=args.root.resolve()/'post';incoming=post/'incoming'
    incoming.mkdir(parents=True,exist_ok=True)
    target=incoming/(args.episode+'.clean-master.mov')
    partial=target.with_suffix('.mov.uploading')
    if args.partial_info:
        if not partial.is_file():raise SystemExit(44)
        before=partial.stat()
        if before.st_size>args.bytes:raise ValueError('Partial longer than master')
        with partial.open('rb') as f:prefix=hashlib.file_digest(f,'sha256').hexdigest()
        after=partial.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('Partial is actively changing; do not start another writer')
        print(json.dumps({'partial_bytes':after.st_size,'partial_sha256':prefix}))
        return
    candidate=target if target.exists() else partial if args.finalize else None
    if candidate is None or not candidate.exists():
        incoming_budget(incoming,args.bytes,partial)
        raise SystemExit(44)
    if candidate.stat().st_size!=args.bytes:raise ValueError('Remote size mismatch; preserved')
    with candidate.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=args.sha256:raise ValueError('Remote master SHA mismatch; preserved')
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(candidate)]))
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    audio=next(s for s in info['streams'] if s['codec_type']=='audio')
    enforce(args.episode,digest,video['r_frame_rate'])
    enforce(args.episode,digest,video['avg_frame_rate'])
    if (video['codec_name']!='hevc' or video.get('profile')!='Main 10' or video.get('pix_fmt')!='yuv420p10le'
            or (video['width'],video['height'],video['r_frame_rate'])!=(1920,1080,args.expected_frame_rate)
            or video['avg_frame_rate']!=args.expected_frame_rate
            or audio['codec_name']!='pcm_s24le' or int(audio['sample_rate'])<48000 or audio['channels']!=2
            or abs(float(info['format']['duration'])-args.expected_duration)>.5):
        raise ValueError('Remote master media policy mismatch')
    for seek in (0,args.expected_duration/2,max(0,args.expected_duration-4)):
        subprocess.run(['ffmpeg','-v','error','-xerror','-nostdin','-ss',str(seek),'-i',str(candidate),
                        '-t','3','-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
    if candidate==partial:partial.rename(target)
    handoffs=post/'handoffs';handoffs.mkdir(exist_ok=True)
    receipt={'episode':args.episode,'path':str(target),'sha256':digest,'bytes':args.bytes,
             'duration_seconds':float(info['format']['duration']),'transfer_verified':True,
             'master_frame_rate':args.expected_frame_rate,
             'remote_sampled_decode_verified':True,'state':'MASTER_RECEIVED_WAIT_POST_REBUILD_AND_REVIEW',
             'received_at':time.time(),'local_master_deleted':False,'backend_started':False,
             'subtitle_reviewed':False,'style_approved_for_this_master':False}
    output=handoffs/(args.episode+'.master-received.json')
    temp=output.with_suffix('.json.tmp');temp.write_text(json.dumps(receipt,indent=2),encoding='utf8');temp.replace(output)
    print(json.dumps(receipt))


if __name__=='__main__':main()
