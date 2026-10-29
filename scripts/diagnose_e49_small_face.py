#!/usr/bin/env python3
"""Bounded, detection-only investigation of E49's preserved tiny-face samples.

Never restores a face, writes media, modifies receipts, or starts another worker.
Run after the full rework service has released its GPU. Findings are not approval.
"""
import argparse
from datetime import datetime
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--roi', type=int, nargs=4, metavar=('X','Y','W','H'),
                        help='Optional bounded original-pixel crop for detection only')
    args = parser.parse_args()
    if args.roi:
        x,y,width,height=args.roi
        if not (0<=x<1920 and 0<=y<1080 and width>=32 and height>=32
                and x+width<=1920 and y+height<=1080):
            raise ValueError('ROI must be inside preserved native source frame')
    if args.report.exists():
        raise FileExistsError('Preserve the prior diagnostic report')
    unit = 'kwjd-face-rework-e49-20261028.service'
    active = subprocess.check_output(['systemctl', '--user', 'show', unit,
                                     '-p', 'ActiveState', '--value'], text=True).strip()
    if active not in ('inactive', 'failed'):
        raise RuntimeError('WAIT_GPU: full rework service has not terminated')
    lease = (args.work.parent.parent / '.face-compute.lock').open('a+')
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if shutil.disk_usage(args.work).free < 25 * 2**30:
        raise RuntimeError('WAIT_DISK: preserve 25GiB floor')
    weights = args.weights / 'detection_Resnet50_Final.pth'
    expected = '6d1de9c2944f2ccddca5f5e010ea5ae64a39845a86311af6fdf30841b0a5a16d'
    if digest(weights) != expected:
        raise ValueError('Detector weight SHA differs')
    samples = args.work / 'audit-gap-0216/visual-samples'
    manifest = json.loads((samples / 'manifest.json').read_text())
    inputs = []
    for second in (2237, 2245, 2252):
        path = samples / f'{second}-source.png'
        sha = digest(path)
        if not any(x.get('second') == second and x.get('kind') == 'source'
                   and x.get('sha256') == sha for x in manifest['captures']):
            raise ValueError('Preserved source sample is not SHA-bound')
        inputs.append((second, path, sha))
    # Masking/reordering CUDA devices would invalidate the physical-device check.
    if os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise ValueError('Run with the original physical CUDA device order')
    import cv2
    import numpy as np
    import torch
    from facexlib.detection import init_detection_model
    import facexlib.detection.retinaface as retinaface
    torch.set_num_threads(4)
    cv2.setNumThreads(4)
    if not torch.cuda.is_available() or torch.cuda.get_device_name(0) != 'NVIDIA RTX A4000':
        raise RuntimeError('Requires physical A4000 at CUDA0')
    free, total = torch.cuda.mem_get_info(0)
    if free < 4.5 * 2**30:
        raise RuntimeError('WAIT_GPU: insufficient measured headroom')
    budget=min(2.5 * 2**30,free-2 * 2**30)
    torch.cuda.set_per_process_memory_fraction(budget / total, 0)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    model = init_detection_model('retinaface_resnet50', half=False,
                                 device=torch.device('cuda:0'), model_rootpath=str(args.weights))
    result = dict(at=datetime.now().astimezone().isoformat(), script_sha256=digest(__file__),
                  weight_sha256=expected, device=torch.cuda.get_device_name(0),
                  device_uuid=str(torch.cuda.get_device_properties(0).uuid),
                  torch=torch.__version__, facexlib=importlib.metadata.version('facexlib'),
                  detector_code_sha256=digest(retinaface.__file__),
                  measured_free_after_context_bytes=free,allocator_budget_bytes=budget,
                  unallocated_headroom_bytes=free-budget,
                  roi_xywh=args.roi,
                  samples=[], final_acceptance=False,
                  scope='Nine detection-only inferences. No GFPGAN, media edits or recipe changes. '
                        'Eye distance is measured after scaling landmarks to original coordinates.')
    for second, path, sha in inputs:
        image = cv2.imread(str(path))
        if image is None or image.shape != (1080, 1920, 3):
            raise ValueError('Expected preserved native1080p source PNG')
        x,y=0,0
        if args.roi:
            x,y,width,height=args.roi
            image=image[y:y+height,x:x+width]
        for resize in (640, 960, 1080):
            temperature = int(subprocess.check_output(['nvidia-smi', '--id=0',
                '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'], text=True).strip())
            if temperature >= 95:
                raise RuntimeError('WAIT_GPU: measured temperature reaches guard')
            scale = min(image.shape[:2]) / resize
            height,width=image.shape[:2]
            resized = cv2.resize(image, (int(width/scale), int(height/scale)), interpolation=cv2.INTER_LANCZOS4)
            started = time.monotonic()
            with torch.inference_mode():
                boxes = model.detect_faces(resized, 0.97)
            torch.cuda.synchronize(0)
            faces = []
            for raw in boxes:
                box=raw.copy()
                # Preserve confidence while mapping coordinates back to the original frame.
                box[[0,2,5,7,9,11,13]]=box[[0,2,5,7,9,11,13]]*scale+x
                box[[1,3,6,8,10,12,14]]=box[[1,3,6,8,10,12,14]]*scale+y
                eye = float(np.linalg.norm([box[5]-box[7], box[6]-box[8]]))
                faces.append(dict(box_and_landmarks=box.tolist(), eye_distance_original_pixels=eye,
                                  passes_original_eye_threshold=eye >= 5))
            result['samples'].append(dict(second=second, source=str(path), source_sha256=sha,
                resize_short_side=resize, confidence_threshold=.97, eye_dist_threshold=5,
                faces=faces, accepted=sum(x['passes_original_eye_threshold'] for x in faces),
                seconds=time.monotonic()-started, temperature=temperature))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'report':str(args.report), 'inferences':len(result['samples']),
                      'accepted':[x['accepted'] for x in result['samples']]}))


if __name__ == '__main__':
    main()
