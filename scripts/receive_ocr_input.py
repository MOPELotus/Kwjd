"""Validate an OCR input by SHA and atomically admit its uploaded partial."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--episode',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--finalize',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'S\d{2}E\d{2,3}',args.episode) or not re.fullmatch('[0-9a-f]{64}',args.sha256):
        parser.error('Invalid episode or SHA')
    folder=args.root.resolve()/'input'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/(args.episode+'.mp4')
    partial=folder/(args.episode+'.mp4.uploading')
    candidate=target if target.exists() else partial if args.finalize else None
    if candidate is None or not candidate.is_file():
        if shutil.disk_usage(folder).free < 5*1024**3:
            raise RuntimeError('Less than 5 GiB free')
        raise SystemExit(44)
    with candidate.open('rb') as stream:
        actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=args.sha256:
        raise RuntimeError('SHA mismatch; preserve existing media')
    if candidate==partial:
        partial.rename(target)
    receipt={'episode':args.episode,'sha256':actual,'bytes':target.stat().st_size,
             'path':str(target),'transfer_verified':True,'subtitle_reviewed':False}
    temporary=target.with_suffix('.mp4.transfer.json.tmp')
    temporary.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
    temporary.replace(target.with_suffix('.mp4.transfer.json'))
    print(json.dumps(receipt))


if __name__=='__main__':
    main()
