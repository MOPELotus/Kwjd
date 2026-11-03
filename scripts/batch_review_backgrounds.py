"""Prepare multi-video sampled review pages and summarize explicit visual decisions."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)


def prepare(root, output):
    from PIL import Image, ImageDraw
    pending=[p for p in sorted((root/'review').glob('*/technical.json'))
             if not (p.parent/'visual-review.json').exists()]
    if (output/'manifest.json').exists():
        raise RuntimeError('Existing review manifest retained; use a new output directory')
    output.mkdir(parents=True,exist_ok=True)
    pages=[]
    for start in range(0,len(pending),6):
        sheet=Image.new('RGB',(1152,6*246),(20,20,20));draw=ImageDraw.Draw(sheet)
        rows=[]
        for row,p in enumerate(pending[start:start+6]):
            d=read(p);item=p.parent.name
            draw.text((5,row*246+5),f'{start+row+1:03d}  ID {item}',fill='white')
            frames=['0-0.jpg','1-1.jpg','2-2.jpg']
            for col,name in enumerate(frames):
                with Image.open(p.parent/name) as image:
                    sheet.paste(image.resize((384,216)),(col*384,row*246+26))
            rows.append(dict(number=start+row+1,id=item,title=d.get('title',''),
                             source_sha256=d['sha256'],frames=frames))
        page=output/f'page-{len(pages)+1:03d}.jpg';sheet.save(page,quality=95)
        pages.append(dict(page=str(page),sha256=hashlib.sha256(page.read_bytes()).hexdigest(),rows=rows))
    atomic(output/'manifest.json',dict(pages=pages,count=len(pending),
        scope='Three ordered existing frames per video: first, middle, last. 384px samples. No native-edge, whole-video or loop approval.'))
    print(json.dumps(dict(pages=len(pages),videos=len(pending))))


def record(root, output, decisions):
    manifest=read(output/'manifest.json')
    lookup={row['number']:(page,row) for page in manifest['pages'] for row in page['rows']}
    allowed={'CLEAN_CANDIDATE','NEEDS_CROP','EXCLUDE','REVIEW_REQUIRED'}
    for item in decisions:
        number,category,flags,note=item
        if category not in allowed:raise ValueError('Invalid category')
        page,row=lookup[number]
        if hashlib.sha256(Path(page['page']).read_bytes()).hexdigest()!=page['sha256']:
            raise ValueError('Review page changed')
        source=read(root/'review'/row['id']/'technical.json')
        if source['sha256']!=row['source_sha256']:raise ValueError('Source receipt changed')
        atomic(output/'decisions'/(row['id']+'.json'),dict(**row,category=category,
               flags=flags,findings=note,page=page['page'],page_sha256=page['sha256'],
               actually_viewed_frames=row['frames'],scope=manifest['scope']))


def report(root, output):
    decisions={p.stem:read(p) for p in (output/'decisions').glob('*.json')}
    rows=[]
    for sub in ['review','extracted-review']:
        for p in sorted((root/sub).glob('*/technical.json')):
            tech=read(p);item=p.parent.name;old=p.parent/'visual-review.json'
            if item in decisions:
                d=decisions[item];category=d['category'];note=d['findings'];flags=d['flags']
            elif old.exists():
                d=read(old);state=d.get('state','');note=d.get('findings','');flags=[]
                if state=='SCENERY_CANDIDATE':category='PRIOR_SCENERY_REVIEW'
                else:category='PRIOR_BACKUP_OR_REVIEW'
            else:category='PENDING';note='';flags=[]
            rows.append(dict(id=item,title=tech.get('title',''),category=category,
                             flags=flags,findings=note,source_sha256=tech['sha256'],
                             width=tech['video']['width'],height=tech['video']['height']))
    groups=collections.defaultdict(list)
    for row in rows:groups[row['source_sha256']].append(row['id'])
    summary=dict(videos=len(rows),categories=dict(collections.Counter(r['category'] for r in rows)),
                 flags=dict(collections.Counter(f for r in rows for f in r['flags'])),
                 byte_identical_groups=[v for v in groups.values() if len(v)>1],
                 unique_sources=len(groups),
                 scope='Sampled picture triage only; clean candidates may still require format normalization and output checks. Prior reviews retain their original scope.')
    atomic(output/'summary.json',summary);atomic(output/'inventory.json',dict(summary=summary,items=rows))
    with (output/'review-results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    print(json.dumps(summary,ensure_ascii=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('D:/qingqingwu/.kwjd/genshin-hsr-background-research'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--decisions',type=Path)
    args=parser.parse_args()
    if args.prepare:prepare(args.root,args.output)
    elif args.decisions:record(args.root,args.output,read(args.decisions))
    else:report(args.root,args.output)
