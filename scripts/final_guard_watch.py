#!/usr/bin/env python3
"""Wait for one explicit worker's final FFmpeg child, then attach strict guard.

No queue mutations, global process scan, media jobs or quality completion seals.
An ATTACHING record is not evidence that thermal protection is already running.
"""
import argparse
import fcntl
import os
from pathlib import Path
import re
import time

from enhance_stage import atomic_json
import final_encode_guard as guard


def option(argv,name,default=None):
    values=[]
    for index,value in enumerate(argv):
        if value==name:
            if index+1>=len(argv):raise ValueError('Missing worker option value')
            values.append(argv[index+1])
        elif value.startswith(name+'='):values.append(value[len(name)+1:])
    if len(values)>1:raise ValueError('Repeated worker scope option')
    return values[0] if values else default


def worker_identity(pid,starttime,episode,expected=None):
    worker=guard.thermal.process(pid)  # Includes same-uid, exit and stable-stat checks.
    argv=worker['argv'];script=str(Path(__file__).resolve().with_name('server_worker.py'))
    prefix=(len(argv)>1 and argv[1]==script) or (len(argv)>2 and argv[1:3]==['-u',script])
    if (worker['starttime']!=starttime or not prefix or option(argv,'--episode')!=episode
            or option(argv,'--stage','all')!='all'
            or (expected is not None and argv!=expected)):
        raise ValueError('Explicit worker starttime, complete argv or episode/stage identity differs')
    return worker


def direct_children(pid):
    path=Path('/proc')/str(pid)/'task'/str(pid)/'children'
    with path.open() as stream:text=stream.read(65537)
    if len(text)>65536:raise ValueError('Unexpectedly large known-worker children list')
    values=text.split()
    if any(not x.isdecimal() or int(x)<=0 for x in values):raise ValueError('Invalid direct-child PID list')
    return sorted(set(map(int,values)))


def run(post,episode,worker_pid,starttime):
    status_path=post/'reports/final_guard_watch.json'
    base={'episode':episode,'worker_pid':worker_pid,'worker_starttime':starttime,
          'watch_pid':os.getpid(),'final_quality_accepted':False,
          'protection_claim':'Only the final guardian receipt proves actual thermal attachment; ATTACHING is a candidate handoff'}
    def publish(state,reason,**extra):
        atomic_json(status_path,{**base,'state':state,'reason':reason,
                                 'updated_at':guard.thermal.utc(),**extra})
    try:initial=worker_identity(worker_pid,starttime,episode)
    except (OSError,ValueError) as error:
        publish('ENDED','Worker admission rejected: '+str(error));return
    base['worker_cmdline']=initial['argv']
    while True:
        try:worker_identity(worker_pid,starttime,episode,initial['argv'])
        except (OSError,ValueError) as error:
            publish('ENDED','Worker ended or identity changed: '+str(error));return
        try:
            job=guard.thermal.load(post/'jobs'/f'{episode}.json')
            if job.get('episode')!=episode:
                publish('ENDED','Job episode identity differs from explicit worker scope');return
        except (OSError,ValueError) as error:
            publish('WAITING','Job temporarily unreadable: '+str(error));time.sleep(2);continue
        if job.get('state')=='READY':
            publish('ENDED','READY observed without attachment; consult final report, no watcher quality claim');return
        if job.get('state')!='PROCESSING':
            publish('WAITING','Waiting for final PROCESSING phase',job_state=job.get('state'))
            time.sleep(2);continue
        matches=[];rejected=[]
        try:
            for child in direct_children(worker_pid):
                try:
                    identity,process=guard.identify(post,episode,child,worker_pid)
                    matches.append((child,identity))
                except (OSError,ValueError,KeyError,TypeError) as error:
                    rejected.append({'pid':child,'reason':str(error)[:180]})
        except (OSError,ValueError) as error:
            publish('WAITING','Known worker children temporarily unreadable: '+str(error));time.sleep(2);continue
        if len(matches)!=1:
            publish('WAITING','Multiple matching final children; no attach' if matches else 'No uniquely verified final FFmpeg yet',
                    matching_target_pids=[x[0] for x in matches],rejected_children=rejected)
            time.sleep(2);continue
        child,identity=matches[0]
        try:
            # Use the same lock as explicit final guardian CLI. Never bypass an
            # existing guardian or infer it is active merely because lock is held.
            with (post/'reports/.final_encode_guard.lock').open('a+') as lock:
                try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    publish('WAITING','Final guardian lock held; attachment not claimed',target_pid=child)
                    time.sleep(2);continue
                worker_identity(worker_pid,starttime,episode,initial['argv'])
                publish('ATTACHING','Unique verified candidate handed to inner guardian for fresh admission',
                        target_pid=child,target_starttime=identity.get('backend_starttime'),
                        master_sha256=identity.get('master_sha256'),backend_fingerprint=identity.get('backend_fingerprint'))
                # No automatic adoption: an externally stopped child or an old
                # owned-stop receipt cannot acquire CONT authority from watcher.
                guard.run(post,episode,child,worker_pid,adopt_stop=False)
            publish('ENDED','Inner guardian returned; inspect its receipt, no quality claim',target_pid=child)
            return
        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
            publish('ENDED','Final guardian admission/operation ended safely: '+str(error),target_pid=child)
            return


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',required=True);parser.add_argument('--episode',required=True)
    parser.add_argument('--worker-pid',type=int,required=True);parser.add_argument('--worker-starttime',type=int,required=True)
    args=parser.parse_args()
    if (not re.fullmatch(r'S01E\d{2,3}',args.episode)
            or min(args.worker_pid,args.worker_starttime)<=0):parser.error('Explicit valid episode/PID/starttime required')
    post=Path(args.post).expanduser().absolute()
    with (post/'reports/.final_guard_watch.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(post,args.episode,args.worker_pid,args.worker_starttime)


if __name__=='__main__':main()
