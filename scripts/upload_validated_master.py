"""Upload a stable, technically validated master and verify its remote handoff."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
import os
import tempfile
import time
from pathlib import Path

BACKGROUND = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def remote(ssh,command):
    result=None
    for attempt in range(4):
        try:result=subprocess.run([*ssh,shlex.join(command)],capture_output=True,text=True,encoding='utf-8',timeout=180, **BACKGROUND)
        except subprocess.TimeoutExpired:
            if attempt==3:raise
        else:
            if result.returncode!=255:return result
        time.sleep(5)
    return result


def prefix_hash(path,count):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while count:
            block=stream.read(min(count,4*1024**2))
            if not block:raise ValueError('Partial exceeds local source')
            digest.update(block);count-=len(block)
    return digest.hexdigest()


def sftp_upload_line(source,destination,count):
    # reput requires an existing remote target; new targets start with put.
    verb='reput' if count else 'put'
    return verb+' "'+source.as_posix()+'" "'+destination+'"\n'


def resumable_transfer(args,source,ssh,command,ssh_binary,sftp_binary):
    destination=args.remote_root+'/post/incoming/'+source.name+'.uploading'
    state_path=args.receipt.with_suffix('.transfer-state.json')
    state_path.parent.mkdir(parents=True,exist_ok=True)
    def state(stage,**values):
        item=dict(stage=stage,source=str(source),destination=destination,checked_at=time.time(),**values)
        temporary=state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(item,indent=2),encoding='utf8');temporary.replace(state_path)
    for attempt in range(1,5):
        previous=remote(ssh,[*command,'--partial-info'])
        if previous.returncode==0:
            info=json.loads(previous.stdout)
            count=info['partial_bytes']
            if prefix_hash(source,count)!=info['partial_sha256']:
                raise ValueError('Existing remote partial differs; preserve it')
        elif previous.returncode==44:count=0
        else:raise RuntimeError(previous.stderr)
        state('UPLOADING',attempt=attempt,remote_bytes=count,total_bytes=source.stat().st_size)
        print('RESUMING_FROM_BYTES '+str(count),flush=True)
        with tempfile.NamedTemporaryFile('w',suffix='.sftp',encoding='utf8',delete=False) as batch:
            batch.write(sftp_upload_line(source,destination,count))
            batch_name=batch.name
        log=state_path.with_suffix('.sftp.log')
        try:
            with log.open('a',encoding='utf8') as output:
                process=subprocess.Popen([sftp_binary,'-b',batch_name,'-B','131072','-R','64','-S',ssh_binary,
                    '-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30',
                    '-o','ServerAliveCountMax=3',args.host],stdout=output,stderr=subprocess.STDOUT, **BACKGROUND)
                changed=time.monotonic();last_check=0;size=count
                while process.poll() is None:
                    if time.monotonic()-last_check>=30:
                        check=remote(ssh,['stat','-c','%s',destination]);last_check=time.monotonic()
                        if check.returncode==0:
                            measured=int(check.stdout.strip())
                            if measured>size:changed=time.monotonic();size=measured
                            state('UPLOADING',attempt=attempt,remote_bytes=size,total_bytes=source.stat().st_size)
                    if time.monotonic()-changed>180:
                        if os.name=='nt':
                            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True, **BACKGROUND)
                        else:process.terminate()
                        process.wait();break
                    time.sleep(5)
            if process.returncode==0:
                state('UPLOAD_TRANSPORT_COMPLETE_REMOTE_HASH_PENDING',remote_bytes=source.stat().st_size)
                return
        finally:Path(batch_name).unlink(missing_ok=True)
        state('RETRY_PENDING',attempt=attempt,remote_bytes=size)
    state('FAILED_RETRY_LIMIT')
    raise RuntimeError('SFTP retries exhausted; partial and local source retained')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validation',type=Path,required=True)
    parser.add_argument('--host',required=True)
    parser.add_argument('--remote-root',required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_.-]+',args.host) or not re.fullmatch('/[A-Za-z0-9_./-]+',args.remote_root):
        parser.error('Plain SSH alias and absolute remote root required')
    data=json.loads(args.validation.read_text(encoding='utf8'))
    source=Path(data['path']).resolve(strict=True)
    match=re.fullmatch(r'(S0[1-4]E\d{2,3})\.clean-master\.mov',source.name)
    if not match or data['state']!='TECHNICALLY_VALIDATED':raise ValueError('Unvalidated master')
    episode=match[1]; before=source.stat()
    with source.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=data['sha256'] or before.st_size!=data['bytes']:raise ValueError('Master changed after validation')
    git=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/usr/bin'
    ssh_binary=str(git/'ssh.exe') if (git/'ssh.exe').is_file() else 'ssh'
    sftp_binary=str(git/'sftp.exe') if (git/'sftp.exe').is_file() else 'sftp'
    ssh=[ssh_binary,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30',args.host]
    command=['python3',args.remote_root+'/repo/scripts/receive_master_handoff.py','--root',args.remote_root,
             '--episode',episode,'--sha256',digest,'--bytes',str(before.st_size),
             '--expected-duration',str(data['duration_seconds'])]
    rate=data.get('frame_rate_audit',{}).get('expected_frame_rate','25/1')
    if rate not in ('25/1','30/1') or data['video']['r_frame_rate']!=rate:
        raise ValueError('Unexpected validated frame rate')
    command+=['--expected-frame-rate',rate]
    check=remote(ssh,command)
    if check.returncode==44:
        print('UPLOADING '+episode,flush=True)
        resumable_transfer(args,source,ssh,command,ssh_binary,sftp_binary)
        check=remote(ssh,[*command,'--finalize'])
    if check.returncode:raise RuntimeError(check.stderr)
    after=source.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Master changed during upload')
    result=json.loads(check.stdout)
    if result['sha256']!=digest or not result['transfer_verified']:raise ValueError('Missing verified handoff')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(result,indent=2),encoding='utf8')
    print('REMOTE_SHA_AND_DECODE_VERIFIED '+episode,flush=True)


if __name__=='__main__':main()
