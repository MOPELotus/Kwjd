#!/usr/bin/env python3
"""Configure pinned general SRVGG CUDA restoration and official CUDA ONNX RIFE.

Keeps the portable backend/environment intact. --activate switches only the
local launcher; no production-readiness receipt is created by installation.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.request

from enhance_stage import sha256,check_space,GIB,atomic_json

COMMIT='a4abfb2979a7bbff3f69f58f58ae324608821e27'
ASSETS={
    'srvgg_arch.py':(f'https://raw.githubusercontent.com/xinntao/Real-ESRGAN/{COMMIT}/realesrgan/archs/srvgg_arch.py',
                     '7419a75e789ca09295266563d74a6d7c08d6c24502af6b0a919b1811d0a0be59'),
    'LICENSE':(f'https://raw.githubusercontent.com/xinntao/Real-ESRGAN/{COMMIT}/LICENSE',
               '4a699ec4863d96a91fc265948a0c90033f7e8735d515524dcf3444736406e0c2'),
    'realesr-general-x4v3.pth':('https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth',
                              '8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292'),
    'realesr-general-wdn-x4v3.pth':('https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth',
                                  '1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca'),
}


def prepare(post,configure_only=False,activate=False,revise_candidate=False,shared_restore=False):
    post=Path(post).expanduser();cache=post/'tools/realesr-general-official';cache.mkdir(parents=True,exist_ok=True)
    check_space(cache,4*GIB,25)
    python=post/'cuda-venv/bin/python'
    if not python.exists():
        if configure_only:raise FileNotFoundError(python)
        subprocess.run(['python3','-m','venv',str(post/'cuda-venv')],check=True)
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','--index-url',
                        'https://download.pytorch.org/whl/cu126','torch==2.6.0'],check=True)
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','numpy==2.5.4','Pillow==13.0.0',
                        'onnxruntime-gpu==1.23.2'],check=True)
    versions=json.loads(subprocess.check_output([str(python),'-c',
        'import torch,numpy,PIL,onnxruntime,json;print(json.dumps([torch.__version__,numpy.__version__,PIL.__version__,onnxruntime.__version__]))'],text=True))
    if versions != ['2.6.0+cu126','2.5.4','13.0.0','1.23.2']:
        raise ValueError('Isolated CUDA environment versions differ; retained')
    for name,(url,digest) in ASSETS.items():
        path=cache/name
        if not path.exists():
            if configure_only:raise FileNotFoundError(path)
            partial=path.with_name(path.name+'.downloading')
            with urllib.request.urlopen(url,timeout=60) as response,partial.open('xb') as out:
                while block:=response.read(1024*1024):out.write(block)
            if sha256(partial)!=digest:raise ValueError('Download SHA differs; partial retained')
            os.link(partial,path);partial.unlink()
        if sha256(path)!=digest:raise ValueError('Pinned official asset differs: '+name)
    cfg=json.loads((post/'backend/runtime.json').read_text())
    cfg.update(restoration_backend='cuda_srvgg',srvgg_source=str(cache/'srvgg_arch.py'),
               srvgg_general=str(cache/'realesr-general-x4v3.pth'),srvgg_wdn=str(cache/'realesr-general-wdn-x4v3.pth'),
               denoise_strength=.35,restore_strength=.35,restore=True,
               chunk_source_frames=125,segment_source_frames=250,rife_jobs='2:2:4')
    from cuda_rife import MODEL_SHA256
    onnx=post/'tools/rife-onnx/rife_v4.6.onnx'
    if not onnx.is_file() or sha256(onnx)!=MODEL_SHA256:
        raise ValueError('Prepare verified official vs-mlrt RIFE v4.6 ONNX first')
    cfg.update(rife_backend='cuda_onnx',rife_onnx=str(onnx))
    cfg['file_sha256'][str(onnx)]=MODEL_SHA256
    if shared_restore:
        cfg.update(restore_gpu=0,restore_gpu_name='NVIDIA RTX A4000',restore_tile=512,
                   restore_memory_gib=.75,restore_free_reserve_gib=4.0,pipeline_prefetch=True)
    cfg['file_sha256'].update({str(cache/name):digest for name,(_,digest) in ASSETS.items()})
    cfg['restoration_source']={'repository':'https://github.com/xinntao/Real-ESRGAN','commit':COMMIT,
                               'license':'BSD-3-Clause','assets':{n:{'url':u,'sha256':h} for n,(u,h) in ASSETS.items()}}
    config=post/'backend/runtime.cuda.json'
    if config.exists() and json.loads(config.read_text())!=cfg:
        if not revise_candidate:raise ValueError('Candidate CUDA profile differs; revise explicitly, never silently replace')
        backup=config.with_name('runtime.cuda.previous-'+sha256(config)[:16]+'.json')
        if not backup.exists():os.link(config,backup)
    atomic_json(config,cfg)
    source=Path(__file__).with_name('segmented_backend.py').resolve()
    content='#!/bin/sh\nset -eu\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(source))+' "$@" --config '+shlex.quote(str(config))+'\n'
    candidate=post/'backend/enhance.cuda.sh'
    if candidate.exists() and candidate.read_text()!=content:
        if not revise_candidate:raise ValueError('Existing CUDA launcher differs')
        backup=candidate.with_name('enhance.cuda.previous-'+sha256(candidate)[:16]+'.sh')
        if not backup.exists():os.link(candidate,backup)
    staging=candidate.with_name(candidate.name+'.configuring');staging.write_text(content);staging.chmod(0o755)
    os.replace(staging,candidate)
    if activate:
        launcher=post/'backend/enhance.sh'
        if launcher.exists() and launcher.read_text()!=content:
            backup=launcher.with_name('enhance.previous-'+sha256(launcher)[:16]+'.sh')
            if not backup.exists():os.link(launcher,backup)
        partial=launcher.with_name('enhance.activating.sh');partial.write_text(content);partial.chmod(0o755)
        os.replace(partial,launcher)
    print(json.dumps({'profile':str(config),'launcher':str(candidate),'activated':activate,
                      'production_checked':False,'versions':versions}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',default=str(Path.home()/'qingqingwu/post'))
    parser.add_argument('--configure-only',action='store_true');parser.add_argument('--activate',action='store_true')
    parser.add_argument('--revise-candidate',action='store_true')
    parser.add_argument('--bounded-shared-restoration',action='store_true',
                        help='0.75GiB allocator-capped512tile restoration, reserving4GiB plus future allocations')
    args=parser.parse_args();prepare(args.post,args.configure_only,args.activate,args.revise_candidate,args.bounded_shared_restoration)
