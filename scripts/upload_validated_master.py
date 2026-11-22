"""Upload a stable, technically validated master and verify its remote handoff."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path


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
    ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30',args.host]
    command=['python3',args.remote_root+'/repo/scripts/receive_master_handoff.py','--root',args.remote_root,
             '--episode',episode,'--sha256',digest,'--bytes',str(before.st_size),
             '--expected-duration',str(data['duration_seconds'])]
    check=subprocess.run([*ssh,shlex.join(command)],capture_output=True,text=True)
    if check.returncode==44:
        print('UPLOADING '+episode,flush=True)
        subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30',
                        '-o','ServerAliveCountMax=3','--',str(source),
                        args.host+':'+args.remote_root+'/post/incoming/'+source.name+'.uploading'],check=True)
        check=subprocess.run([*ssh,shlex.join([*command,'--finalize'])],capture_output=True,text=True)
    if check.returncode:raise RuntimeError(check.stderr)
    after=source.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Master changed during upload')
    result=json.loads(check.stdout)
    if result['sha256']!=digest or not result['transfer_verified']:raise ValueError('Missing verified handoff')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    args.receipt.write_text(json.dumps(result,indent=2),encoding='utf8')
    print('REMOTE_SHA_AND_DECODE_VERIFIED '+episode,flush=True)


if __name__=='__main__':main()
