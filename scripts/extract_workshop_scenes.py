"""Extract installed scene assets to separate evidence directories, preserving Steam sources."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)


def main(args):
    inventory=json.loads(args.inventory.read_text(encoding='utf-8-sig'))['items']
    items=[x for x in inventory if x.get('steam_registered_installed') and x.get('type')=='scene']
    results={}
    for item in items:
        if args.limit and len(results)>=args.limit:
            break
        root=(args.workshop/item['id']).resolve()
        source=Path(item['source']).resolve()
        # project.json names scene.json inside the package, not the package itself.
        if source.suffix.lower()!='.pkg' and (root/'scene.pkg').is_file():
            source=(root/'scene.pkg').resolve()
        if not source.is_relative_to(root) or source.suffix.lower()!='.pkg' or not source.is_file():
            results[item['id']]='UNSUPPORTED_SCENE_SOURCE'
            continue
        dest=args.output/item['id'];receipt=dest/'extraction-receipt.json';info=source.stat()
        if receipt.exists():
            old=json.loads(receipt.read_text(encoding='utf8'))
            if old['source_bytes']==info.st_size and old['source_mtime_ns']==info.st_mtime_ns:
                results[item['id']]=old['state']
                continue
        if shutil.disk_usage(args.output).free<40*1024**3:
            raise RuntimeError('40 GiB free reserve reached; no source deletion')
        dest.mkdir(parents=True,exist_ok=True)
        with (dest/'extract.stdout.log').open('wb') as stdout, (dest/'extract.stderr.log').open('wb') as stderr:
            result=subprocess.run([str(args.repkg),'extract','-o',str(dest),str(source)],stdout=stdout,stderr=stderr,timeout=600)
        if result.returncode:
            results[item['id']]='EXTRACTION_FAILED'
        else:
            after=source.stat()
            if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise RuntimeError('Scene changed while extracting')
            videos=[str(p) for p in dest.rglob('*') if p.is_file() and p.suffix.lower() in ('.mp4','.webm','.mkv','.avi','.mov')]
            images=[str(p) for p in dest.rglob('*') if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp')]
            state='EXTRACTED_VIDEO_TECHNICAL_PENDING' if videos else 'STATIC_OR_ENGINE_ONLY_NO_EXTRACTED_VIDEO'
            with source.open('rb') as file:
                digest=hashlib.file_digest(file,'sha256').hexdigest()
            save(receipt,{'id':item['id'],'state':state,'source':str(source),'source_bytes':info.st_size,
                          'source_mtime_ns':info.st_mtime_ns,'source_sha256':digest,'videos':videos,'images':images,
                          'scope':'Raw assets only; no scene-render equivalence, video decode, visual or loop approval'})
            results[item['id']]=state
        save(args.output/'status.json',{'state':'RUNNING','results':results,'at':time.time()})
        print(json.dumps({'id':item['id'],'state':results[item['id']]}),flush=True)
    save(args.output/'status.json',{'state':'BATCH_COMPLETE','results':results,'at':time.time()})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repkg',type=Path,required=True)
    p.add_argument('--workshop',type=Path,default=Path('D:/SteamLibrary/steamapps/workshop/content/431960'))
    p.add_argument('--limit',type=int,default=0)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True);main(args)
