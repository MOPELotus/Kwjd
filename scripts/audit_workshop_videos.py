"""Read-only source video audit. Technical evidence never grants visual approval."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from PIL import Image, ImageDraw


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)


def invoke(args):
    return subprocess.run(args,capture_output=True,timeout=180)


def audit(item, directory, args):
    item_root=(args.workshop/item['id']).resolve()
    source=Path(item['source']).resolve()
    if not source.is_relative_to(item_root) or not source.is_file():
        raise ValueError('Missing or unsafe source path')
    info=source.stat()
    receipt=directory/'technical.json'
    if receipt.exists():
        previous=json.loads(receipt.read_text(encoding='utf8'))
        if previous.get('bytes')==info.st_size and previous.get('mtime_ns')==info.st_mtime_ns:
            return previous
    directory.mkdir(parents=True,exist_ok=True)
    probe=invoke([args.ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(source)])
    if probe.returncode:
        raise ValueError('ffprobe failed')
    data=json.loads(probe.stdout)
    video=next((s for s in data['streams'] if s.get('codec_type')=='video'),None)
    if video is None:
        raise ValueError('No video stream')
    duration=float(data['format']['duration'])
    windows=[0,max(0,duration/2-1),max(0,duration-2)]
    checks,frames=[],[]
    for index,start in enumerate(windows):
        decoded=invoke([args.ffmpeg,'-v','error','-xerror','-ss',str(start),'-i',str(source),'-t','2','-map','0:v:0','-map','0:a?','-f','null','-'])
        checks.append({'start':start,'seconds':min(2,duration-start),'returncode':decoded.returncode,
                       'stderr':decoded.stderr.decode('utf8',errors='replace')[-1000:]})
        for step in range(3):
            moment=min(max(0,duration-.05),start+step*.5)
            frame=directory/f'{index}-{step}.jpg'
            result=invoke([args.ffmpeg,'-v','error','-y','-ss',str(moment),'-i',str(source),'-map','0:v:0','-frames:v','1','-vf','scale=480:-2',str(frame)])
            if result.returncode==0:
                frames.append((frame,moment))
    sheet=Image.new('RGB',(1440,900),(20,20,20));draw=ImageDraw.Draw(sheet)
    for index,(path,moment) in enumerate(frames):
        with Image.open(path) as frame:
            frame.thumbnail((480,270));x=index%3*480;y=index//3*300
            sheet.paste(frame,(x,y+24));draw.text((x+4,y+4),f'{item["id"]} {moment:.2f}s',fill='white')
    sheet.save(directory/'contact.jpg')
    with source.open('rb') as file:
        digest=hashlib.file_digest(file,'sha256').hexdigest()
    after=source.stat()
    if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('Source changed during audit; no receipt granted')
    passed=len(frames)==9 and all(c['returncode']==0 and not c['stderr'].strip() for c in checks)
    result={'state':'TECHNICAL_SAMPLED_VISUAL_PENDING' if passed else 'TECHNICAL_REVIEW_REQUIRED',
            'id':item['id'],'title':item['title'],'source':str(source),'bytes':info.st_size,
            'mtime_ns':info.st_mtime_ns,'sha256':digest,'duration_seconds':duration,
            'video':video,'audio':[s for s in data['streams'] if s.get('codec_type')=='audio'],
            'decode_windows':checks,'contact':str(directory/'contact.jpg'),
            'scope':'Three distributed 2-second decode windows, nine ordered stills; no full-film visual/audio or loop approval',
            'visual_reviewed':False,'crop_decided':False}
    save(receipt,result)
    return result


def main(args):
    items=json.loads(args.inventory.read_text(encoding='utf-8-sig'))['items']
    eligible=[x for x in items if x.get('steam_registered_installed') and x.get('type')=='video' and x.get('source_inside_item') and x.get('source_exists')]
    results={}
    for item in eligible:
        if args.limit and len(results)>=args.limit:
            break
        try:
            result=audit(item,args.output/item['id'],args)
            results[item['id']]=result['state']
        except Exception as error:
            save(args.output/item['id']/'failure.json',{'type':type(error).__name__})
            results[item['id']]='AUDIT_FAILED'
        save(args.output/'status.json',{'state':'RUNNING','eligible':len(eligible),'results':results,'at':time.time()})
        print(json.dumps({'id':item['id'],'state':results[item['id']],'processed':len(results)}),flush=True)
    save(args.output/'status.json',{'state':'BATCH_COMPLETE','eligible':len(eligible),'results':results,'at':time.time()})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workshop',type=Path,default=Path('D:/SteamLibrary/steamapps/workshop/content/431960'))
    p.add_argument('--limit',type=int,default=0)
    p.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe')
    p.add_argument('--ffprobe',default='D:/Apps/ffmpeg/ffprobe.exe')
    main(p.parse_args())
