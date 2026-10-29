"""Task-only hardware collection; screenshots require separate actual review."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path.home()/'qingqingwu'
sys.path.insert(0,str(ROOT/'repo/scripts'))
from check_face_rework_playback import IPC

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def inactive():
    for unit in ('kwjd-native-production-e50.service','kwjd-native-production-e51.service'):
        state=subprocess.check_output(['systemctl','--user','show',unit,'-p','MainPID','--value'],text=True).strip()
        if state not in ('0',''):raise RuntimeError('Active producer '+unit)

def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',type=Path,action='append',required=True)
    p.add_argument('--report-dir',type=Path,required=True);a=p.parse_args()
    dest=a.report_dir.absolute();dest.mkdir(parents=True,exist_ok=False)
    report=dict(samples=[],hardware_checks_passed=False,visual_acceptance=False,
        quality_subject='Guo Degang only',audio_listening=False,
        limitations='Finite actual hardware playback and screenshots; root must separately view screenshots. Audio output disabled; PCM has separate byte evidence.')
    try:
        inactive()
        with (ROOT/'post/jobs/.worker.lock').open('a+') as queue,(ROOT/'post-home/.face-compute.lock').open('a+') as compute:
            for lease in (queue,compute):fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            for suite_index,suite in enumerate(a.suite):
                data=json.loads(suite.read_text())
                if data['status']=='TECHNICAL_LONG_TRIAL_COMPLETE_VISUAL_REVIEW_PENDING':
                    cases=[dict(name=f'source-{s["source_start"]}',continuous_sample=s['continuous_sample'],
                                audio_validation=s['mux_validation']) for s in data['segments']]
                elif data['status']=='TECHNICAL_BENCHMARK_COMPLETE_VISUAL_REVIEW_PENDING':cases=data['cases']
                else:raise ValueError('Unclosed benchmark')
                for case in cases:
                    inactive();media=Path(case['continuous_sample']);expected=case['audio_validation']['sha256']
                    if sha(media)!=expected:raise ValueError('Sample SHA changed')
                    before_stat=media.stat()
                    identity=(before_stat.st_dev,before_stat.st_ino,before_stat.st_size,before_stat.st_mtime_ns)
                    duration=case['audio_validation']['frames']/60
                    folder=dest/f'{suite_index}-{case["name"]}';folder.mkdir()
                    sock=ROOT/'work'/f'nsp-{os.getpid()}-{suite_index}-{case["name"]}.sock';log=folder/'mpv.log'
                    if sock.exists():raise FileExistsError('Existing owned player socket')
                    args=[str(ROOT/'work/tools/mpv/mpv-user'),'--no-config','--load-scripts=no',
                        '--vo=gpu','--gpu-api=opengl','--gpu-context=x11egl','--gpu-sw=no',
                        '--hwdec=nvdec-copy','--cuda-decode-device=0','--vd-lavc-software-fallback=no',
                        '--opengl-swapinterval=0','--audio=no','--pause=yes','--keep-open=yes',
                        '--force-window=yes','--geometry=1920x1080+0+0','--border=no',
                        '--sub-auto=no','--osc=no','--osd-level=0',f'--input-ipc-server={sock}',
                        f'--log-file={log}','--msg-level=all=v',str(media)]
                    entry=dict(case=case['name'],suite=str(suite),suite_sha256=sha(suite),
                        media=str(media),media_sha256=expected,command=args,captures=[],positions=[])
                    report['samples'].append(entry);player=ipc=None
                    try:
                        with (folder/'console.log').open('w') as console:
                            player=subprocess.Popen(args,env={**os.environ,'DISPLAY':':97'},stdout=console,stderr=console)
                        deadline=time.monotonic()+25
                        while not sock.exists():
                            if player.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Player startup failed')
                            time.sleep(.05)
                        ipc=IPC(sock)
                        while not ipc.get('vo-configured',True) or ipc.get('time-pos',True) is None:
                            if time.monotonic()>deadline:raise TimeoutError('Renderer not ready')
                            time.sleep(.05)
                        text=log.read_text();match=re.search(r"GL_RENDERER='([^']+)'",text)
                        if not match or 'NVIDIA RTX A4000' not in match.group(1):raise RuntimeError('Physical A4000 renderer missing')
                        entry['renderer']=match.group(1);entry['hwdec']=ipc.get('hwdec-current')
                        if entry['hwdec']!='nvdec-copy':raise RuntimeError('Hardware decoder missing')
                        points=(0.,.5,1.3,1.4,2.,3.,4.8)+( (7.,9.5) if duration>=10 else () )
                        for t in points:
                            ipc.seek(t);image=folder/f'{t:.3f}.png'
                            actual=ipc.get('time-pos');ipc.command(['screenshot-to-file',str(image),'window'])
                            from PIL import Image
                            with Image.open(image) as im:
                                if im.size!=(1920,1080):raise ValueError('Window not native-sized')
                            entry['captures'].append(dict(requested=t,actual=actual,path=str(image),sha256=sha(image)))
                        ipc.seek(.1);ipc.command(['set_property','pause',False]);time.sleep(.4)
                        ipc.command(['set_property','pause',True]);before=ipc.get('time-pos')
                        drops=ipc.get('frame-drop-count');ddrops=ipc.get('decoder-frame-drop-count')
                        ipc.command(['set_property','pause',False]);start=time.monotonic()
                        for _ in range(9 if duration>=10 else 4):
                            time.sleep(.8);entry['positions'].append(dict(wall=time.monotonic()-start,position=ipc.get('time-pos')))
                        ipc.command(['set_property','pause',True]);end=ipc.get('time-pos');wall=time.monotonic()-start
                        entry['playback']=dict(start=before,end=end,wall=wall,
                            frame_drops=ipc.get('frame-drop-count')-drops,
                            decoder_frame_drops=ipc.get('decoder-frame-drop-count')-ddrops)
                        m=entry['playback']
                        if not .8*wall<end-before<1.2*wall or m['frame_drops'] or m['decoder_frame_drops']:
                            raise ValueError('Playback clock/drop check failed '+str(m))
                        if sha(media)!=expected:raise ValueError('Sample changed during playback')
                        after_stat=media.stat()
                        entry['file_identity_unchanged']=identity==(after_stat.st_dev,after_stat.st_ino,after_stat.st_size,after_stat.st_mtime_ns)
                        if not entry['file_identity_unchanged']:raise ValueError('Sample identity changed during playback')
                    finally:
                        if ipc:
                            try:ipc.command(['quit'])
                            except Exception:pass
                            ipc.sock.close()
                        if player:
                            try:player.wait(timeout=5)
                            except subprocess.TimeoutExpired:player.terminate();player.wait(timeout=5)
                        if sock.exists():sock.unlink()
                        if log.exists():entry['renderer_log_sha256']=sha(log)
            report['hardware_checks_passed']=True
    except Exception as e:
        report['error']=repr(e);raise
    finally:(dest/'collection.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
