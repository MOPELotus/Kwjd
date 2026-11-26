"""Retire authorized redundant raster payloads from completed legacy reviews.

Indexes, decisions, audits, final subtitles and explicitly referenced pictures
remain. A hash inventory records removed bytes without claiming they still exist.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

EPISODES={'S03E02','S03E03','S03E04','S03E05'}
BULK={'gap-allframes-wide-v1','short-allframes-wide-v1','fullframe-position-1s-v1'}
RASTERS={'.jpg','.jpeg','.png'}


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def image_references(value):
    if isinstance(value,str) and Path(value).suffix.lower() in RASTERS:
        yield value
    elif isinstance(value,dict):
        for item in value.values():yield from image_references(item)
    elif isinstance(value,list):
        for item in value:yield from image_references(item)


def plan(root,episode):
    if episode not in EPISODES:raise ValueError('Only the four sealed legacy reviews are authorized')
    from subtitle_review_supervisor import validate_episode
    root=Path(root);review=root/'ocr/subtitle-review'
    if not validate_episode(root,episode):raise ValueError('Genuine existing sealed review required')
    report=json.loads((review/'reports'/(episode+'.json')).read_text())
    if report.get('review_policy') is not None:raise ValueError('Focused review payloads are not cleanup targets')
    protected={Path(x['path']).resolve() for x in report['evidence']}
    for directory in [review/'decisions'/episode,review/'audits'/episode]:
        for path in directory.glob('*.json'):
            # Whole-episode coverage ledgers document the very payloads being retired.
            if 'review-progress' in path.name or path.name.startswith('final-'):continue
            data=json.loads(path.read_text())
            for ref in image_references(data):
                if Path(ref).is_absolute():protected.add(Path(ref).resolve())
    candidates=[]
    parent=review/'evidence'/episode
    for directory in parent.iterdir():
        if not directory.is_dir() or directory.is_symlink():continue
        if directory.name not in BULK and not directory.name.startswith('scan-'):continue
        for path in directory.rglob('*'):
            if path.is_symlink():raise ValueError('Unexpected symlink in cleanup target')
            if path.is_file() and path.suffix.lower() in RASTERS and path.resolve() not in protected:
                if not path.resolve().is_relative_to(parent.resolve()):raise ValueError('Cleanup escaped episode')
                candidates.append(path)
    return report,sorted(candidates),protected


def verify_retirement(review,episode,report):
    pointer=report.get('evidence_payload_retirement')
    if pointer is None:return True
    path=Path(pointer['path']);review=Path(review).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(review/'retirements') or sha(path)!=pointer['sha256']:return False
    ledger=json.loads(path.read_text())
    if (ledger.get('state')!='COMPLETE' or ledger.get('episode')!=episode
        or ledger.get('user_authorized') is not True
        or any(ledger.get(k)!=report.get(k) for k in ['source_sha256','draft_sha256','final_srt_sha256'])):return False
    for key in ['inventory','original_report']:
        item=ledger[key];target=Path(item['path'])
        if target.is_symlink() or not target.resolve().is_relative_to(review/'retirements') or sha(target)!=item['sha256']:return False
    original=json.loads(Path(ledger['original_report']['path']).read_text())
    current={k:v for k,v in report.items() if k!='evidence_payload_retirement'}
    return original==current


def retire(root,episode,execute=False):
    root=Path(root);review=root/'ocr/subtitle-review';report_path=review/'reports'/(episode+'.json')
    current=json.loads(report_path.read_text())
    if current.get('evidence_payload_retirement'):
        if not verify_retirement(review,episode,current):raise ValueError('Existing retirement differs')
        return json.loads(Path(current['evidence_payload_retirement']['path']).read_text())
    report,paths,protected=plan(root,episode)
    total=sum(p.stat().st_size for p in paths)
    if not execute:return dict(episode=episode,state='PLAN_ONLY',files=len(paths),bytes=total)
    folder=review/'retirements'/episode;folder.mkdir(parents=True,exist_ok=True)
    original=folder/'original-report.json'
    if original.exists() and original.read_bytes()!=report_path.read_bytes():raise ValueError('Original report archive differs')
    if not original.exists():original.write_bytes(report_path.read_bytes())
    inventory=folder/'retired-raster-payloads.jsonl'
    if inventory.exists():raise ValueError('Partial retirement inventory retained; inspect before resuming')
    with inventory.open('x') as out:
        for path in paths:
            stat=path.stat();digest=sha(path)
            out.write(json.dumps(dict(path=str(path),bytes=stat.st_size,sha256=digest,mtime_ns=stat.st_mtime_ns,inode=stat.st_ino,device=stat.st_dev))+'\n')
        out.flush();os.fsync(out.fileno())
    # Durable inventory first; verify identity immediately before each unlink.
    removed=0
    for line in inventory.read_text().splitlines():
        row=json.loads(line);path=Path(row['path']);stat=path.stat()
        if (stat.st_size!=row['bytes'] or stat.st_mtime_ns!=row['mtime_ns']
            or stat.st_ino!=row['inode'] or stat.st_dev!=row['device'] or path.is_symlink()):
            raise ValueError('Raster changed; preserve remaining payload')
        path.unlink();removed+=row['bytes']
    ledger=dict(schema_version=1,episode=episode,state='COMPLETE',at=time.time(),user_authorized=True,
        authorization_quote='那就全都清理掉呗 没用的',authorization_date='2026-11-26',
        source_sha256=report['source_sha256'],draft_sha256=report['draft_sha256'],final_srt_sha256=report['final_srt_sha256'],
        inventory={'path':str(inventory),'sha256':sha(inventory)},original_report={'path':str(original),'sha256':sha(original)},
        files_removed=len(paths),bytes_removed=removed,scope='Retired redundant legacy bulk/scan raster payloads only; historical audit completion remains historical, raw payload availability explicitly retired',
        retained='All indexes, decisions, audit records, explicitly referenced report/decision/doubt pictures, final SRT/ASS/seals; current focused evidence/models/media untouched')
    target=folder/'retirement.json';save(target,ledger)
    report['evidence_payload_retirement']={'path':str(target),'sha256':sha(target)};save(report_path,report)
    from subtitle_review_supervisor import validate_episode
    if not validate_episode(root,episode):raise ValueError('Retired review validation failed')
    return ledger


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--episode',required=True);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    print(json.dumps(retire(args.root,args.episode,args.execute),ensure_ascii=False))
