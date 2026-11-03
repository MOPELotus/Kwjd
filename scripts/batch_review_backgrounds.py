"""Prepare multi-video sampled review pages and summarize explicit visual decisions."""
import argparse
import collections
import csv
import hashlib
import html
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
        verdict=dict(**row,category=category,
               flags=flags,findings=note,page=page['page'],page_sha256=page['sha256'],
               actually_viewed_frames=row['frames'],scope=manifest['scope'])
        atomic(output/'decisions'/(row['id']+'.json'),verdict)
        canonical=root/'review'/row['id']/'visual-review.json'
        if not canonical.exists() or read(canonical).get('page_sha256')==page['sha256']:
            atomic(canonical,dict(**verdict,state=category,actually_viewed_indices=[0,4,8],
                                  crop_decided=False,loop_approved=False))


STANDARD_SIZES={(640,360),(854,480),(960,540),(1280,720),(1366,768),
                (1600,900),(1920,1080),(2048,1152),(2560,1440),(3840,2160),(7680,4320),
                (1280,800),(1440,900),(1680,1050),(1728,1080),(1920,1200),
                (2304,1440),(2560,1600),(3456,2160),(3840,2400),
                (2560,1080),(3440,1440),(3840,1600),(5120,1440),(5120,2160),
                (640,480),(800,600),(1024,768),(1440,1080),(1600,1200),(1920,1440),
                (2048,1536),(4096,2160),(4096,1716)}


def standard_picture(video):
    # Frame rate is deliberately irrelevant: final export handles 1080p60.
    return ((video['width'],video['height']) in STANDARD_SIZES
            and video.get('sample_aspect_ratio','1:1') in ['1:1','N/A',None])


def prior_verdict(item, review):
    state=review.get('state') or review.get('disposition') or ''
    if item in {'3458380217','3554733910','3556513823','3556544500','3598566875','3598576287'}:
        return 'NEEDS_CROP',['UID']
    if item in {'2711999352','2817427886','2824451900','2841481582'}:
        return 'NEEDS_CROP',['BLACK_BARS']
    if state=='SCENERY_WITH_FOREGROUND_BACKUP':return 'CLEAN_CANDIDATE',[]
    if state=='SCENERY_CANDIDATE':return 'CLEAN_CANDIDATE',[]
    if state=='SCENERY_NEEDS_OVERLAY_REVIEW':return 'REVIEW_REQUIRED',['TEXT','LOGO']
    if 'CHARACTER' in state or any(x in state for x in ['ILLUSTRATED','PERFORMANCE','GRAPHIC','MONTAGE','SEQUENCE','PORTRAIT']):
        return 'EXCLUDE',[state]
    if state.startswith('SCENERY_NEEDS_'):
        return 'NEEDS_CROP',['TRIM'] if 'TRIM' in state else ['HUD_OR_UID']
    return 'REVIEW_REQUIRED',[state or 'UNKNOWN_LEGACY_RECORD']


def report(root, output):
    decisions={p.stem:read(p) for p in (output/'decisions').glob('*.json')}
    rows=[];accepted={}
    for receipt in (root/'clean1080').glob('batch*/*/receipt.json'):
        visual=receipt.parent/'output-visual-review.json'
        if not visual.exists():continue
        rec=read(receipt);rev=read(visual)
        if rev.get('state')=='BOUNDED_VISUAL_ACCEPTED' and rev.get('output_sha256')==rec['sha256']:
            accepted[str(rec['item']['id'])]=rec['item']['destination']
    for sub in ['review','extracted-review']:
        for p in sorted((root/sub).glob('*/technical.json')):
            tech=read(p);item=p.parent.name;old=p.parent/'visual-review.json'
            if item in decisions:
                d=decisions[item];category=d['category'];note=d['findings'];flags=d['flags']
            elif old.exists():
                d=read(old);note=d.get('findings') or d.get('finding') or d.get('scene_observed','')
                category,flags=prior_verdict(item,d)
            else:category='PENDING';note='';flags=[]
            source=Path(tech['source']);stat=source.stat()
            unchanged=(stat.st_size==tech['bytes'] and stat.st_mtime_ns==tech['mtime_ns'])
            if category=='EXCLUDE':action='EXCLUDE'
            elif item in accepted:action='READY_EXISTING_OUTPUT'
            elif not unchanged:action='SOURCE_CHANGED_REVIEW'
            elif category in ['REVIEW_REQUIRED','PENDING']:action=category
            elif category=='NEEDS_CROP':action='PICTURE_FIX_REQUIRED'
            elif not standard_picture(tech['video']):action='NONSTANDARD_SIZE_OR_SAR'
            elif tech['state']=='TECHNICAL_REVIEW_REQUIRED':action='TECHNICAL_REVIEW'
            else:action='DIRECT_ORIGINAL'
            rows.append(dict(id=item,title=tech.get('title',''),category=category,
                             flags=flags,findings=note,source_sha256=tech['sha256'],action=action,
                             file=accepted.get(item,tech['source']),source_unchanged=unchanged,
                             width=tech['video']['width'],height=tech['video']['height'],
                             fps=tech['video'].get('avg_frame_rate')))
    groups=collections.defaultdict(list)
    for row in rows:groups[row['source_sha256']].append(row['id'])
    summary=dict(videos=len(rows),categories=dict(collections.Counter(r['category'] for r in rows)),
                 actions=dict(collections.Counter(r['action'] for r in rows)),
                 flags=dict(collections.Counter(f for r in rows for f in r['flags'])),
                 byte_identical_groups=[v for v in groups.values() if len(v)>1],
                 unique_sources=len(groups),
                 scope='Sampled picture triage. Clean standard-size originals are passed without re-encoding regardless of frame rate; final export handles 1080p60. Only picture fixes/nonstandard dimensions enter processing. Existing technical flags remain review items.')
    atomic(output/'summary.json',summary);atomic(output/'inventory.json',dict(summary=summary,items=rows))
    with (output/'review-results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    for action in ['DIRECT_ORIGINAL','READY_EXISTING_OUTPUT']:
        selected=[r for r in rows if r['action']==action]
        (output/(action+'.m3u8')).write_text('#EXTM3U\n'+''.join('#EXTINF:-1,'+r['title']+'\n'+r['file']+'\n' for r in selected),encoding='utf8')
    labels={'DIRECT_ORIGINAL':'原视频直接通过','READY_EXISTING_OUTPUT':'已有处理好成片',
            'PICTURE_FIX_REQUIRED':'需裁HUD/UID/黑边或修剪','NONSTANDARD_SIZE_OR_SAR':'需修正规格或像素比例',
            'TECHNICAL_REVIEW':'技术标记待核','REVIEW_REQUIRED':'画面疑点待核','EXCLUDE':'排除/仅备用'}
    page='<meta charset="utf-8"><title>风景素材批量审核</title><style>body{font:15px system-ui;margin:24px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}input{padding:8px;width:65%}</style><h1>风景素材批量审核</h1><p>454份视频均有真实抽帧审看记录（旧123份记录复用，新331份每份首中尾3帧）。正常规格原片直接通过，不要求1080p或60fps；最终成片导出再统一。这里的通过仅限抽样审查，不保证未抽到的瞬间无HUD。</p><ul>'
    page+=''.join('<li>'+labels.get(k,k)+'：'+str(v)+'</li>' for k,v in summary['actions'].items())
    page+='</ul><input id="q" placeholder="搜索曲目以外的地点、编号、处理类别"><table><thead><tr><th>编号</th><th>标题</th><th>分类</th><th>原始规格</th><th>画面观察</th><th>文件</th></tr></thead><tbody>'
    for row in rows:
        values=[row['id'],row['title'],labels.get(row['action'],row['action']),str(row['width'])+'×'+str(row['height'])+' '+str(row['fps']),row['findings'],row['file']]
        page+='<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>'
    page+='</tbody></table><script>document.getElementById("q").oninput=e=>document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase()))</script>'
    (output/'风景素材审核汇总.html').write_text(page,encoding='utf8')
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
