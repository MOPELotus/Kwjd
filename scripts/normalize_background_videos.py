import json,os,sys,time,subprocess,hashlib,shutil,argparse
from pathlib import Path
from fractions import Fraction
from PIL import Image,ImageDraw

parser=argparse.ArgumentParser(description='Normalize reviewed background videos with complete packet cadence checks')
parser.add_argument('--plan',type=Path,required=True)
parser.add_argument('--evidence',type=Path,required=True)
parser.add_argument('--playlist',type=Path,required=True)
args=parser.parse_args()
playlist_path=args.playlist
root=args.evidence
root.mkdir(parents=True,exist_ok=True)
plan=json.loads(args.plan.read_text(encoding='utf-8-sig'))
ffmpeg='D:/Apps/ffmpeg/ffmpeg.exe';ffprobe='D:/Apps/ffmpeg/ffprobe.exe'
lock=root/'worker.lock';lock.open('x').write(str(os.getpid()))
def atomic(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');tmp.replace(path)
def status(stage,item=None,**extra):
    atomic(root/'status.json',{'at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'pid':os.getpid(),'stage':stage,'item':item,'completed_indices':completed,**extra})
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
completed=[]
try:
    for item in sorted(plan['items'],key=lambda x:x['source_duration']):
        index=item['index'];work=root/f'{index:02d}';work.mkdir(exist_ok=True)
        dest=Path(item['destination']);receipt=work/'receipt.json'
        if dest.exists():
            if not receipt.exists():raise RuntimeError(f'Unreceipted destination exists: {dest}')
            old=json.loads(receipt.read_text(encoding='utf8'))
            if old.get('state')!='TECHNICAL_VERIFIED_VISUAL_PENDING' or sha(dest)!=old['sha256']:raise RuntimeError('Existing receipt or SHA mismatch')
            completed.append(index);continue
        if shutil.disk_usage(dest.parent).free<25*1024**3:raise RuntimeError('Less than 25 GiB free, stopped without deleting sources')
        source_sha=sha(item['source'])
        if item.get('expected_source_sha256') and source_sha!=item['expected_source_sha256']:raise RuntimeError('Reviewed source SHA mismatch')
        partial=work/'encoded.partial.mp4'
        if partial.exists():raise RuntimeError(f'Existing partial retained for investigation: {partial}')
        args=[ffmpeg,'-hide_banner','-nostdin','-y','-hwaccel','cuda','-i',item['source'],'-map','0:v:0','-an','-sn','-dn','-vf',item['filter'],'-r','60','-fps_mode','cfr','-c:v','hevc_nvenc','-preset','p5','-tune','hq','-rc','vbr','-cq','18','-b:v','0','-pix_fmt','yuv420p','-tag:v','hvc1','-movflags','+faststart','-map_metadata','-1','-progress',str(work/'encode.progress'),'-stats_period','2',str(partial)]
        atomic(work/'command.json',{'args':args,'source_sha256':source_sha,'item':item})
        start=time.monotonic()
        with (work/'encode.log').open('wb') as log:
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
            status('ENCODING',index,child_pid=process.pid,progress_file=str(work/'encode.progress'))
            if process.wait()!=0:raise RuntimeError(f'Encode failed item {index}; logs and partial retained')
        render_seconds=time.monotonic()-start
        status('VERIFYING',index)
        probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(partial)],encoding='utf8'))
        if len(probe['streams'])!=1:raise RuntimeError('Expected exactly one video stream')
        v=probe['streams'][0];duration=float(probe['format']['duration'])
        if (v['width'],v['height'],v['r_frame_rate'],v['avg_frame_rate'],v['sample_aspect_ratio'])!=(1920,1080,'60/1','60/1','1:1'):raise RuntimeError('Output spec mismatch')
        if abs(duration-item['source_duration'])>1:raise RuntimeError('Unexpected output duration difference')
        # Read every packet, proving a complete unique 60fps presentation timeline.
        pts=set();last_dts=None;count=0;ordered=True
        with (work/'packet-probe.stderr.log').open('wb') as log:
            proc=subprocess.Popen([ffprobe,'-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pts,dts','-of','csv=p=0',str(partial)],stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf8')
            for line in proc.stdout:
                if not line.strip():continue
                fields=line.strip().split(',');p,d=int(fields[0]),int(fields[1]);pts.add(p);count+=1
                if last_dts is not None and d<=last_dts:ordered=False
                last_dts=d
            if proc.wait()!=0:raise RuntimeError('Whole packet scan failed')
        step=1/(Fraction(v['time_base'])*60)
        if step.denominator!=1 or len(pts)!=count or min(pts)!=0 or max(pts)!=(count-1)*int(step) or any(p%int(step) for p in pts) or not ordered:raise RuntimeError('PTS/DTS cadence check failed')
        checks=[];sheet=Image.new('RGB',(1440,300),(15,15,15));draw=ImageDraw.Draw(sheet)
        for n,t in enumerate([min(5,duration/4),duration/2,max(0,duration-2)]):
            p=subprocess.run([ffmpeg,'-v','error','-ss',str(t),'-i',str(partial),'-t','2','-map','0:v:0','-f','null','-'],capture_output=True,text=True,encoding='utf8')
            checks.append({'start':t,'seconds':2,'exit_code':p.returncode,'stderr':p.stderr})
            if p.returncode or p.stderr.strip():raise RuntimeError('Sample decode failed or warned')
            target=work/f'output-{n}.jpg'
            subprocess.run([ffmpeg,'-v','error','-y','-ss',str(t),'-i',str(partial),'-frames:v','1','-vf','scale=480:270',str(target)],check=True)
            with Image.open(target) as image:sheet.paste(image,(n*480,25))
            draw.text((n*480+4,4),f'{index:02d} encoded {t:.2f}s',fill='white')
        sheet.save(work/'output-contact.jpg')
        result={'state':'TECHNICAL_VERIFIED_VISUAL_PENDING','item':item,'source_sha256':source_sha,'sha256':sha(partial),'bytes':partial.stat().st_size,'seconds':duration,'video':v,'packet_count':count,'all_pts_unique_60fps':True,'all_dts_strictly_monotonic':True,'decode_windows':checks,'render_seconds':render_seconds,'visual_contact':str(work/'output-contact.jpg'),'scope':'All packet timestamps and three 2-second decode windows; no full perceptual playback/listening.'}
        partial.replace(dest);atomic(receipt,result)
        completed.append(index);status('ITEM_TECHNICAL_VERIFIED',index)
        print(json.dumps({'completed':index,'count':len(completed),'render_seconds':render_seconds},ensure_ascii=False),flush=True)
    playlist=playlist_path
    playlist.write_text('#EXTM3U\n'+'\n'.join(line for item in plan['items'] for line in [f'#EXTINF:-1,{item["label"]}',Path(item['destination']).as_posix()])+'\n',encoding='utf8')
    status('ALL_TECHNICAL_VERIFIED_VISUAL_PENDING',playlist=str(playlist))
except BaseException as e:
    status('FAILED',error=str(e));print(str(e),file=sys.stderr,flush=True);raise
finally:
    lock.unlink(missing_ok=True)


