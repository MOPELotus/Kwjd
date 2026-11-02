"""Download authorized native audio with per-file technical receipts and checkpoints."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def candidate_score(data):
    lossless = data.get('format', '').lower() in ('flac','wav','alac','aiff')
    duration = data.get('duration_ms') or 1
    bitrate = data.get('bitrate') or (data.get('size') or 0)*8000/duration
    return (lossless, bitrate)


def native_candidate(data, expected_duration):
    if not data.get('available') or not data.get('url'):
        return False
    if data.get('format', '').lower() not in ('flac','wav','alac','aiff','mp3','m4a','ogg','opus','aac'):
        return False
    duration = data.get('duration_ms')
    trial = (data.get('extensions') or {}).get('trial')
    if trial is True:
        return False
    return not (duration and expected_duration and abs(duration-expected_duration)>2000)


class StopBatch(Exception):
    pass


def check_download_http_status(code):
    # A gateway outage is not evidence that the next songs are unavailable.
    if code in (401, 429) or 500 <= code <= 599:
        raise StopBatch(f'Download service HTTP {code}; preserve checkpoint for review')


def collect_candidates(args, group, blocked, fallback_map):
    candidates, attempts = [], []
    sources = list(group['sources'])
    for original in group['sources']:
        extra = fallback_map.get(original['ref'])
        if extra and not any(s['ref'] == extra['ref'] for s in sources):
            sources.append(extra)
    for source in sources:
        platform = source['ref'].split(':', 1)[0]
        if platform in blocked:
            attempts.append({'ref': source['ref'], 'skipped': blocked[platform]})
            continue
        for quality in ('master','hires','lossless'):
            query = urllib.parse.urlencode({'account':'personal','quality':quality,
                                           'fallback':'false','unblock':'false'})
            time.sleep(.5)
            try:
                with urllib.request.urlopen(args.base+'/v1/tracks/'+source['ref']+'/download?'+query,timeout=40) as response:
                    answer=json.load(response)
                data=answer.get('data') or {}
                attempts.append({'ref':source['ref'],'requested':quality,
                                 'actual':data.get('actual_quality'),'format':data.get('format'),
                                 'available':data.get('available')})
                if native_candidate(data,source.get('duration_ms')):
                    candidates.append((candidate_score(data),source,data))
            except urllib.error.HTTPError as error:
                attempts.append({'ref':source['ref'],'requested':quality,'http':error.code})
                if getattr(args, 'qq_fallback', False) and platform == 'qq' and (error.code == 401 or 500 <= error.code <= 599):
                    blocked['qq'] = f'HTTP {error.code}'
                    break
                check_download_http_status(error.code)
    return candidates, attempts, sources


def run(args):
    plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))['groups']
    status_path = args.evidence/'status.json'
    status = {'status':'RUNNING','total':len(plan),'verified':0,'failed':0,'deferred':0}
    network_errors = 0
    blocked = {}
    fallback_map = json.loads(args.fallback_map.read_text(encoding='utf-8-sig')) if getattr(args, 'fallback_map', None) else {}
    for index, group in enumerate(plan):
        key = hashlib.sha256(json.dumps(group,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
        receipt_path = args.evidence/'receipts'/(key+'.json')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding='utf8'))
            file = Path(receipt['file'])
            if receipt.get('status')=='VERIFIED' and file.exists() and file.stat().st_size==receipt['bytes']:
                status['verified'] += 1
                continue
        status.update(index=index,current_title=group['title'],current_key=key)
        atomic(status_path,status)
        if shutil.disk_usage(args.output).free < args.minimum_free_gib*1024**3:
            raise StopBatch('Insufficient disk reserve; sources retained')
        try:
            candidates, attempts, sources = collect_candidates(args, group, blocked, fallback_map)
            if not candidates:
                if 'qq' in blocked and any(s['ref'].startswith('qq:') for s in sources):
                    atomic(args.evidence/'deferred'/(key+'.json'),
                           {'key':key,'reason':'QQ unavailable; no verified alternate candidate', 'attempts':attempts})
                    status['deferred'] += 1
                    atomic(status_path,status)
                    continue
                raise ValueError('No authorized complete ordinary audio candidate')
            _,source,data=max(candidates,key=lambda x:x[0])
            extension=data['format'].lower()
            directory=args.output/group['game'];directory.mkdir(parents=True,exist_ok=True)
            destination=directory/(key+'.'+extension)
            partial=destination.with_suffix(destination.suffix+'.partial')
            # Signed URLs and request headers remain in memory, never in receipts/logs.
            with urllib.request.urlopen(urllib.request.Request(data['url'],headers=data.get('headers') or {}),timeout=60) as response, partial.open('wb') as output:
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>512*1024**2:
                        raise ValueError('Single-file size budget exceeded')
                    output.write(chunk)
            if data.get('size') and partial.stat().st_size!=data['size']:
                raise ValueError('Download size mismatch')
            probe=json.loads(subprocess.run([args.ffprobe,'-v','error','-show_format','-show_streams','-of','json',str(partial)],capture_output=True,check=True,timeout=30).stdout)
            duration=float(probe['format']['duration'])
            if source.get('duration_ms') and abs(duration-source['duration_ms']/1000)>2:
                raise ValueError('Decoded duration mismatch; no full-song claim')
            subprocess.run([args.ffmpeg,'-v','error','-xerror','-i',str(partial),'-f','null','-'],capture_output=True,check=True,timeout=120)
            with partial.open('rb') as file:
                digest=hashlib.file_digest(file,'sha256').hexdigest()
            partial.replace(destination)
            receipt={'status':'VERIFIED','group_key':key,'title':group['title'],'album':group['album'],
                     'source_ref':source['ref'],'candidate_sources':[s['ref'] for s in sources],
                     'match_basis':group['match_basis'],'requested_quality':data.get('requested_quality'),
                     'actual_quality':data.get('actual_quality'),'file':str(destination),'bytes':destination.stat().st_size,
                     'sha256':digest,'full_decode_ok':True,'duration_seconds':duration,
                     'streams':[{k:s.get(k) for k in ('codec_name','sample_rate','channels','bits_per_raw_sample')} for s in probe['streams']],
                     'attempts':attempts,'listened':False,'quality_note':'Highest ordinary authorized candidate ranked by lossless format and returned bitrate; platform effect labels do not prove master fidelity'}
            atomic(receipt_path,receipt)
            status['verified']+=1
            deferred_path = args.evidence/'deferred'/(key+'.json')
            if deferred_path.exists(): deferred_path.unlink()
            network_errors=0
        except StopBatch as error:
            status['status']='STOPPED_REQUIRES_REVIEW'
            status['stop_reason']=str(error)
            atomic(status_path,status)
            raise
        except Exception as error:
            # Only stable exception type; exceptions may embed signed media URLs.
            atomic(args.evidence/'failures'/(key+'.json'),{'key':key,'type':type(error).__name__})
            status['failed']+=1
            if isinstance(error,(urllib.error.URLError,TimeoutError)):
                network_errors+=1
                if network_errors>=3:
                    status['status']='STOPPED_NETWORK';atomic(status_path,status)
                    return
        atomic(status_path,status)
        print(json.dumps({k:status[k] for k in ('index','verified','failed','total')}),flush=True)
        if args.limit and status['verified']>=args.limit:
            status['status']='LIMIT_REACHED';atomic(status_path,status)
            return
    status['status']='COMPLETE_WITH_PENDING' if status['failed'] or status['deferred'] else 'COMPLETE'
    atomic(status_path,status)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--qq-fallback',action='store_true',help='Suspend failing QQ provider for this run and use reviewed NetEase candidates; retain unmatched groups as deferred')
    p.add_argument('--fallback-map',type=Path,help='Reviewed QQ ref to NetEase source overrides; original plan and receipt keys stay unchanged')
    p.add_argument('--base',default='http://127.0.0.1:7832')
    p.add_argument('--minimum-free-gib',type=int,default=40)
    p.add_argument('--limit',type=int,default=0)
    p.add_argument('--ffmpeg',default='D:/Apps/ffmpeg/ffmpeg.exe')
    p.add_argument('--ffprobe',default='D:/Apps/ffmpeg/ffprobe.exe')
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    try:run(args)
    except Exception as error:
        atomic(args.evidence/'batch-error.json',{'type':type(error).__name__})
        raise SystemExit(2)
