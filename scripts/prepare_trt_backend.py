#!/usr/bin/env python3
"""Publish a validated TensorRT candidate; never activate it or launch CUDA work."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import tempfile

from enhance_stage import sha256
from cuda_trt import validate_assets


def validate_pins(cfg):
    pins=cfg.get('file_sha256')
    if not isinstance(pins,dict) or not pins:
        raise ValueError('Candidate requires a nonempty file_sha256 map')
    for path,digest in pins.items():
        if (not isinstance(path,str) or not Path(path).is_absolute()
                or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)):
            raise ValueError('Invalid asset pin')
        if not Path(path).is_file() or sha256(path) != digest:
            raise ValueError('Pinned asset differs or is missing: '+path)


def check_target(path, data, *, json_value=None):
    if path.is_symlink():
        raise ValueError('Candidate target is a symlink; retained: '+str(path))
    if path.exists():
        if not path.is_file():
            raise ValueError('Candidate target is not a regular file: '+str(path))
        same=json.loads(path.read_text())==json_value if json_value is not None else path.read_bytes()==data
        if not same:
            raise ValueError('Existing candidate differs; never overwrite: '+str(path))
        return True
    return False


def publish(path, data, mode, *, json_value=None):
    if check_target(path,data,json_value=json_value):
        return
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    temp=Path(temp)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.fchmod(stream.fileno(),mode)
        try:
            os.link(temp,path)
        except FileExistsError:
            check_target(path,data,json_value=json_value)
    finally:
        temp.unlink(missing_ok=True)


def prepare(post):
    post=Path(post).expanduser().absolute()
    base=json.loads((post/'backend/runtime.cuda.json').read_text())
    additions=json.loads((post/'reports/rife_trt_config_additions.json').read_text())
    fields={'rife_trt_engine','rife_trt_model','rife_trt_provenance','file_sha256'}
    if not isinstance(additions,dict) or set(additions)!=fields:
        raise ValueError('TRT additions must contain only the reviewed asset paths and pins')
    if not isinstance(additions['file_sha256'],dict):
        raise ValueError('TRT additions need an asset pin map')
    pins=dict(base.get('file_sha256',{}))
    for path,digest in additions['file_sha256'].items():
        if path in pins and pins[path]!=digest:
            raise ValueError('Conflicting base/TRT asset pin: '+path)
        pins[path]=digest
    cfg={**base,**additions,'file_sha256':pins,'rife_backend':'cuda_trt'}
    # Neither operation imports a GPU runtime or deserializes the engine.
    validate_pins(cfg)
    validate_assets(cfg)
    python=post/'cuda-venv/bin/python'
    source=Path(__file__).with_name('segmented_backend.py').resolve()
    if not python.is_file() or not os.access(python,os.X_OK) or not source.is_file():
        raise ValueError('Existing CUDA environment and segmented backend required')
    config=post/'backend/runtime.trt.json'
    candidate=post/'backend/enhance.trt.sh'
    config_bytes=(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n').encode()
    launcher=('#!/bin/sh\nset -eu\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(source))+
              ' "$@" --config '+shlex.quote(str(config))+'\n').encode()
    # Reject either differing target before publishing either file.
    check_target(config,config_bytes,json_value=cfg)
    check_target(candidate,launcher)
    publish(config,config_bytes,0o600,json_value=cfg)
    publish(candidate,launcher,0o755)
    return {'profile':str(config),'launcher':str(candidate),'profile_sha256':sha256(config),
            'activated':False,'production_checked':False,'gpu_work_started':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',default=str(Path.home()/'qingqingwu/post'))
    print(json.dumps(prepare(parser.parse_args().post)))
