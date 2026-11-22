"""Verify locked installation from an empty private venv; no model service.

Runs actual two-GPU frame inference after installation, then releases only its
own private test directory. Main runtime, original media and finals are retained.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from enhance_stage import atomic_json,sha256,check_space
from fixed_workflow import REQUIREMENTS


def smoke(root,test_post,output):
    from fixed_workflow import verify_fixed_environment
    import numpy as np
    import torch
    from PIL import Image
    from face_frame_processor import GFPGANProcessor
    from cuda_trt import CudaRife
    output.mkdir(parents=True,exist_ok=True)
    cfg=json.loads((root/'post/backend/runtime.trt.json').read_text())
    provenance=json.loads(Path(cfg['rife_trt_provenance']).read_text())
    pairs=json.loads(Path(provenance['footage_pair_manifest']).read_text())['frames']
    face=GFPGANProcessor(test_post/'tools/face-candidates/weights');rife=CudaRife(cfg)
    tensors=[];details=[]
    for number,row in enumerate(pairs):
        image=np.asarray(Image.open(row['path']).convert('RGB'))
        restored,detail=face.process_bgr(image[:,:,::-1].copy())
        if detail['faces_restored']<1:raise ValueError('Clean runtime did not actually restore a face')
        rgb=restored[:,:,::-1].copy();target=output/f'face-{number}.png';Image.fromarray(rgb).save(target)
        tensors.append(torch.from_numpy(rgb).permute(2,0,1).unsqueeze(0).to('cuda:1',dtype=torch.float32)/255)
        details.append({'source_sha256':row['sha256'],'restored_sha256':sha256(target),'coverage':detail})
    with torch.inference_mode(),torch.cuda.device(1):result=rife.interpolate(*tensors,.5)
    if tuple(result.shape)!=(1,3,1080,1920) or not torch.isfinite(result).all():
        raise ValueError('Clean runtime RIFE output invalid')
    target=output/'rife.png';Image.fromarray((result[0].permute(1,2,0).clamp(0,1)*255).round().byte().cpu().numpy()).save(target)
    report=dict(state='ACTUAL_CLEAN_RUNTIME_GPU_SMOKE_PASSED',packages=verify_fixed_environment(),
        physical_devices=[torch.cuda.get_device_name(i) for i in (0,1)],
        faces=details,rife_output_sha256=sha256(target),engine_sha256=provenance['engine_sha256'],
        scope='Actual two-frame GFPGAN and one RIFE midpoint in freshly installed locked environment; not full-episode acceptance')
    atomic_json(output/'smoke.json',report)


def verify(root):
    root=root.resolve(strict=True);reports=root/'post/reports/fixed-clean-recovery';reports.mkdir(parents=True,exist_ok=True)
    status=root/'post/reports/fixed-clean-recovery.json'
    with (reports/'selftest.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        check_space(Path('/var/tmp'),22*2**30,25)
        area=Path(tempfile.mkdtemp(prefix='kwjd-fixed-clean-check-',dir='/var/tmp'))
        token=str(uuid.uuid4());atomic_json(area/'owner.json',dict(token=token,root=str(root),created_at=time.time()))
        test_post=area/'post';test_post.mkdir()
        state=dict(schema_version=1,state='RUNNING_CLEAN_INSTALL',started_at=time.time(),
            test_root=str(area),requirements_sha256=sha256(REQUIREMENTS),original_media_untouched=True,
            main_runtime_untouched=True,temporary_artifact_manifest={'path':str(area),'owner_token':token,
                'kind':'private_generated_clean_environment_test','cleanup_after_verified_smoke':True})
        atomic_json(status,state)
        try:
            command=[sys.executable,str(Path(__file__).with_name('bootstrap_face_runtime.py')),'--post',str(test_post)]
            with (reports/'install.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
            state.update(state='RUNNING_CLEAN_RUNTIME_GPU_SMOKE',installed_at=time.time());atomic_json(status,state)
            python=test_post/'face-venv/bin/python'
            with (reports/'pip-check.log').open('w') as log:
                subprocess.run([str(python),'-m','pip','check'],stdout=log,stderr=subprocess.STDOUT,check=True)
            with (reports/'smoke.log').open('w') as log:
                subprocess.run([str(python),str(Path(__file__).resolve()),'--root',str(root),
                    '--smoke-only','--test-post',str(test_post),'--output',str(reports/'gpu-smoke')],
                    stdout=log,stderr=subprocess.STDOUT,check=True)
            proof=reports/'gpu-smoke/smoke.json'
            if json.loads(proof.read_text()).get('state')!='ACTUAL_CLEAN_RUNTIME_GPU_SMOKE_PASSED':
                raise ValueError('Clean inference proof missing')
            state.update(state='PASSED',completed_at=time.time(),smoke_path=str(proof),smoke_sha256=sha256(proof),
                         pip_check_sha256=sha256(reports/'pip-check.log'),temporary_test_runtime_released=False)
            atomic_json(status,state)
            # This exact newly created private directory contains only our test.
            # Parent runs from the repository; the test interpreter already exited.
            if (area.parent!=Path('/var/tmp') or not area.name.startswith('kwjd-fixed-clean-check-')
                    or area.is_symlink() or area.stat().st_uid!=os.getuid()
                    or json.loads((area/'owner.json').read_text()).get('token')!=token):
                raise ValueError('Private test cleanup identity failed; retained')
            shutil.rmtree(area)
            state['temporary_test_runtime_released']=True;atomic_json(status,state)
            atomic_json(reports/'cleanup.json',dict(state='OWN_PRIVATE_TEST_RELEASED',path=str(area),
                owner_token=token,smoke_sha256=sha256(proof),main_runtime_preserved=True))
        except Exception as error:
            state.update(state='FAILED_RETAINED_FOR_INSPECTION',error=repr(error),failed_at=time.time())
            atomic_json(status,state);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.home()/'qingqingwu')
    parser.add_argument('--smoke-only',action='store_true');parser.add_argument('--test-post',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.smoke_only:smoke(args.root,args.test_post,args.output)
    else:verify(args.root)
