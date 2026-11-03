"""Local/LAN music review server. Streams catalogued audio and saves human labels."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

LABELS={'calm':'舒缓','cheerful':'轻快','intense':'激烈/紧张','vocal':'人声','other':'其他'}
MODEL_LABELS={'舒缓候选':'calm','轻快候选':'cheerful','激烈候选/暂不入视频':'intense',
              '紧张候选/暂不入视频':'intense','人声显著候选/待复核':'vocal'}
WEB=Path(__file__).resolve().parents[1]/'web'/'music-review'


class Conflict(Exception):pass


def byte_range(header,size):
    if not header:return 0,size-1,False
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
    if not match or not any(match.groups()):raise ValueError('Invalid range')
    first,last=match.groups()
    if first:
        start=int(first);end=min(int(last),size-1) if last else size-1
    else:
        length=int(last)
        if not length:raise ValueError('Empty suffix')
        start=max(0,size-length);end=size-1
    if start>=size or end<start:raise ValueError('Unsatisfiable range')
    return start,end,True


class Library:
    def __init__(self,root):
        self.root=Path(root).resolve();self.directory=self.root/'.kwjd/mihoyo-music/selection'
        self.catalog=self.directory/'catalog.json';self.state=self.directory/'human-reviews.json'
        self.journal=self.directory/'human-reviews.jsonl';self.lock=threading.RLock();self.stamp=None
        self.rows={};self.reviews={};self.reload()
        if self.state.exists():self.reviews=json.loads(self.state.read_text(encoding='utf-8-sig'))['reviews']
        # A durable journal can recover an interrupted snapshot replacement.
        if self.journal.exists():
            for line in self.journal.read_text(encoding='utf8').splitlines():
                try:entry=json.loads(line)
                except json.JSONDecodeError:continue
                old=self.reviews.get(entry['key'],{})
                if entry['revision']>old.get('revision',0):self.reviews[entry['key']]=entry

    def reload(self):
        with self.lock:
            stamp=self.catalog.stat().st_mtime_ns
            if stamp==self.stamp:return
            tracks=json.loads(self.catalog.read_text(encoding='utf-8-sig'))['tracks']
            self.rows={x['key']:x for x in tracks};self.stamp=stamp

    def track(self,key):
        self.reload()
        if key not in self.rows:raise KeyError(key)
        row=self.rows[key];saved=self.reviews.get(key)
        valid=saved and saved['source_sha256']==row['sha256']
        return dict(key=key,game=row['game'],title=row['title'],album=row['album'],duration=row['duration'],
                    source_sha256=row['sha256'],model_mood=row['mood'],model_label=MODEL_LABELS.get(row['mood']),
                    label=saved['label'] if valid else MODEL_LABELS.get(row['mood']),
                    reviewed=bool(valid and saved['label'] is not None),human_override=bool(valid),
                    revision=saved['revision'] if saved else 0,updated_at=saved.get('updated_at') if valid else None)

    def tracks(self):
        self.reload()
        with self.lock:return [self.track(k) for k in self.rows]

    def audio(self,key):
        self.reload();row=self.rows[key];path=Path(row['file']).resolve()
        if not path.is_relative_to(self.root/'music/miHoYo/library'):raise ValueError('Audio outside library')
        if path.suffix.lower() not in ['.flac','.ogg','.mp3','.m4a','.wav']:raise ValueError('Invalid audio type')
        return path

    def save(self,key,body):
        with self.lock:
            current=self.track(key)
            if body.get('source_sha256')!=current['source_sha256']:raise Conflict('音频版本已更新，请刷新后重试')
            if body.get('expected_revision')!=current['revision']:raise Conflict('这首歌已在其他页面修改，请刷新后重试')
            label=body.get('label')
            if label is not None and label not in LABELS:raise ValueError('Invalid label')
            if 'label' not in body:raise ValueError('Missing label')
            entry=dict(key=key,label=label,source_sha256=current['source_sha256'],revision=current['revision']+1,
                       updated_at=datetime.now(timezone.utc).isoformat(),origin='human_web_review')
            with self.journal.open('a',encoding='utf8') as stream:
                stream.write(json.dumps(entry,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
            self.reviews[key]=entry
            tmp=self.state.with_suffix('.json.tmp')
            with tmp.open('w',encoding='utf8') as stream:
                json.dump(dict(schema_version=1,reviews=self.reviews),stream,ensure_ascii=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            tmp.replace(self.state)
            return self.track(key)


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,library):
        self.library=library;super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    server_version='MusicReview/1.0'

    def log_message(self,format,*args):pass

    def allowed_host(self):
        host=self.headers.get('Host','')
        try:
            parsed=urlsplit('http://'+host)
            if parsed.port!=self.server.server_port:return False
            if parsed.hostname=='localhost':return True
            ip=ipaddress.ip_address(parsed.hostname)
            return ip.is_loopback or ip.is_private
        except (ValueError,TypeError):return False

    def headers_for(self,status,kind,length,extra=None):
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(length))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers()

    def json(self,status,body):
        data=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf8')
        self.headers_for(status,'application/json; charset=utf-8',len(data))
        if self.command!='HEAD':self.wfile.write(data)

    def do_HEAD(self):self.do_GET()

    def do_GET(self):
        if not self.allowed_host():return self.json(403,dict(error='Host not allowed'))
        path=urlsplit(self.path).path
        try:
            if path=='/api/tracks':return self.json(200,dict(tracks=self.server.library.tracks(),labels=LABELS))
            if path=='/api/health':return self.json(200,dict(ok=True,tracks=len(self.server.library.rows)))
            if path.startswith('/audio/'):
                key=path.removeprefix('/audio/');file=self.server.library.audio(key);size=file.stat().st_size
                try:start,end,ranged=byte_range(self.headers.get('Range'),size)
                except ValueError:return self.json_range_error(size)
                extra={'Accept-Ranges':'bytes'}
                if ranged:extra['Content-Range']=f'bytes {start}-{end}/{size}'
                kind={'.flac':'audio/flac','.ogg':'audio/ogg','.mp3':'audio/mpeg','.m4a':'audio/mp4','.wav':'audio/wav'}[file.suffix.lower()]
                self.headers_for(206 if ranged else 200,kind,end-start+1,extra)
                if self.command=='HEAD':return
                with file.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        data=stream.read(min(256*1024,remaining))
                        if not data:break
                        self.wfile.write(data);remaining-=len(data)
                return
            assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if path not in assets:return self.json(404,dict(error='Not found'))
            file=WEB/assets[path];data=file.read_bytes();kind={'.html':'text/html','.js':'text/javascript','.css':'text/css'}[file.suffix]
            self.headers_for(200,kind+'; charset=utf-8',len(data))
            if self.command!='HEAD':self.wfile.write(data)
        except (KeyError,FileNotFoundError):self.json(404,dict(error='Not found'))
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except ValueError:self.json(400,dict(error='Invalid request'))

    def json_range_error(self,size):
        self.headers_for(416,'application/octet-stream',0,{'Content-Range':f'bytes */{size}'})

    def do_POST(self):
        if not self.allowed_host():return self.json(403,dict(error='Host not allowed'))
        origin=self.headers.get('Origin')
        if origin and origin!='http://'+self.headers.get('Host'):return self.json(403,dict(error='Origin not allowed'))
        if self.headers.get('Sec-Fetch-Site')=='cross-site':return self.json(403,dict(error='Cross-site request denied'))
        path=urlsplit(self.path).path
        if not path.startswith('/api/review/'):return self.json(404,dict(error='Not found'))
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:raise ValueError('Invalid length')
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Expected JSON')
            body=json.loads(self.rfile.read(length));key=path.removeprefix('/api/review/')
            if not isinstance(body,dict):raise ValueError('Expected object')
            return self.json(200,dict(track=self.server.library.save(key,body)))
        except Conflict as error:self.json(409,dict(error=str(error)))
        except (ValueError,TypeError,UnicodeError):self.json(400,dict(error='Invalid review'))
        except KeyError:self.json(404,dict(error='Unknown track'))
        except OSError:self.json(500,dict(error='保存失败，请检查磁盘后重试'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('D:/qingqingwu'))
    parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=7833)
    args=parser.parse_args();server=Server((args.host,args.port),Library(args.root))
    print(json.dumps(dict(state='LISTENING',host=args.host,port=server.server_port,tracks=len(server.library.rows))),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
