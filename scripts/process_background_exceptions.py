"""Encode explicit, visually reviewed exception plans while preserving native timing.

Clean originals never enter this runner. Plans contain source SHA, a reviewed crop,
standard output dimensions, and hashes of the actual framing previews inspected.
"""
import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from PIL import Image, ImageDraw

FFMPEG='D:/Apps/ffmpeg/ffmpeg.exe'
FFPROBE='D:/Apps/ffmpeg/ffprobe.exe'


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(path)


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def video_filter(item):
    w,h=item['output_size'];x,y,cw,ch=item['crop']
    if any(not isinstance(v,int) or v<0 for v in [x,y,cw,ch,w,h]) or min(cw,ch,w,h)<=0:
        raise ValueError('Invalid geometry')
    if any(v%2 for v in [x,y,cw,ch,w,h]):raise ValueError('Geometry must be even for 4:2:0')
    # Scaling after an explicit crop preserves display aspect; no stretching.
    square=''
    sar=Fraction(item.get('source_sar','1:1').replace(':','/'))
    if sar!=1:square=f',scale={int(cw*sar)//2*2}:{ch},setsar=1'
    return f'crop={cw}:{ch}:{x}:{y}{square},scale={w}:{h}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={w}:{h},setsar=1'


def prepare_previews(plan_path,evidence):
    """Render cached source frames with the exact encode filter, without encoding video."""
    plan=read(plan_path);pages=[];evidence.mkdir(parents=True,exist_ok=True)
    for start in range(0,len(plan['items']),8):
        sheet=Image.new('RGB',(1440,8*290),(18,18,18));draw=ImageDraw.Draw(sheet);rows=[]
        for row,item in enumerate(plan['items'][start:start+8]):
            draw.text((4,row*290+3),item['id']+' '+str(item['crop'])+' -> '+str(item['output_size']),fill='white')
            for col,frame in enumerate(read(item['native_receipt'])['frames']):
                if sha(frame['file'])!=frame['sha256']:raise ValueError('Cached source frame changed')
                # Image input SAR is explicit because PNG storage does not reliably retain it.
                tmp=evidence/(item['id']+f'-{col}.jpg')
                subprocess.run([FFMPEG,'-v','error','-i',frame['file'],'-vf',
                    'setsar='+item.get('source_sar','1:1').replace(':','/')+','+video_filter(item)+',scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2',
                    '-frames:v','1',str(tmp)],check=True,capture_output=True)
                with Image.open(tmp) as im:sheet.paste(im,(col*480,row*290+20))
            rows.append(item['id'])
        page=evidence/f'preview-{len(pages)+1:02d}.jpg';sheet.save(page,quality=95)
        pages.append(dict(file=str(page),sha256=sha(page),ids=rows))
    atomic(evidence/'manifest.json',dict(pages=pages,scope='Exact crop/scale previews of three cached native samples. Requires actual visual inspection before encoding.'))
    print(json.dumps(dict(pages=len(pages),items=len(plan['items']))))


def timestamp_check(packets,timeline=None):
    """Validate packet timing without imposing CFR or a particular frame rate."""
    pts=set();count=0;last=None
    for line in packets:
        if not line.strip():continue
        fields=line.strip().split(',')
        if len(fields)!=2 or 'N/A' in fields:raise ValueError('Missing packet timestamps')
        p,d=map(int,fields)
        if p in pts or (last is not None and d<=last):raise ValueError('Duplicate PTS or non-monotonic DTS')
        pts.add(p);last=d;count+=1
    if not count:raise ValueError('No video packets')
    if timeline is not None:timeline.extend(sorted(pts))
    return count


def output_review_pages(evidence):
    receipts=[p for p in sorted(evidence.glob('*/receipt.json')) if not (p.parent/'output-visual-review.json').exists()]
    directory=evidence/'visual-pages'/time.strftime('%Y%m%d-%H%M%S')
    directory.mkdir(parents=True,exist_ok=False);pages=[]
    for start in range(0,len(receipts),8):
        sheet=Image.new('RGB',(1440,8*300),(18,18,18));rows=[]
        for n,path in enumerate(receipts[start:start+8]):
            rec=read(path)
            if sha(rec['item']['destination'])!=rec['sha256']:raise ValueError('Encoded output SHA changed')
            with Image.open(rec['visual_contact']) as im:sheet.paste(im,(0,n*300))
            rows.append(dict(id=path.parent.name,receipt=str(path),output_sha256=rec['sha256'],source_sha256=rec['source_sha256']))
        page=directory/f'page-{len(pages)+1:02d}.jpg';sheet.save(page,quality=95)
        pages.append(dict(file=str(page),sha256=sha(page),rows=rows))
    atomic(directory/'manifest.json',dict(pages=pages,items=len(receipts),scope='Three encoded frames per source, prepared for actual batch visual inspection; preparation is not acceptance.'))
    print(json.dumps(dict(manifest=str(directory/'manifest.json'),items=len(receipts))))


def run(plan_path,evidence):
    plan=read(plan_path);evidence.mkdir(parents=True,exist_ok=True)
    lock=evidence/'worker.lock'
    with lock.open('x') as stream:stream.write(str(os.getpid()))
    completed=[]
    def status(state,**extra):
        atomic(evidence/'status.json',dict(state=state,pid=os.getpid(),completed=completed,total=len(plan['items']),at=time.time(),**extra))
    try:
        for item in plan['items']:
            name=item['id'];work=evidence/name;work.mkdir(exist_ok=True)
            dest=Path(item['destination']);receipt=work/'receipt.json'
            if dest.exists():
                if not receipt.exists() or read(receipt)['sha256']!=sha(dest):raise ValueError('Existing output has no matching receipt')
                completed.append(name);continue
            if not item.get('framing_reviewed') or not item.get('reviewed_previews'):raise ValueError('Framing review missing')
            for preview in item['reviewed_previews']:
                if sha(preview['file'])!=preview['sha256']:raise ValueError('Framing preview changed')
            if shutil.disk_usage(evidence).free<40*1024**3:raise RuntimeError('Less than 40 GiB free')
            source=Path(item['source']);before=source.stat()
            if sha(source)!=item['source_sha256']:raise ValueError('Source SHA changed')
            native=json.loads(subprocess.check_output([FFPROBE,'-v','error','-select_streams','v:0','-show_streams','-of','json',str(source)]))['streams'][0]
            x,y,cw,ch=item['crop']
            if x+cw>native['width'] or y+ch>native['height']:raise ValueError('Crop outside source')
            partial=work/'encoded.partial.mp4'
            if partial.exists():raise ValueError('Prior partial retained for investigation')
            command=[FFMPEG,'-v','warning','-nostdin','-hwaccel','cuda','-i',str(source),'-map','0:v:0','-an','-sn','-dn',
                '-vf',video_filter(item),'-fps_mode','passthrough','-enc_time_base','demux','-c:v','hevc_nvenc','-preset','p3',
                '-tune','hq','-rc','vbr','-cq','19','-b:v','0','-bf','0','-pix_fmt','yuv420p','-tag:v','hvc1','-movflags','+faststart',
                '-map_metadata','-1','-progress',str(work/'progress.txt'),str(partial)]
            atomic(work/'command.json',dict(args=command,source_sha256=item['source_sha256']))
            started=time.monotonic()
            with (work/'encode.log').open('wb') as log:
                child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
                status('ENCODING',item=name,child_pid=child.pid)
                if child.wait():raise RuntimeError('Encoder failed; partial and log retained')
            status('VERIFYING',item=name)
            probe=json.loads(subprocess.check_output([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(partial)]))
            v=probe['streams'][0];duration=float(probe['format']['duration'])
            if len(probe['streams'])!=1 or [v['width'],v['height']]!=item['output_size'] or v.get('sample_aspect_ratio')!='1:1':
                raise ValueError('Unexpected output dimensions/SAR/streams')
            if abs(duration-item['duration'])>max(0.15,2/float(Fraction(native['avg_frame_rate']))):raise ValueError('Duration changed')
            packet_command=[FFPROBE,'-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pts,dts','-of','csv=p=0']
            counts=[];timelines=[]
            for packet_source in [source,partial]:
                process=subprocess.Popen(packet_command+[str(packet_source)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                timeline=[]
                try:counts.append(timestamp_check(process.stdout,timeline))
                except BaseException:process.kill();process.communicate();raise
                _,stderr=process.communicate()
                if process.returncode or stderr:raise ValueError('Packet scan failed')
                timelines.append(timeline)
            if counts[0]!=counts[1]:raise ValueError('Frame count changed')
            bases=[Fraction(native['time_base']),Fraction(v['time_base'])]
            timing_error=max(abs(float((a-timelines[0][0])*bases[0]-(b-timelines[1][0])*bases[1])) for a,b in zip(*timelines))
            if timing_error>float(max(bases))+1e-8:raise ValueError('Native presentation timing changed')
            checks=[];sheet=Image.new('RGB',(1440,300),(18,18,18));draw=ImageDraw.Draw(sheet)
            for n,t in enumerate([min(1,duration/4),duration/2,max(0,duration-1)]):
                check=subprocess.run([FFMPEG,'-v','error','-ss',str(t),'-i',str(partial),'-t','1','-map','0:v:0','-fps_mode','passthrough','-enc_time_base','demux','-f','null','-'],capture_output=True,text=True)
                if check.returncode or check.stderr.strip():
                    atomic(work/f'decode-{n}-failure.json',dict(start=t,exit_code=check.returncode,stderr=check.stderr))
                    raise ValueError('Output sample decode failed')
                target=work/f'output-{n}.jpg'
                subprocess.run([FFMPEG,'-v','error','-ss',str(t),'-i',str(partial),'-map','0:v:0','-frames:v','1','-vf','scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2',str(target)],check=True,capture_output=True)
                with Image.open(target) as im:sheet.paste(im,(n*480,25))
                draw.text((n*480+4,4),f'{name} {t:.2f}s',fill='white')
                checks.append(dict(start=t,seconds=1,exit_code=0,stderr=''))
            contact=work/'output-contact.jpg';sheet.save(contact,quality=95)
            after=source.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during encoding')
            result=dict(state='TECHNICAL_VERIFIED_VISUAL_PENDING',item=item,source_sha256=item['source_sha256'],sha256=sha(partial),bytes=partial.stat().st_size,
                seconds=duration,video=v,packet_count=counts[1],source_packet_count=counts[0],native_frame_count_preserved=True,all_pts_unique=True,
                all_dts_strictly_monotonic=True,max_relative_pts_error_seconds=timing_error,decode_windows=checks,render_seconds=time.monotonic()-started,visual_contact=str(contact),
                scope='Native packet count retained, complete packet timestamp checks and three bounded decode windows. Output picture approval pending.')
            dest.parent.mkdir(parents=True,exist_ok=True);partial.replace(dest);atomic(receipt,result)
            completed.append(name);status('ITEM_TECHNICAL_VERIFIED',item=name)
        status('ALL_TECHNICAL_VERIFIED_VISUAL_PENDING')
    except BaseException as error:
        status('FAILED',error_type=type(error).__name__,error=str(error));raise
    finally:lock.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--prepare-previews',action='store_true')
    parser.add_argument('--output-review-pages',action='store_true')
    args=parser.parse_args()
    if args.prepare_previews:prepare_previews(args.plan,args.evidence)
    elif args.output_review_pages:output_review_pages(args.evidence)
    else:run(args.plan,args.evidence)
