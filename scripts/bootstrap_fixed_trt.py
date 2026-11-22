"""Rebuild the established TRT recipe after cleanup; never activate production.

Archived conversion/build recipes execute unchanged in their original relative
layout inside a fresh isolated build directory. Old evidence is never rewritten.
"""
import argparse
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from enhance_stage import atomic_json,sha256,check_space
from cuda_trt import (validate_assets,audit_typed_model,SOURCE_SHA256,MODEL_SHA256,
                      CONVERSION_SHA256,BUILD_SHA256,TRT_VERSION,DRIVER_VERSION)
from ffmpeg_timestamps import showinfo_frames


def versions():
    required={'torch':'2.6.0+cu126','torchvision':'0.21.0+cu126','gfpgan':'1.3.8',
              'basicsr':'1.4.2','facexlib':'0.3.0','tensorrt-cu12':TRT_VERSION,'onnx':'1.20.1'}
    actual={name:importlib.metadata.version(name) for name in required}
    if actual!=required:raise ValueError('Fixed runtime versions differ: '+str(actual))
    return actual


def run(command,log,env=None,guard_gpu=False):
    with Path(log).open('x') as stream:
        if not guard_gpu:
            subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,env=env,check=True);return
        with subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,env=env) as process:
            telemetry=Path(log).with_suffix('.telemetry.jsonl')
            try:
                while process.poll() is None:
                    values=subprocess.check_output(['nvidia-smi','-i','1','--query-gpu=temperature.gpu,memory.free',
                        '--format=csv,noheader,nounits'],text=True).strip().split(', ')
                    temperature,free=map(int,values)
                    with telemetry.open('a') as evidence:
                        evidence.write(json.dumps(dict(at=time.time(),physical_gpu=1,temperature_c=temperature,free_mib=free))+'\n')
                    if temperature>=85 or free<1024:
                        raise RuntimeError('Fixed TRT rebuild waits for physical T10 cooling/headroom; no fallback')
                    time.sleep(2)
                if process.returncode:raise subprocess.CalledProcessError(process.returncode,command)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


def bootstrap(post,master):
    post=Path(post).resolve(strict=True);master=Path(master).resolve(strict=True)
    backend=post/'backend';backend.mkdir(exist_ok=True)
    with (backend/'fixed-trt-rebuild.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return rebuild(post,master)


def rebuild(post,master):
    actual_versions=versions();repo=Path(__file__).resolve().parents[1]
    lock=repo/'config/workflows/native-face-v1.json';workflow=json.loads(lock.read_text())
    if workflow['production_rife_backend']!='cuda_trt':raise ValueError('Fixed workflow changed')
    profile=post/'backend/runtime.trt.json'
    if profile.exists():
        prior=json.loads(profile.read_text())
        try:validate_assets(prior)
        except (ValueError,FileNotFoundError,KeyError):pass
        else:return {'state':'FIXED_TRT_ASSETS_REUSED','config':str(profile),'activated':False}
    for job in (post/'jobs').glob('*.json'):
        if json.loads(job.read_text()).get('state') in ('ENHANCING','PROCESSING'):
            raise ValueError('An active production context cannot be replaced')
    check_space(post,5*2**30,25)
    devices=subprocess.check_output(['nvidia-smi','-i','1','--query-gpu=name,driver_version,compute_cap,temperature.gpu,memory.free',
        '--format=csv,noheader,nounits'],text=True).strip().split(', ')
    if devices[:3]!=['Tesla T10',DRIVER_VERSION,'7.5'] or int(devices[3])>=75 or int(devices[4])<8192:
        raise ValueError('Fixed physical T10/driver/headroom build admission failed: '+str(devices))
    received=json.loads((post/'handoffs'/(master.stem.split('.')[0]+'.master-received.json')).read_text())
    digest=sha256(master)
    if received.get('transfer_verified') is not True or received.get('sha256')!=digest:
        raise ValueError('A verified actual master is required for rebuild comparison')
    source=post/'tools/rife-onnx/rife_v4.6.onnx'
    if sha256(source)!=SOURCE_SHA256:raise ValueError('Official fixed RIFE source changed')
    parent=post/'tools/fixed-trt-builds';parent.mkdir(exist_ok=True)
    area=Path(tempfile.mkdtemp(prefix='recipe-v1-',dir=parent))
    bench=area/'bench';models=area/'tools/rife-onnx';reports=area/'reports'
    for folder in (bench,models,reports):folder.mkdir(parents=True)
    os.link(source,models/source.name)
    recipe_names={'prepare_rife_neural_fp16.py':CONVERSION_SHA256,'benchmark_rife_tensorrt.py':BUILD_SHA256}
    for name,expected in recipe_names.items():
        original=repo/'scripts/recipes'/name
        if sha256(original)!=expected:raise ValueError('Archived fixed recipe changed: '+name)
        shutil.copy2(original,bench/name)
    shutil.copy2(Path(__file__),area/'bootstrap-source.py')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=avg_frame_rate','-of','json',str(master)]))['streams'][0]
    from frame_clock import input_rate
    rate=int(input_rate(probe['avg_frame_rate']));frames=[]
    for number,index in enumerate((53*rate,53*rate+1),1):
        image=bench/f'rife_pair_{number:02d}.png'
        command=['ffmpeg','-nostdin','-v','info','-n','-copyts','-seek_timestamp','1','-ss',str(index/rate),
            '-i',str(master),'-map','0:v:0','-frames:v','1','-vf','trim=end_frame=1,showinfo',
            '-fps_mode','passthrough',str(image)]
        result=subprocess.run(command,capture_output=True,text=True,check=True)
        log=bench/f'rife_pair_{number:02d}.decode.log';log.write_text(result.stderr)
        pts=showinfo_frames(result.stderr)
        if len(pts)!=1 or abs(pts[0]['seconds']-index/rate)>.0001:
            raise ValueError('Actual native footage frame identity failed')
        frames.append(dict(path=str(image),sha256=sha256(image),source_frame=index,
                           source_pts=pts[0],decode_log=str(log),decode_log_sha256=sha256(log)))
    pair=reports/'footage-pair.json';atomic_json(pair,dict(master_sha256=digest,master_path=str(master),input_rate=rate,frames=frames))
    python=post/'face-venv/bin/python'
    run([str(python),str(bench/'prepare_rife_neural_fp16.py')],reports/'conversion.log')
    typed=models/'rife_v4.6.neural-fp16.onnx'
    if sha256(typed)!=MODEL_SHA256:raise ValueError('Fixed conversion did not reproduce the established typed graph SHA')
    audit=audit_typed_model(typed);atomic_json(reports/'typed-graph-audit.json',audit)
    engine=models/'rife_v4.6.sm75.engine';benchmark=reports/'benchmark.json'
    environment=os.environ.copy();environment['CUDA_VISIBLE_DEVICES']='1'
    began=time.time()
    run([str(python),str(bench/'benchmark_rife_tensorrt.py'),'--model',str(typed),
         '--reference',str(models/source.name),'--output',str(benchmark),'--engine',str(engine)],
        reports/'build-benchmark.log',environment,guard_gpu=True)
    conversion=reports/'rife_neural_fp16_conversion.json'
    proof=dict(schema=2,engine_sha256=sha256(engine),typed_model_sha256=MODEL_SHA256,
        source_model_sha256=SOURCE_SHA256,conversion_recipe_sha256=CONVERSION_SHA256,
        tensorrt_version=TRT_VERSION,torch_version=actual_versions['torch'],driver_version=DRIVER_VERSION,
        compiled_gpu='Tesla T10',compute_capability=[7,5],physical_gpu_index=1,strongly_typed=True,
        input_shape=[1,7,1080,1920],output_shape=[1,3,1080,1920],
        source_model=str(models/source.name),conversion_recipe=str(bench/'prepare_rife_neural_fp16.py'),
        workflow_lock=str(lock),workflow_lock_sha256=sha256(lock),footage_pair_manifest=str(pair),
        footage_pair_manifest_sha256=sha256(pair),build_started_at=began,
        historical_engine_sha_is_not_reused=True)
    for key,path in [('build_recipe',bench/'benchmark_rife_tensorrt.py'),('benchmark_report',benchmark),('conversion_report',conversion)]:
        proof[key]=str(path);proof[key+'_sha256']=sha256(path)
    provenance=reports/'provenance.json';atomic_json(provenance,proof)
    assets=[source,models/source.name,typed,engine,provenance,conversion,benchmark,pair,lock,
            bench/'prepare_rife_neural_fp16.py',bench/'benchmark_rife_tensorrt.py',*[Path(row['path']) for row in frames]]
    cfg=dict(rife_backend='cuda_trt',rife_trt_engine=str(engine),rife_trt_model=str(typed),
        rife_trt_provenance=str(provenance),rife_onnx=str(source),gpu=1,gpu_name='Tesla T10',
        encoder_gpu=1,rife_precision='float32',nvidia_icd='/usr/share/vulkan/icd.d/nvidia_icd.json',
        file_sha256={str(path):sha256(path) for path in assets})
    validate_assets(cfg)
    if profile.exists():
        history=profile.parent/'trt-rebuild-history';history.mkdir(exist_ok=True)
        saved=history/(sha256(profile)+'.json')
        if not saved.exists():saved.write_bytes(profile.read_bytes())
    atomic_json(profile,cfg)
    result=dict(state='FIXED_TRT_REBUILT_AND_NUMERICALLY_BENCHMARKED',config=str(profile),
        engine_sha256=sha256(engine),typed_model_sha256=sha256(typed),provenance=str(provenance),
        build_directory=str(area),activated=False,current_master_fullchain_checked=False)
    atomic_json(post/'reports/fixed-trt-rebuild.json',result)
    print(json.dumps(result));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',required=True,type=Path);parser.add_argument('--master',required=True,type=Path)
    args=parser.parse_args();bootstrap(args.post,args.master)
