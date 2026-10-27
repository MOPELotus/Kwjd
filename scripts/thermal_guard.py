#!/usr/bin/env python3
"""Temperature hysteresis for one explicitly identified owned backend process.

Never changes hardware settings, searches for other workers, or resumes an
externally stopped process. Linux pidfds prevent signalling a recycled PID.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from enhance_stage import atomic_json


def utc():return datetime.now(timezone.utc).isoformat()


def process(pid):
    path=Path('/proc')/str(pid)
    if path.stat().st_uid!=os.getuid():raise ValueError('Process belongs to another uid')
    before=(path/'stat').read_text();parts=before[before.rfind(')')+2:].split()
    argv=[x.decode() for x in (path/'cmdline').read_bytes().split(b'\0') if x]
    after=(path/'stat').read_text();second=after[after.rfind(')')+2:].split()
    if parts[19]!=second[19] or parts[1]!=second[1]:raise ValueError('Process changed during identity read')
    if second[0] in ('Z','X'):raise ProcessLookupError('Target exited')
    return {'pid':pid,'ppid':int(second[1]),'starttime':int(second[19]),'state':second[0],'argv':argv}


def load(path):
    if path.is_symlink():raise ValueError('Refusing symlink state/config')
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError('Expected JSON object')
    return value


def identify(post,episode,pid,worker_pid):
    job=load(post/'jobs'/f'{episode}.json');production=load(post/'backend/production-ready.json')
    digest=job.get('master_sha256');fp=production.get('backend_fingerprint')
    if (job.get('episode')!=episode or not isinstance(digest,str)
            or not re.fullmatch('[0-9a-f]{64}',digest) or production.get('master_sha256')!=digest
            or not isinstance(fp,str) or not re.fullmatch('[0-9a-f]{64}',fp)):
        raise ValueError('Job/production source and fingerprint differ')
    master=post/'incoming'/f'{episode}.clean-master.mov'
    if (post/str(job.get('master',''))).resolve()!=master.resolve():
        raise ValueError('Guard supports only the exact received episode master')
    script=Path(__file__).resolve().with_name('segmented_backend.py')
    expected=[str(post/'cuda-venv/bin/python'),str(script),str(master),
              str(post/'cache'/episode/f'{episode}.enhanced.partial.mkv'),episode,
              '--config',str(post/'backend/runtime.trt.json')]
    backend=process(pid);worker=process(worker_pid)
    if backend['ppid']!=worker_pid:raise ValueError('Backend is not a child of the specified worker')
    if backend['argv']!=expected:raise ValueError('Backend command does not match the exact episode/config')
    worker_script=str(script.with_name('server_worker.py'))
    if worker_script not in worker['argv'][1:3]:raise ValueError('Worker command identity differs')
    return {'episode':episode,'backend_pid':pid,'worker_pid':worker_pid,
            'backend_starttime':backend['starttime'],'worker_starttime':worker['starttime'],
            'backend_cmdline':backend['argv'],'worker_cmdline':worker['argv'],
            'master_sha256':digest,'backend_fingerprint':fp},backend


def verify(identity):
    backend=process(identity['backend_pid']);worker=process(identity['worker_pid'])
    if (backend['starttime']!=identity['backend_starttime'] or worker['starttime']!=identity['worker_starttime']
            or backend['ppid']!=identity['worker_pid'] or backend['argv']!=identity['backend_cmdline']
            or worker['argv']!=identity['worker_cmdline']):
        raise ValueError('Process identity changed; never signal replacement')
    return backend


def send(identity,signum):
    verify(identity)
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise RuntimeError('Linux pidfd signalling is required; no unsafe PID fallback')
    fd=os.pidfd_open(identity['backend_pid'],0)
    try:
        verify(identity)
        signal.pidfd_send_signal(fd,signum)
    finally:os.close(fd)


def temperatures():
    result={}
    for index in (0,1):
        text=subprocess.check_output(['nvidia-smi','-i',str(index),'-q','-d','TEMPERATURE'],
                                     text=True,timeout=10)
        values={}
        for name,key in [('GPU Current Temp','current'),('GPU Max Operating Temp','max_operating'),
                         ('GPU Slowdown Temp','slowdown'),('GPU Shutdown Temp','shutdown')]:
            match=re.search(r'^\s*'+re.escape(name)+r'\s*:\s*(\d+) C\s*$',text,re.MULTILINE)
            if not match:raise ValueError(f'GPU{index} missing actual hardware temperature: {name}')
            values[key]=int(match.group(1))
        if not 0<values['max_operating']<=values['slowdown']<values['shutdown']:
            raise ValueError(f'GPU{index} hardware thermal limits are inconsistent')
        values['pause']=min(values['max_operating']-1,values['shutdown']-4)
        values['resume']=values['pause']-4
        result[str(index)]=values
    return result


def decision(temps,owned_stop,stopped):
    if not stopped and owned_stop:return 'OWNERSHIP_LOST'
    if stopped and not owned_stop:return 'EXTERNAL_STOP'
    if owned_stop:
        return 'CONT' if all(v['current']<=v['resume'] for v in temps.values()) else 'WAIT'
    return 'STOP' if any(v['current']>=v['pause'] for v in temps.values()) else 'RUN'


def adopt(receipt,identity,backend):
    required=('episode','backend_pid','worker_pid','backend_starttime','worker_starttime',
              'backend_cmdline','master_sha256','backend_fingerprint')
    if (receipt.get('owned_stop') is not True or backend['state'] not in ('T','t')
            or any(type(receipt.get(k)) is not type(identity[k]) or receipt.get(k)!=identity[k] for k in required)):
        raise ValueError('Cannot adopt a stop without an exact owned-stop receipt')
    try:
        paused=datetime.fromisoformat(receipt['paused_at'].replace('Z','+00:00'))
        if not paused.tzinfo or paused.timestamp()>time.time()+5:raise ValueError('Invalid pause time')
    except (KeyError,AttributeError,TypeError,ValueError) as error:
        raise ValueError('Owned-stop receipt lacks a valid pause time') from error
    return paused.timestamp()


def run(post,episode,pid,worker_pid,adopt_stop=False):
    identity,backend=identify(post,episode,pid,worker_pid)
    path=post/'reports/thermal_pause.json'
    prior=load(path) if path.exists() else {}
    if adopt_stop and prior.get('owned_stop') is True:
        paused_at=adopt(prior,identity,backend);owned=True
    else:
        if prior.get('owned_stop') is True:raise ValueError('Existing owned stop requires explicit --adopt')
        if adopt_stop and backend['state'] in ('T','t'):
            raise ValueError('Cannot adopt an external stop without an owned-stop receipt')
        paused_at=None;owned=False
    cumulative=prior.get('cumulative_pause_seconds',0) if adopt_stop else 0
    if type(cumulative) not in (int,float) or cumulative<0:cumulative=0
    state={**prior,**identity,'guard_pid':os.getpid(),'owned_stop':owned}
    previous=None;resume_authority=True
    def publish(status,reason,temps=None):
        nonlocal previous
        state.update(state=status,reason=reason,updated_at=utc(),owned_stop=owned,
                     temperatures=temps,paused_seconds=round(cumulative+(time.time()-paused_at if owned and paused_at else 0),3),
                     cumulative_pause_seconds=cumulative)
        atomic_json(path,state)
        marker=(status,reason,owned)
        if marker!=previous:
            with (post/'logs/thermal_guard.jsonl').open('a') as stream:
                stream.write(json.dumps(state,ensure_ascii=False)+'\n')
            previous=marker
    def deliver(signum):
        try:send(identity,signum)
        except (OSError,ValueError) as error:
            publish('ENDED','Target ended/changed before pidfd signal: '+str(error));return False
        return True
    while True:
        try:backend=verify(identity)
        except (OSError,ValueError) as error:
            publish('ENDED','Backend/worker exited or identity changed: '+str(error));return
        reading_state=True
        try:
            current=load(path) if path.exists() else {}
            if previous is not None and (current.get('guard_pid')!=os.getpid()
                    or any(current.get(k)!=v for k,v in identity.items())
                    or current.get('owned_stop') is not owned):
                raise ValueError('Persistent stop ownership changed; retain existing stop')
            reading_state=False
            temps=temperatures()
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            if reading_state:resume_authority=False
            if not owned and backend['state'] not in ('T','t'):
                if not deliver(signal.SIGSTOP):return
                owned=True;paused_at=time.time();state['paused_at']=utc()
            publish('THERMAL_PAUSE' if owned else 'UNOWNED_STOP','Telemetry/state unavailable: '+str(error))
            time.sleep(5);continue
        action=decision(temps,owned,backend['state'] in ('T','t'))
        if action=='CONT' and not resume_authority:
            publish('THERMAL_PAUSE','Stop ownership was unreadable/changed; explicit re-adoption required',temps)
            time.sleep(5);continue
        if action=='OWNERSHIP_LOST':
            # Someone resumed our stop. Drop authority; next hot pass may acquire
            # a fresh stop, but we never claim to own that external transition.
            cumulative+=max(0,time.time()-paused_at) if paused_at else 0
            owned=False;paused_at=None
            publish('OWNERSHIP_LOST','Backend resumed outside this guard',temps)
        elif action=='STOP':
            if not deliver(signal.SIGSTOP):return
            owned=True;paused_at=time.time();state['paused_at']=utc()
            publish('THERMAL_PAUSE','At least one GPU reached its hardware-derived pause limit',temps)
        elif action=='CONT':
            if not deliver(signal.SIGCONT):return
            cumulative+=max(0,time.time()-paused_at) if paused_at else 0
            owned=False;paused_at=None;state['resumed_at']=utc()
            publish('RUNNING','Both GPUs cooled below their resume limits',temps)
        elif action=='EXTERNAL_STOP':publish('UNOWNED_STOP','Stopped without owned-stop authority; never resume',temps)
        elif action=='WAIT':publish('THERMAL_PAUSE','Waiting for both GPUs to cool below resume limits',temps)
        else:publish('RUNNING','Both GPUs below pause limits',temps)
        time.sleep(5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',required=True);parser.add_argument('--episode',default='S01E49')
    parser.add_argument('--pid',type=int,required=True);parser.add_argument('--worker-pid',type=int,required=True)
    parser.add_argument('--adopt',action='store_true');args=parser.parse_args()
    if not re.fullmatch(r'S01E\d{2,3}',args.episode) or min(args.pid,args.worker_pid)<=0:
        parser.error('Explicit positive PIDs and valid episode are required')
    post=Path(args.post).expanduser().absolute()
    # One persistent owner; neither normal exit nor exceptions send a CONT.
    with (post/'reports/.thermal_guard.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(post,args.episode,args.pid,args.worker_pid,args.adopt)


if __name__=='__main__':main()
