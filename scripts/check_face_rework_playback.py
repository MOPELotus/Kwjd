#!/usr/bin/env python3
"""Collect physical-GPU E49 rework playback evidence; never approve visual quality."""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

FRAMES = [2063,2064,2065,2066,2067,24256,24257,24258,24259,24260,
          68399,134700,162000,167896,168430,181099,181100,181101,205800,211053]
NORMAL = [34.1,403.8,1139.3,2235.5,2244.3,2251.8,2699.3,2797.7,
          2806.7,3017.6,3429.3,3515.0]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class IPC:
    def __init__(self, path):
        self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(20)
        self.sock.connect(str(path));self.buffer=b'';self.request_id=0;self.events=[]

    def read(self):
        while b'\n' not in self.buffer:
            chunk=self.sock.recv(65536)
            if not chunk:raise RuntimeError('mpv IPC disconnected')
            self.buffer+=chunk
        line,self.buffer=self.buffer.split(b'\n',1);value=json.loads(line)
        if 'event' in value:self.events.append(value)
        return value

    def command(self, command, optional=False):
        self.request_id+=1;rid=self.request_id
        self.sock.sendall((json.dumps(dict(command=command,request_id=rid))+'\n').encode())
        while True:
            value=self.read()
            if value.get('request_id')==rid:
                if value.get('error')!='success':
                    if optional and value.get('error') in ('property unavailable','property not found'):return None
                    raise RuntimeError(value)
                return value.get('data')

    def get(self, name, optional=False):return self.command(['get_property',name],optional)

    def seek(self, position):
        self.events.clear();self.command(['seek',position,'absolute+exact'])
        while not any(x.get('event')=='playback-restart' for x in self.events):self.read()
        time.sleep(.25)  # Seek completion alone did not ensure a fresh screenshot in prior tests.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--report-dir',type=Path,required=True)
    parser.add_argument('--display',default=':97')
    parser.add_argument('--mpv',type=Path,required=True)
    args=parser.parse_args();work=args.work.resolve();dest=args.report_dir.resolve()
    active=subprocess.check_output(['systemctl','--user','show',
        'kwjd-face-rework-e49-20261028.service','-p','ActiveState','--value'],text=True).strip()
    if active!='inactive':raise RuntimeError('WAIT_GPU: full rework service has not completed')
    state=json.loads((work/'status.json').read_text())
    technical=json.loads((work/'technical_result.json').read_text())
    manifest=json.loads((work/'full.manifest.json').read_text())
    if (state['stage']!='TECHNICAL_PASS_PENDING_VISUAL' or technical['frames']!=211054
            or technical['fingerprint']!=state['fingerprint']):
        raise ValueError('Full technical acceptance has not been bound')
    source=Path(manifest['source']);output=Path(state['output'])
    if output.parent.resolve()!=work or output.resolve()==source.resolve():
        raise ValueError('Separate rework output is required')
    lease=(work.parent.parent/'.face-compute.lock').open('a+')
    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if digest(source)!=manifest['source_sha256']:
        raise ValueError('Preserved source SHA changed')
    if digest(output)!=technical['validation']['sha256'] or state['sha256']!=technical['validation']['sha256']:
        raise ValueError('Rework output SHA changed')
    dest.mkdir(parents=True,exist_ok=False)
    sock=work.parent.parent/f'face-review-{os.getpid()}.sock'
    if sock.exists():raise FileExistsError('Refuse existing player IPC socket')
    command=[str(args.mpv.resolve()),'--no-config','--load-scripts=no','--vo=gpu',
        '--gpu-api=opengl','--gpu-context=x11egl','--gpu-sw=no','--hwdec=nvdec-copy',
        '--cuda-decode-device=0','--vd-lavc-software-fallback=no','--opengl-swapinterval=0',
        '--ao=null','--pause=yes','--keep-open=yes','--force-window=yes',
        '--geometry=1920x1080+0+0','--border=no','--sub-auto=no','--osd-level=0',
        f'--input-ipc-server={sock}',f'--log-file={dest/"mpv.log"}','--msg-level=all=v',str(source)]
    report=dict(at=datetime.now().astimezone().isoformat(),script_sha256=digest(__file__),
        command=command,display=args.display,source_sha256=manifest['source_sha256'],
        output_sha256=state['sha256'],samples=[],hardware_checks_passed=False,
        visual_acceptance=False,final_acceptance=False,
        limitations='Bounded playback only. Null audio output; no listening claim. '
                    'Screenshots must actually be viewed and visual judgments recorded separately.')
    process=None;ipc=None
    try:
        with (dest/'console.log').open('w') as console:
            process=subprocess.Popen(command,env={**os.environ,'DISPLAY':args.display},stdout=console,stderr=console)
        deadline=time.monotonic()+30
        while not sock.exists():
            if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('mpv startup failed')
            time.sleep(.05)
        ipc=IPC(sock)
        while not ipc.get('vo-configured',True) or ipc.get('time-pos',True) is None:
            if time.monotonic()>deadline:raise TimeoutError('Hardware renderer startup timeout')
            time.sleep(.05)
        text=(dest/'mpv.log').read_text();match=re.search(r"GL_RENDERER='([^']+)'",text)
        if not match or 'NVIDIA RTX A4000' not in match.group(1):raise RuntimeError('Physical A4000 renderer not proved')
        report['renderer']=match.group(1)
        for kind,path in [('source',source),('rework',output)]:
            if kind=='rework':
                ipc.command(['loadfile',str(path),'replace']);time.sleep(.5)
            if Path(ipc.get('path')).resolve()!=path.resolve() or ipc.get('hwdec-current')!='nvdec-copy':
                raise RuntimeError('Target file/hardware decoder not confirmed')
            ipc.command(['set_property','pause',True]);folder=dest/kind;folder.mkdir()
            entry=dict(kind=kind,path=str(path),hwdec=ipc.get('hwdec-current'),captures=[],playback=[])
            report['samples'].append(entry)
            for frame in FRAMES:
                ipc.seek(round(frame/60,3));png=folder/f'{frame:06d}.png'
                actual=ipc.get('time-pos')
                if round(actual*60)!=frame:raise ValueError('Player selected a different frame')
                ipc.command(['screenshot-to-file',str(png),'window'])
                from PIL import Image
                with Image.open(png) as image:
                    if image.size!=(1920,1080):raise ValueError('Native window dimensions changed')
                entry['captures'].append(dict(frame=frame,requested=round(frame/60,3),actual=actual,
                                              image=str(png),sha256=digest(png)))
            for speed,starts in [(1.,NORMAL),(.25,[34.25,404.1,3018.1])]:
                ipc.command(['set_property','speed',speed])
                for start in starts:
                    ipc.seek(start);before=ipc.get('time-pos')
                    drops=ipc.get('frame-drop-count');decoder_drops=ipc.get('decoder-frame-drop-count')
                    ipc.command(['set_property','pause',False]);begin=time.monotonic();time.sleep(2)
                    ipc.command(['set_property','pause',True]);end=ipc.get('time-pos');wall=time.monotonic()-begin
                    item=dict(start=before,end=end,speed=speed,wall=wall,
                        frame_drops=ipc.get('frame-drop-count')-drops,
                        decoder_frame_drops=ipc.get('decoder-frame-drop-count')-decoder_drops)
                    entry['playback'].append(item)
                    if (not .65*wall*speed<end-before<1.35*wall*speed
                            or item['frame_drops'] or item['decoder_frame_drops']):
                        raise RuntimeError('Playback clock/drop check failed: '+str(item))
            print(json.dumps(dict(kind=kind,captures=len(entry['captures']),playback=len(entry['playback']))),flush=True)
            ipc.command(['set_property','speed',1.])
        report['hardware_checks_passed']=True
    except Exception as error:
        report['error']=repr(error)
        raise
    finally:
        if ipc:
            try:ipc.command(['quit'])
            except Exception:pass
            ipc.sock.close()
        if process:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
        sock.unlink(missing_ok=True)
        with (dest/'report.json').open('x') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    from PIL import Image,ImageDraw
    for group in range(4):
        contact=Image.new('RGB',(1280,380*5));draw=ImageDraw.Draw(contact)
        for row,index in enumerate(range(group*5,group*5+5)):
            for col,entry in enumerate(report['samples']):
                capture=entry['captures'][index]
                with Image.open(capture['image']) as image:
                    if image.size!=(1920,1080):raise ValueError('Native window dimensions changed')
                    contact.paste(image.resize((640,360)),(col*640,row*380+20))
                draw.text((col*640+5,row*380+3),f"{capture['frame']} {entry['kind']} actual={capture['actual']}",fill='white')
        contact.save(dest/f'contact-{group}.png')


if __name__=='__main__':main()
