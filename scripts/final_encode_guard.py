#!/usr/bin/env python3
"""Explicit-PID thermal protection for this project's final FFmpeg child only.

Receipt bindings identify the authorised inputs; full media quality/hash checks
remain the worker's responsibility. No discovery, queueing or GPU reconfiguration.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import time

from enhance_stage import atomic_json
import thermal_guard as thermal


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return isinstance(value,str) and bool(re.fullmatch('[0-9a-f]{64}',value))


def file_identity(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size<=0:
        raise ValueError('Expected nonempty regular input: '+str(path))
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]


def input_binding(post,episode):
    job=thermal.load(post/'jobs'/f'{episode}.json')
    if (job.get('episode')!=episode or job.get('state')!='PROCESSING'
            or not digest(job.get('master_sha256'))
            or job.get('output')!=f'final/{episode}.final-1080p60.mkv'):
        raise ValueError('Final guard requires exact PROCESSING episode/output/master binding')
    directory=post/'cache'/episode
    bundle_path=directory/f'{episode}.source.ready.json'
    checkpoint_path=directory/f'{episode}.enhanced.mkv.ready.json'
    ass=directory/f'{episode}.ass'
    bundle=thermal.load(bundle_path);checkpoint=thermal.load(checkpoint_path)
    fp=checkpoint.get('backend_fingerprint');master=job['master_sha256']
    if (bundle.get('schema_version')!=1 or bundle.get('episode')!=episode
            or bundle.get('master_sha256')!=master or checkpoint.get('episode')!=episode
            or checkpoint.get('master_sha256')!=master or not digest(fp)
            or bundle.get('enhanced_checkpoint')!=checkpoint
            or not digest(checkpoint.get('sha256'))
            or bundle.get('enhanced_sha256')!=checkpoint['sha256']
            or not digest(bundle.get('audio_sha256')) or not digest(bundle.get('decoded_pcm_sha256'))
            or bundle.get('enhanced_decode_checked') is not True or bundle.get('pcm_equivalent') is not True):
        raise ValueError('Source bundle/enhancement receipt does not bind this final job')
    production=thermal.load(post/'backend/production-ready.json')
    if production.get('master_sha256')!=master or production.get('backend_fingerprint')!=fp:
        raise ValueError('Final source differs from active production identity')
    source=bundle.get('source_probe')
    if not isinstance(source,dict) or not isinstance(source.get('streams'),list):
        raise ValueError('Invalid preserved source stream metadata')
    streams=source['streams']
    audio=[s for s in streams if isinstance(s,dict) and s.get('codec_type')=='audio']
    if len(audio)!=1:raise ValueError('Exactly one preserved original mixed stream required')
    raw_rate=audio[0].get('sample_rate')
    if type(raw_rate) not in (int,str) or not re.fullmatch(r'\d{1,10}',str(raw_rate)):
        raise ValueError('Invalid native audio rate')
    rate=int(raw_rate)
    if rate<48000:raise ValueError('Unsupported source audio rate')
    offset=bundle.get('audio_offset_seconds')
    if type(offset) not in (int,float) or not math.isfinite(offset):
        raise ValueError('Invalid source audio offset')
    media=[directory/f'{episode}.enhanced.mkv',directory/f'{episode}.mixed.flac']
    records=[bundle_path,checkpoint_path,ass]
    style_rel=Path(str(job.get('style','')))
    if style_rel.is_absolute() or '..' in style_rel.parts or style_rel.parts[:1]!=('styles',):
        raise ValueError('Style must remain inside the approved styles directory')
    style=post/style_rel;seal=style.with_name(style.name+'.ready.json')
    approval=thermal.load(seal);font_pins=approval.get('fonts')
    if (approval.get('approved') is not True or approval.get('master_sha256')!=master
            or not isinstance(font_pins,dict) or not font_pins or approval.get('sha256')!=sha(style)):
        raise ValueError('Style/font approval differs from the final source')
    fonts=[]
    for name,pin in font_pins.items():
        if not isinstance(name,str) or Path(name).name!=name or not digest(pin):
            raise ValueError('Invalid approved font name/hash')
        font=post/'styles/fonts'/name
        file_identity(font)
        if sha(font)!=pin:raise ValueError('Approved font changed')
        fonts.append(font)
    actual_fonts={p.name for p in (post/'styles/fonts').iterdir()
                  if p.suffix.lower() in ('.ttf','.otf','.ttc')}
    if actual_fonts!=set(font_pins):raise ValueError('Font directory differs from approved exact font set')
    records += [style,seal]+fonts
    facts={str(p):file_identity(p) for p in media+records}
    small_hashes={str(p):sha(p) for p in records}
    # The worker performed full media hashes before final encoding; rehashing
    # tens of GiB here would delay attaching thermal protection to the encoder.
    return {'episode':episode,'master_sha256':master,'backend_fingerprint':fp,
            'source_bundle_sha256':small_hashes[str(bundle_path)],'ass_sha256':small_hashes[str(ass)],
            'style_sha256':small_hashes[str(style)],'fonts':font_pins,
            'preserved_audio_sha256':bundle['audio_sha256'],'enhanced_sha256':checkpoint['sha256'],
            'file_identities':facts,'small_file_sha256':small_hashes,
            'media_hash_policy':'worker-validated receipt bindings; guardian checks inode/size/mtime, not full media hashes',
            'sample_rate':rate,'audio_offset_seconds':offset}


def expected_command(post,episode,binding):
    directory=post/'cache'/episode;ass=directory/f'{episode}.ass'
    def escaped(path):return str(path).replace('\\','/').replace(':',r'\:')
    vf=f"ass=filename='{escaped(ass)}':fontsdir='{escaped(post/'styles/fonts')}'"
    return ['ffmpeg','-hide_banner','-n','-nostdin','-v','warning',
            '-i',str(directory/f'{episode}.enhanced.mkv'),'-itsoffset',str(binding['audio_offset_seconds']),
            '-i',str(directory/f'{episode}.mixed.flac'),'-map','0:v:0','-map','1:a:0?',
            '-vf',vf,'-c:v','hevc_nvenc','-gpu','1','-preset','p6','-rc','vbr','-cq','18',
            '-b:v','0','-maxrate','50M','-bufsize','100M','-pix_fmt','p010le',
            '-c:a','flac','-sample_fmt','s32','-bits_per_raw_sample','24','-ar',str(binding['sample_rate']),
            str(post/'final'/f'{episode}.final-1080p60.partial.mkv')]


def executable(pid):return str((Path('/proc')/str(pid)/'exe').resolve(strict=True))


def identify(post,episode,pid,worker_pid):
    binding=input_binding(post,episode)
    child=thermal.process(pid);worker=thermal.process(worker_pid)
    argv=expected_command(post,episode,binding)
    worker_script=str(Path(__file__).resolve().with_name('server_worker.py'))
    expected_exe=shutil.which('ffmpeg')
    if not expected_exe:raise ValueError('Cannot locate expected FFmpeg executable')
    expected_exe=str(Path(expected_exe).resolve(strict=True))
    if (child['ppid']!=worker_pid or child['argv']!=argv
            or worker_script not in worker['argv'][1:3] or executable(pid)!=expected_exe):
        raise ValueError('Final FFmpeg/worker executable, argv or direct-parent identity differs')
    identity={**binding,'stage':'FINAL_ENCODE','backend_pid':pid,'target_pid':pid,'worker_pid':worker_pid,
              'backend_starttime':child['starttime'],'worker_starttime':worker['starttime'],
              'backend_cmdline':argv,'worker_cmdline':worker['argv'],'target_executable':expected_exe}
    verify(identity)
    return identity,child


def verify(identity):
    child=thermal.verify(identity)
    if executable(identity['backend_pid'])!=identity['target_executable']:
        raise ValueError('Final encoder executable changed')
    return child


def verify_inputs(post,identity):
    if input_binding(post,identity['episode'])!={k:v for k,v in identity.items() if k in (
            'episode','master_sha256','backend_fingerprint','source_bundle_sha256','ass_sha256',
            'style_sha256','fonts',
            'preserved_audio_sha256','enhanced_sha256','file_identities','small_file_sha256',
            'media_hash_policy','sample_rate','audio_offset_seconds')}:
        raise ValueError('Final input/receipt/ASS identity changed; hold owned stop')


def adopt(receipt,identity,child):
    if child['state']!='T' or any(type(receipt.get(k)) is not type(v) or receipt.get(k)!=v for k,v in identity.items()):
        raise ValueError('Final stop receipt differs from exact process/source/ASS identity')
    return thermal.adopt(receipt,identity,child)


def run(post,episode,pid,worker_pid,adopt_stop=False):
    identity,child=identify(post,episode,pid,worker_pid)
    path=post/'reports/final_encode_thermal_pause.json'
    history=post/'logs/final_encode_thermal_guard.jsonl'
    prior=thermal.load(path) if path.exists() else {}
    if adopt_stop:paused_at=adopt(prior,identity,child);owned=True
    else:
        if prior.get('owned_stop') is True:
            raise ValueError('An existing owned final stop requires explicit --adopt')
        paused_at=None;owned=False
    cumulative=prior.get('cumulative_pause_seconds',0) if adopt_stop else 0
    if type(cumulative) not in (int,float) or not math.isfinite(cumulative) or cumulative<0:
        raise ValueError('Invalid accumulated final pause time')
    state={**identity,'guard_pid':os.getpid(),'owned_stop':owned}
    if owned:state['paused_at']=prior['paused_at']
    previous=None;resume_authority=True
    def publish(status,reason,temps=None):
        nonlocal previous
        state.update(state=status,reason=reason,updated_at=thermal.utc(),owned_stop=owned,
                     temperatures=temps,cumulative_pause_seconds=cumulative,
                     paused_seconds=round(cumulative+(max(0,time.time()-paused_at) if owned and paused_at else 0),3),
                     final_quality_accepted=False)
        atomic_json(path,state)
        marker=(status,reason,owned)
        if marker!=previous:
            with history.open('a') as stream:stream.write(json.dumps(state,ensure_ascii=False)+'\n')
            previous=marker
    def deliver(signum):
        nonlocal resume_authority
        if signum==signal.SIGCONT:
            try:
                verify_inputs(post,identity)
                current=thermal.load(path)
                if (current.get('guard_pid')!=os.getpid() or current.get('owned_stop') is not True
                        or any(current.get(k)!=v for k,v in identity.items())):
                    raise ValueError('Stop authority changed immediately before CONT')
            except (OSError,ValueError,KeyError,TypeError) as error:
                resume_authority=False
                publish('THERMAL_PAUSE','CONT withheld after authority recheck: '+str(error));return False
        try:
            verify(identity)
            thermal.send(identity,signum)
        except (OSError,ValueError) as error:
            publish('ENDED','Target ended/changed before pidfd signal: '+str(error));return False
        return True
    while True:
        try:child=verify(identity)
        except (OSError,ValueError) as error:
            publish('ENDED','Final encoder/worker ended or changed: '+str(error));return
        if child['state']=='t':
            resume_authority=False
            publish('UNOWNED_STOP','Ptrace stop is not owned by this guardian')
            time.sleep(5);continue
        try:
            verify_inputs(post,identity)
            if previous is not None:
                current=thermal.load(path)
                if (current.get('guard_pid')!=os.getpid() or current.get('owned_stop') is not owned
                        or any(current.get(k)!=v for k,v in identity.items())):
                    raise ValueError('Final stop authority record changed')
        except (OSError,ValueError,KeyError,TypeError) as error:
            resume_authority=False
            if not owned and child['state'] not in ('T','t'):
                if not deliver(signal.SIGSTOP):return
                owned=True;paused_at=time.time();state['paused_at']=thermal.utc()
            publish('THERMAL_PAUSE' if owned else 'UNOWNED_STOP','Input/state authority uncertain: '+str(error))
            time.sleep(5);continue
        try:temps=thermal.temperatures()
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            if not owned and child['state'] not in ('T','t'):
                if not deliver(signal.SIGSTOP):return
                owned=True;paused_at=time.time();state['paused_at']=thermal.utc()
            publish('THERMAL_PAUSE' if owned else 'UNOWNED_STOP','Temperature unavailable: '+str(error))
            time.sleep(5);continue
        action=thermal.decision(temps,owned,child['state'] in ('T','t'))
        if action=='CONT' and not resume_authority:
            publish('THERMAL_PAUSE','Explicit re-adoption required after authority uncertainty',temps)
        elif action=='CONT':
            if not deliver(signal.SIGCONT):return
            cumulative+=max(0,time.time()-paused_at) if paused_at else 0
            owned=False;paused_at=None;state['resumed_at']=thermal.utc()
            publish('RUNNING','Both GPUs cooled below hardware-derived resume limits',temps)
        elif action=='STOP':
            if not deliver(signal.SIGSTOP):return
            owned=True;paused_at=time.time();state['paused_at']=thermal.utc()
            publish('THERMAL_PAUSE','A GPU reached its hardware-derived pause limit',temps)
        elif action=='OWNERSHIP_LOST':
            cumulative+=max(0,time.time()-paused_at) if paused_at else 0
            owned=False;paused_at=None;resume_authority=False
            publish('OWNERSHIP_LOST','Target resumed outside this guard; no CONT authority',temps)
        elif action=='EXTERNAL_STOP':publish('UNOWNED_STOP','External STOP is never resumed',temps)
        elif action=='WAIT':publish('THERMAL_PAUSE','Waiting for all GPUs to cool below resume limits',temps)
        else:publish('RUNNING','All GPUs below pause thresholds',temps)
        time.sleep(5)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',required=True);parser.add_argument('--episode',default='S01E49')
    parser.add_argument('--pid',type=int,required=True);parser.add_argument('--worker-pid',type=int,required=True)
    parser.add_argument('--adopt',action='store_true');args=parser.parse_args()
    if not re.fullmatch(r'S01E\d{2,3}',args.episode) or min(args.pid,args.worker_pid)<=0:
        parser.error('Valid episode and explicit positive PIDs required')
    post=Path(args.post).expanduser().absolute()
    with (post/'reports/.final_encode_guard.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        # No finally-SIGCONT: interrupted guardians retain a recoverable stop.
        run(post,args.episode,args.pid,args.worker_pid,args.adopt)


if __name__=='__main__':main()
