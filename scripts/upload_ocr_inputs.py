"""Upload exactly a prepared story's cuts, with matching local/remote hashes."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--host',required=True)
    parser.add_argument('--remote-root',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_.-]+',args.host) or not re.fullmatch('/[A-Za-z0-9_./-]+',args.remote_root):
        parser.error('Use an SSH alias and plain absolute remote path')
    ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',args.host]
    report=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    for clip in report['clips']:
        episode=clip['episode']
        if not re.fullmatch(r'S\d{2}E\d{2,3}',episode) or not clip['cut_technical_checked']:
            raise ValueError('Unvalidated input')
        source=Path(clip['path']).resolve(strict=True)
        with source.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=clip['output_sha256']:
            raise ValueError('Local cut changed')
        command=['python3',args.remote_root+'/repo/scripts/receive_ocr_input.py',
                 '--root',args.remote_root,'--episode',episode,'--sha256',digest]
        check=subprocess.run([*ssh,shlex.join(command)],capture_output=True,text=True)
        if check.returncode==0:
            print('REUSED '+episode,flush=True)
            continue
        if check.returncode!=44:
            raise RuntimeError(check.stderr)
        print('UPLOADING '+episode,flush=True)
        subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=15',
                        '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
                        '--',str(source),args.host+':'+args.remote_root+'/input/'+episode+'.mp4.uploading'],check=True)
        subprocess.run([*ssh,shlex.join([*command,'--finalize'])],check=True)
        print('VERIFIED '+episode,flush=True)


if __name__=='__main__':
    main()
