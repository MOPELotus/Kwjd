"""Recover the repository's pinned official RIFE model without activating it."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from cuda_rife import MODEL_SHA256,MODEL_SOURCE,ARCHIVE_SHA256


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',required=True,type=Path)
    args=parser.parse_args()
    folder=args.post.resolve()/'tools/rife-onnx';folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'rife_v2_v4.7z'
    if not archive.exists():
        partial=archive.with_suffix('.7z.downloading')
        subprocess.run(['curl','-fL','--retry','3','-C','-','-o',str(partial),MODEL_SOURCE],check=True)
        if sha(partial)!=ARCHIVE_SHA256:raise ValueError('Archive SHA mismatch; partial retained')
        partial.rename(archive)
    if sha(archive)!=ARCHIVE_SHA256:raise ValueError('Changed archive')
    listing=subprocess.check_output(['7z','l','-slt',str(archive)],text=True)
    matches=[line[7:] for line in listing.splitlines() if line.startswith('Path = ')
             and Path(line[7:]).name=='rife_v4.6.onnx']
    if len(matches)!=1:raise ValueError('Pinned model entry not unique')
    target=folder/'rife_v4.6.onnx'
    if not target.exists():
        with target.with_suffix('.onnx.extracting').open('xb') as out:
            subprocess.run(['7z','x','-so',str(archive),matches[0]],stdout=out,check=True)
        partial=target.with_suffix('.onnx.extracting')
        if sha(partial)!=MODEL_SHA256:raise ValueError('Extracted model SHA mismatch')
        partial.rename(target)
    if sha(target)!=MODEL_SHA256:raise ValueError('Changed RIFE model')
    print(json.dumps({'model':str(target),'sha256':MODEL_SHA256,'backend_activated':False}))


if __name__=='__main__':main()
