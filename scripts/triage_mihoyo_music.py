"""Source-bound, sampled audio triage. Model labels never imply human listening."""
import argparse
import collections
import csv
import hashlib
import html
import json
from pathlib import Path
import subprocess

MODEL = 'laion/clap-htsat-unfused'
PROMPTS = {
    'calm': ['Quiet gentle relaxing instrumental background music.', 'Soft peaceful ambient music with a sparse arrangement.', 'A calm mellow acoustic melody for a peaceful landscape.'],
    'cheerful': ['Light cheerful playful instrumental background music.', 'A pleasant upbeat tune with gentle rhythm.', 'Warm happy casual daily life music.'],
    'intense': ['Intense aggressive fast battle music with heavy drums.', 'Loud epic boss fight music with dense percussion.', 'Dramatic action music with forceful driving rhythm.'],
    'tense': ['Dark tense ominous suspenseful music.', 'Unsettling music expressing anxiety and danger.', 'Dramatic threatening orchestral music.'],
    'vocal': ['A song with prominent singing vocals.', 'A singer performing lyrics over instrumental music.', 'Music with a loud choir and lead vocals.'],
}
LABELS = {'calm':'舒缓候选', 'cheerful':'轻快候选', 'intense':'激烈候选/暂不入视频', 'tense':'紧张候选/暂不入视频', 'vocal':'人声显著候选/待复核'}
ALBUM_CONTEXT = [
    ('Genshin','智妙明论之林','须弥','https://genshin.hoyoverse.com/vi/news/detail/24746','官方专辑地区'),
    ('Genshin','皎月云间之梦','璃月','https://www.youtube.com/watch?v=t1O7LpOTBfM','官方专辑地区'),
    ('Genshin','寂远无妄之国','稻妻','https://genshin.hoyoverse.com/en/news/detail/16077','官方专辑地区'),
    ('Genshin','白露澈明之泉','枫丹','https://www.youtube.com/watch?v=znHhRYBmfck','官方专辑地区'),
    ('Genshin','风与牧歌之城','蒙德','https://genshin-impact.fandom.com/wiki/City_of_Winds_and_Idylls','社区专辑资料/待逐曲核对'),
    ('StarRail','雪融于烬','雅利洛-VI','https://www.siliconera.com/HONKAI-STAR-RAIL-JARILO-VI-SOUNDTRACK-AND-BEHIND-THE-SCENES-VIDEO-SHARED/','专辑地区报道/待逐曲核对'),
    ('StarRail','失控 Out of Control','空间站黑塔','https://esports.gg/news/honkai-star-rail/phonograph-records-in-honkai-star-rail-how-to-unlock-them-all/','专辑地区资料/待逐曲核对'),
]
LOCATIONS = [
    ('Genshin','蒙德','龙脊雪山',['dragonspine','龙脊雪山']),
    ('Genshin','璃月','轻策庄',['qingce','轻策']),
    ('Genshin','璃月','沉玉谷',['chenyu','沉玉']),
    ('Genshin','璃月','层岩巨渊',['chasm','层岩']),
    ('Genshin','稻妻','渊下宫',['enkanomiya','渊下宫']),
    ('Genshin','限时区域','希穆兰卡',['simulanka','希穆兰卡']),
    ('Genshin','限时区域','琉形蜃境',['veluriyam','琉形蜃境','银瓶庭院']),
    ('Genshin','蒙德','',['mondstadt','蒙德','windrise','风起地']),
    ('Genshin','璃月','',['liyue','璃月','wangshu','望舒']),
    ('Genshin','稻妻','',['inazuma','稻妻','narukami','鸣神','watatsumi','海祇']),
    ('Genshin','须弥','',['sumeru','须弥','port ormos','奥摩斯','vanarana','桓那兰那']),
    ('Genshin','枫丹','',['fontaine','枫丹']),
    ('Genshin','纳塔','',['natlan','纳塔']),
    ('StarRail','空间站黑塔','',['herta','黑塔','space station','空间站']),
    ('StarRail','雅利洛-VI','',['jarilo','雅利洛','belobog','贝洛伯格']),
    ('StarRail','仙舟罗浮','',['luofu','罗浮','xianzhou','仙舟']),
    ('StarRail','匹诺康尼','',['penacony','匹诺康尼','golden hour','黄金的时刻']),
    ('StarRail','翁法罗斯','',['amphoreus','翁法罗斯']),
    ('ZZZ','新艾利都','六分街',['sixth street','六分街']),
    ('ZZZ','新艾利都','光映广场',['lumina square','光映广场']),
    ('ZZZ','新艾利都','芭莱大厦',['ballet twins','芭莱大厦']),
    ('ZZZ','新艾利都','野火镇',['blazewood','野火镇']),
    ('ZZZ','新艾利都','厄匹斯港',['port elpis','厄匹斯港']),
    ('ZZZ','新艾利都','光映海湾',['光映海湾']),
    ('ZZZ','新艾利都','随便观',['随便观']),
    ('ZZZ','新艾利都','澄辉坪',['澄辉坪']),
    ('ZZZ','新艾利都','莱姆尼安',['莱姆尼安']),
]

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)

def locations(game,text):
    found=[]
    for g,region,place,aliases in LOCATIONS:
        if g==game and any(a.casefold() in text.casefold() for a in aliases):
            found.append(dict(region=region,place=place))
    return found

def album_context(game, album):
    return [dict(region=region,place='',source=url,basis=basis) for g,name,region,url,basis in ALBUM_CONTEXT if g==game and name in album]

def decide(scores):
    winners=[max(s,key=s.get) for s in scores]
    means={k:sum(s[k] for s in scores)/len(scores) for k in PROMPTS}
    ordered=sorted(means,key=means.get,reverse=True)
    margin=means[ordered[0]]-means[ordered[1]]
    if len(set(winners))>1:return '混合/待复核',margin
    return (LABELS[ordered[0]] if margin>=.015 else '边界不清/待复核'),margin

def assets(root):
    result=[]
    research=root/'.kwjd/genshin-hsr-background-research'
    for d in read(research/'selected-metadata.json'):
        item=str(d['publishedfileid'])
        game='StarRail' if '星' in d.get('game','') else 'Genshin'
        reviewed=any((research/sub/item/'visual-review.json').exists() for sub in ['review','extracted-review'])
        status='已看抽帧/待处理' if reviewed else '已安装/待审看'
        extracted=research/'extracted'/item/'extraction-receipt.json'
        if extracted.exists():
            state=read(extracted).get('state','')
            if state=='STATIC_OR_ENGINE_ONLY_NO_EXTRACTED_VIDEO':status='仅静态或引擎场景/无原视频'
            elif 'UNSUPPORTED' in state:status='源类型待核'
        if item in ['3539590179','3172124476']:status='仅预设/依赖待核'
        result.append(dict(id=item,game=game,title=d['title'],location=locations(game,d['title']),status=status,file=''))
    for batch in (research/'clean1080').glob('batch*'):
        if not (batch/'completion.json').exists() or not (batch/'plan.json').exists():continue
        for item in read(batch/'plan.json')['items']:
            for a in result:
                if a['id']==str(item['id']) and Path(item['destination']).exists():
                    a['status']='可用成片（有界验收）';a['file']=item['destination']
    for f in (root/'background/ZZZ-1080p60-clean').glob('*.mp4'):
        result.append(dict(id='ZZZ:'+f.stem,game='ZZZ',title=f.stem,location=locations('ZZZ',f.stem),status='可用成片（有界验收）',file=str(f)))
    return result

def build(root,evidence):
    a=assets(root);rows=[]
    for g in read(root/'.kwjd/mihoyo-music/catalog/download-plan.json')['groups']:
        key=hashlib.sha256(json.dumps(g,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
        rec=read(root/'.kwjd/mihoyo-music/download/receipts'/(key+'.json'))
        loc=locations(g['game'],g['title']);context=album_context(g['game'],g['album'])
        matching_location=loc or context
        matches=[x for x in a if x['game']==g['game'] and matching_location and any(m['region']==n['region'] and (not m['place'] or not n['place'] or m['place']==n['place']) for m in matching_location for n in x['location'])]
        cache=evidence/'audio-reviews'/(rec['sha256']+'.json');review=read(cache) if cache.exists() else None
        rows.append(dict(key=key,game=g['game'],title=g['title'],album=g['album'],file=rec['file'],sha256=rec['sha256'],duration=rec['duration_seconds'],mood=review['category'] if review else '待音频初筛',audio_review=review,location=loc,album_context=context,location_basis='曲名明确地名，仅元数据线索，非游戏播放位置确证' if loc else '专辑地区背景，具体播放位置待核' if context else '待逐曲核对',assets=[dict(id=x['id'],status=x['status'],file=x['file'],title=x['title']) for x in matches],human_listened=False))
    atomic(evidence/'catalog.json',dict(tracks=rows,assets=a,note='模型分布抽样初筛，非人工试听；素材匹配仅地区线索，不保证构图适合。'))
    return rows,a

def export(root,evidence,rows,a):
    headings=['游戏','曲名','情绪初筛','曲名地区线索','专辑地区背景','素材匹配','素材明细','专辑','文件']
    records=[]
    for r in rows:
        ready=sum(x['status']=='可用成片（有界验收）' for x in r['assets'])
        loc='、'.join(x['region']+('/'+x['place'] if x['place'] else '') for x in r['location']) or '待核对'
        context='、'.join(x['region']+'（'+x['basis']+'）' for x in r['album_context']) or '待核对'
        detail='；'.join(x['title']+' ['+x['status']+'] '+x['file'] for x in r['assets']) or '尚未匹配到；不代表游戏内不存在'
        records.append([r['game'],r['title'],r['mood'],loc,context,f'{ready}份可用 / {len(r["assets"])}份相关候选',detail,r['album'],r['file']])
    with (evidence/'曲目分类与素材.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(headings);w.writerows(records)
    page='<meta charset="utf-8"><title>音乐与风景素材对照</title><style>body{font:15px system-ui;margin:24px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}input{padding:8px;width:60%}</style><h1>音乐与风景素材对照</h1><p>模型抽样初筛，不等于人工试听；地区仅明确地名线索，待逐曲核对。舒缓/轻快候选优先，激烈/紧张候选暂不入视频。</p><input id="q" placeholder="搜索游戏、曲名、地区、情绪"><table><thead><tr>'
    page+=''.join('<th>'+html.escape(h)+'</th>' for h in headings)+'</tr></thead><tbody>'
    for row in records:
        page+='<tr>'+''.join('<td>'+('<details><summary>展开素材</summary>'+html.escape(str(v))+'</details>' if i==6 else html.escape(str(v)))+'</td>' for i,v in enumerate(row))+'</tr>'
    page+='</tbody></table><script>document.getElementById("q").oninput=e=>document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase()))</script>'
    target=root/'music/miHoYo/音乐与风景素材对照.html';target.write_text(page,encoding='utf8')
    atomic(evidence/'summary.json',dict(total=len(rows),moods=dict(collections.Counter(r['mood'] for r in rows)),assets=len(a),output=str(target)))

def classify(args):
    import numpy as np
    import torch
    from transformers import ClapModel,ClapProcessor
    torch.set_num_threads(4)
    processor=ClapProcessor.from_pretrained(args.model,revision=args.revision)
    model=ClapModel.from_pretrained(args.model,revision=args.revision).eval()
    texts=[p for prompts in PROMPTS.values() for p in prompts]
    with torch.inference_mode():
        text=model.get_text_features(**processor(text=texts,padding=True,return_tensors='pt'));text=text/text.norm(dim=-1,keepdim=True)
    rows,a=build(args.root,args.evidence);done=0;errors=0
    for row in rows:
        dest=args.evidence/'audio-reviews'/(row['sha256']+'.json')
        if dest.exists():continue
        duration=row['duration'];starts=sorted(set(round(max(0,(duration-10)*p),3) for p in [.1,.5,.85]));clips=[]
        try:
            before=Path(row['file']).stat()
            with Path(row['file']).open('rb') as source:
                digest=hashlib.file_digest(source,'sha256').hexdigest()
            if digest!=row['sha256']:raise ValueError('Source hash mismatch')
            for start in starts:
                raw=subprocess.check_output([args.ffmpeg,'-v','error','-ss',str(start),'-i',row['file'],'-t','10','-vn','-ac','1','-ar','48000','-f','f32le','-'],timeout=40)
                clips.append(np.frombuffer(raw,dtype=np.float32).copy())
            with torch.inference_mode():
                audio=model.get_audio_features(**processor(audios=clips,sampling_rate=48000,return_tensors='pt',padding=True));audio=audio/audio.norm(dim=-1,keepdim=True);sims=(audio@text.T).tolist()
            scores=[{k:sum(v[i*3:i*3+3])/3 for i,k in enumerate(PROMPTS)} for v in sims];category,margin=decide(scores)
            after=Path(row['file']).stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed')
            atomic(dest,dict(model=MODEL,revision=getattr(model.config,'_commit_hash',args.revision),source_sha256=row['sha256'],windows=[dict(start=t,seconds=min(10,duration-t)) for t in starts],category=category,cosine_scores=scores,margin=margin,human_listened=False,scope='Three distributed excerpts; no full-track listening guarantee'))
        except Exception as error:
            errors+=1
            atomic(args.evidence/'errors'/(row['key']+'.json'),dict(type=type(error).__name__))
        done+=1;atomic(args.evidence/'status.json',dict(state='RUNNING',processed_this_run=done,errors=errors,current_key=row['key']))
        if done%25==0:
            rows,a=build(args.root,args.evidence);export(args.root,args.evidence,rows,a)
        if args.limit and done>=args.limit:break
    rows,a=build(args.root,args.evidence);export(args.root,args.evidence,rows,a)
    pending=sum(r['audio_review'] is None for r in rows)
    state='BOUNDED_RUN_COMPLETE' if args.limit else ('BATCH_COMPLETE' if not pending else 'INCOMPLETE')
    atomic(args.evidence/'status.json',dict(state=state,processed_this_run=done,errors=errors,pending=pending))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('D:/qingqingwu'))
    p.add_argument('--evidence',type=Path,default=Path('D:/qingqingwu/.kwjd/mihoyo-music/selection'))
    p.add_argument('--classify',action='store_true');p.add_argument('--limit',type=int,default=0)
    p.add_argument('--revision',default='main')
    p.add_argument('--model',default=MODEL)
    p.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe');args=p.parse_args()
    if args.classify:classify(args)
    else:rows,a=build(args.root,args.evidence);export(args.root,args.evidence,rows,a)
