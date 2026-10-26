#!/usr/bin/env python3
"""Install hash-pinned official portable models into an isolated user workspace.

No sudo, driver/system changes or OCR environment use. Model files stay outside
Git. --configure-only reuses existing verified downloads and existing venv.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import urllib.request
import zipfile

from enhance_stage import check_space, sha256, GIB

ASSETS = [
    ('rife.zip','https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-ubuntu.zip',
     '1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68'),
    ('esrgan.zip','https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases/download/v0.2.0/realesrgan-ncnn-vulkan-v0.2.0-ubuntu.zip',
     'd0e8e1cf954f5cde11be4745dd912cc3774bef36f71c5b1cb8f74c4112b6e919'),
    ('esrgan_models.zip','https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip',
     'e5aa6eb131234b87c0c51f82b89390f5e3e642b7b70f2b9bbe95b6a285a40c96'),
]


def install(post, configure_only=False):
    post = Path(post).expanduser();tools=post/'tools';tools.mkdir(parents=True,exist_ok=True)
    check_space(tools,2*GIB,25)
    for filename,url,digest in ASSETS:
        path=tools/filename
        if not path.exists():
            if configure_only:raise FileNotFoundError(path)
            temp=path.with_suffix('.downloading')
            if temp.exists():raise ValueError('Prior partial download retained: '+str(temp))
            with urllib.request.urlopen(url,timeout=60) as response,temp.open('xb') as stream:
                while block:=response.read(1024*1024):
                    check_space(tools,512*1024**2,25);stream.write(block)
            if sha256(temp)!=digest:raise ValueError('Download SHA mismatch; partial retained')
            os.link(temp,path);temp.unlink()
        if sha256(path)!=digest:raise ValueError('Existing archive SHA mismatch; not overwritten')
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.endswith('/'):continue
                basename=Path(name).name;destination=None
                if filename=='rife.zip' and (basename=='rife-ncnn-vulkan' or '/rife-v4.6/' in name):
                    relative=Path('rife')/('rife-v4.6' if '/rife-v4.6/' in name else '')/basename
                    destination=tools/relative
                elif filename=='esrgan.zip' and basename=='realesrgan-ncnn-vulkan':
                    destination=tools/'esrgan'/basename
                elif filename=='esrgan_models.zip' and basename in ('realesrgan-x4plus.bin','realesrgan-x4plus.param'):
                    destination=tools/'esrgan/models'/basename
                elif basename.lower().startswith('license'):
                    destination=tools/'licenses'/filename/basename
                if destination:
                    payload=archive.read(name);destination.parent.mkdir(parents=True,exist_ok=True)
                    if destination.exists() and destination.read_bytes()!=payload:
                        raise ValueError('Refusing to replace differing installed asset: '+str(destination))
                    if not destination.exists():destination.write_bytes(payload)
    rife=tools/'rife/rife-ncnn-vulkan';esrgan=tools/'esrgan/realesrgan-ncnn-vulkan'
    rife.chmod(0o755);esrgan.chmod(0o755)
    python=post/'venv/bin/python'
    if not python.exists():
        if configure_only:raise FileNotFoundError(python)
        subprocess.run(['python3','-m','venv',str(post/'venv')],check=True)
        subprocess.run([str(python),'-m','pip','install','--no-cache-dir','numpy==2.5.4','Pillow==13.0.0'],check=True)
    versions=json.loads(subprocess.check_output([str(python),'-c',
        'import numpy,PIL,json; print(json.dumps({"numpy":numpy.__version__,"Pillow":PIL.__version__}))'],text=True))
    if versions != {'numpy':'2.5.4','Pillow':'13.0.0'}:
        raise ValueError('Existing isolated venv dependency versions differ; retained for inspection')
    model_files=[rife,tools/'rife/rife-v4.6/flownet.bin',tools/'rife/rife-v4.6/flownet.param',
                 esrgan,tools/'esrgan/models/realesrgan-x4plus.bin',tools/'esrgan/models/realesrgan-x4plus.param']
    cfg={'gpu':1,'gpu_name':'Tesla T10','encoder_gpu':1,
         'nvidia_icd':'/usr/share/vulkan/icd.d/nvidia_icd.json',
         'rife':str(rife),'rife_model':str(tools/'rife/rife-v4.6'),
         'esrgan':str(esrgan),'esrgan_models':str(tools/'esrgan/models'),
         'restore':True,'restore_strength':.35,'scratch':str(post/'cache/scratch'),
         'file_sha256':{str(p):sha256(p) for p in model_files},
         'installation_sources':[{'url':url,'sha256':digest} for _,url,digest in ASSETS]}
    backend=post/'backend';backend.mkdir(exist_ok=True)
    config=backend/'runtime.json'
    if config.exists() and json.loads(config.read_text())!=cfg:
        raise ValueError('Existing runtime differs; preserve and explicitly revise it')
    config.write_text(json.dumps(cfg,indent=2)+'\n')
    runner=backend/'enhance.sh';source=Path(__file__).resolve().parent/'enhance_backend.py'
    content='#!/bin/sh\nset -eu\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(source))+' "$@" --config '+shlex.quote(str(config))+'\n'
    if runner.exists() and runner.read_text()!=content:
        raise ValueError('Existing backend launcher differs; retained')
    runner.write_text(content);runner.chmod(0o755)
    print(json.dumps({'post':str(post),'physicalGPU_requires_preflight':True,'runner':str(runner),'config':str(config)},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--post',default=str(Path.home()/'qingqingwu/post'));parser.add_argument('--configure-only',action='store_true')
    args=parser.parse_args();install(args.post,args.configure_only)
