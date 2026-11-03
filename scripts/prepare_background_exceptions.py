"""Extract source-bound native frames for exceptional backgrounds, without encoding video."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from PIL import Image, ImageDraw


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');temp.replace(path)


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def review_pages(output):
    """Full sampled framing plus unscaled left/right edge pixels, six videos/page."""
    target=output/'review-pages';target.mkdir(exist_ok=True)
    if (target/'manifest.json').exists():raise RuntimeError('Existing review pages retained')
    sources=sorted(output.glob('*/receipt.json'));pages=[]
    for start in range(0,len(sources),6):
        sheet=Image.new('RGB',(1920,6*578),(18,18,18));draw=ImageDraw.Draw(sheet);rows=[]
        for n,path in enumerate(sources[start:start+6]):
            receipt=read(path);y=n*578
            draw.text((5,y+3),path.parent.name+'   full frame samples / native left and right edge pixels',fill='white')
            for col,frame in enumerate(receipt['frames']):
                native=Path(frame['file'])
                if sha(native)!=frame['sha256']:raise ValueError('Native frame SHA changed')
                with Image.open(native) as image:
                    thumb=image.copy();thumb.thumbnail((640,260))
                    sheet.paste(thumb,(col*640,y+24))
                    # Both sides of each top/bottom strip, at original pixel scale.
                    for j,(left,top) in enumerate([(0,0),(image.width-320,0),(0,image.height-128),(image.width-320,image.height-128)]):
                        sheet.paste(image.crop((left,top,left+320,top+128)),(col*640+(j%2)*320,y+294+(j//2)*128))
            rows.append(dict(id=path.parent.name,source_sha256=receipt['source_sha256'],frames=receipt['frames']))
        page=target/f'page-{len(pages)+1:02d}.png';sheet.save(page)
        pages.append(dict(file=str(page),sha256=sha(page),rows=rows))
    atomic(target/'manifest.json',dict(pages=pages,count=len(sources),scope='Three full-frame thumbnails and native corner strips per source; preparation alone is not visual approval.'))
    print(json.dumps(dict(pages=len(pages),videos=len(sources))))


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    lock=args.output/'worker.lock'
    with lock.open('x') as stream:stream.write(str(__import__('os').getpid()))
    completed=errors=0
    try:
        rows=[x for x in read(args.inventory)['items'] if x['action'] in
              ['PICTURE_FIX_REQUIRED','NONSTANDARD_SIZE_OR_SAR','REVIEW_REQUIRED']]
        if args.limit:rows=rows[:args.limit]
        for row in rows:
            directory=args.output/row['id'];directory.mkdir(exist_ok=True)
            receipt=directory/'receipt.json'
            if receipt.exists() and read(receipt).get('source_sha256')==row['source_sha256']:
                old=read(receipt)
                if all(Path(x['file']).exists() and sha(Path(x['file']))==x['sha256'] for x in old['frames']):
                    completed+=1;continue
            try:
                if shutil.disk_usage(args.output).free<40*1024**3:
                    raise RuntimeError('Less than 40 GiB free')
                source=Path(row['file']);before=source.stat()
                if sha(source)!=row['source_sha256']:raise ValueError('Source SHA mismatch')
                technical=args.research/'review'/row['id']/'technical.json'
                if not technical.exists():technical=args.research/'extracted-review'/row['id']/'technical.json'
                duration=read(technical)['duration_seconds']
                times=sorted(set([min(1,duration/4),duration/2,max(0,duration-1)]))
                frames=[];width=row['width'];strip=128
                sheet=Image.new('RGB',(width,len(times)*(strip*2+24)),(15,15,15));draw=ImageDraw.Draw(sheet)
                for index,start in enumerate(times):
                    target=directory/f'native-{index}.png'
                    subprocess.run([args.ffmpeg,'-v','error','-y','-ss',str(start),'-i',str(source),
                        '-map','0:v:0','-frames:v','1',str(target)],check=True,capture_output=True,timeout=90)
                    with Image.open(target) as image:
                        if image.size!=(row['width'],row['height']):raise ValueError('Native dimensions changed')
                        y=index*(strip*2+24)
                        draw.text((5,y+3),f'{row["id"]} {start:.3f}s top / bottom, native pixels',fill='white')
                        sheet.paste(image.crop((0,0,width,strip)),(0,y+24))
                        sheet.paste(image.crop((0,image.height-strip,width,image.height)),(0,y+24+strip))
                    frames.append(dict(file=str(target),sha256=sha(target),start=start))
                after=source.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed')
                edge=directory/'native-edges.png';sheet.save(edge)
                atomic(receipt,dict(state='NATIVE_FRAMES_READY_VISUAL_PENDING',source_sha256=row['source_sha256'],
                    frames=frames,native_edge_sheet=str(edge),edge_sha256=sha(edge),
                    scope='Three native frames and top/bottom strips prepared, not actually visually reviewed by this script. No source changes or video encoding.'))
                completed+=1
            except Exception as error:
                errors+=1;atomic(directory/'error.json',dict(type=type(error).__name__))
            atomic(args.output/'status.json',dict(state='RUNNING',completed=completed,errors=errors,total=len(rows),at=time.time()))
        atomic(args.output/'status.json',dict(state='BOUNDED_COMPLETE' if args.limit else 'PREPARATION_COMPLETE' if not errors else 'INCOMPLETE',completed=completed,errors=errors,total=len(rows)))
    finally:lock.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research',type=Path,default=Path('D:/qingqingwu/.kwjd/genshin-hsr-background-research'))
    parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe')
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--review-pages',action='store_true')
    args=parser.parse_args()
    if args.review_pages:review_pages(args.output)
    else:run(args)
