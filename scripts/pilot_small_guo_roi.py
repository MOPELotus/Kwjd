#!/usr/bin/env python3
"""Three preserved PNGs only: bounded tiny-Guo ROI/GF pilot, no video changes."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--weights',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();work=args.work.resolve()
    if args.output.exists():raise FileExistsError('Preserve previous pilot assets')
    active=subprocess.check_output(['systemctl','--user','show',
        'kwjd-face-rework-e49-20261028.service','-p','ActiveState','--value'],text=True).strip()
    if active!='inactive':raise RuntimeError('WAIT_GPU: full rework service has not completed')
    plan=json.loads((work/'plan.json').read_text());frozen=work/'frozen_61ef57a'
    for name in ['face_frame_processor.py','render_face_samples.py']:
        if digest(frozen/name)!=plan['bundle']['code'][name]:raise ValueError('Frozen processor changed')
    samples=work/'audit-gap-0216/visual-samples'
    manifest=json.loads((samples/'manifest.json').read_text());inputs=[]
    for second in [2237,2245,2252]:
        pair={}
        for kind in ['source','rework']:
            path=samples/f'{second}-{kind}.png';sha=digest(path)
            if not any(c['second']==second and c['kind']==kind and c['sha256']==sha
                       for c in manifest['captures']):raise ValueError('Unbound preserved PNG')
            pair[kind]=dict(path=str(path),sha256=sha)
        inputs.append(dict(second=second,**pair))
    args.output.mkdir(parents=True)
    sys.path.insert(0,str(frozen))
    import cv2
    import numpy as np
    import torch
    from PIL import Image,ImageDraw
    from face_frame_processor import GFPGANProcessor,WEIGHTS
    torch.set_num_threads(4);cv2.setNumThreads(4)
    started=time.monotonic();processor=GFPGANProcessor(args.weights,protected_bottom=760)
    init_seconds=time.monotonic()-started;helper=processor.helper
    if helper.pad_blur or helper.template_3points:raise ValueError('Unexpected helper geometry')
    roi=[1480,500,240,240];resize=960;threshold=5;confidence=.97;detections=[]

    def local_landmarks(*_args,**_kwargs):
        x,y,width,height=roi;image=helper.input_img[y:y+height,x:x+width]
        scale=min(image.shape[:2])/resize
        resized=cv2.resize(image,(int(width/scale),int(height/scale)),interpolation=cv2.INTER_LANCZOS4)
        with torch.inference_mode():boxes=helper.face_det.detect_faces(resized,confidence)
        current=[]
        for raw in boxes:
            box=raw.copy()
            box[[0,2,5,7,9,11,13]]=box[[0,2,5,7,9,11,13]]*scale+x
            box[[1,3,6,8,10,12,14]]=box[[1,3,6,8,10,12,14]]*scale+y
            eye=float(np.linalg.norm([box[5]-box[7],box[6]-box[8]]))
            current.append(dict(box_and_landmarks=box.tolist(),eye_distance_original_pixels=eye,accepted=eye>=threshold))
            if eye>=threshold:
                helper.det_faces.append(box[:5]);helper.all_landmarks_5.append(box[5:].reshape(5,2))
        detections.append(current)
        return len(helper.det_faces)

    helper.get_face_landmarks_5=local_landmarks
    recipe=dict(parent_fingerprint=plan['fingerprint'],roi_xywh=roi,resize_short_side=resize,
        confidence=confidence,eye_distance_original_pixels=threshold,protected_bottom=760,
        code_sha256=digest(__file__),frozen_processor_sha256=digest(frozen/'face_frame_processor.py'),
        weights=WEIGHTS,scope='Three preserved full-frame PNGs only; pre-encode GF output, no media edits.')
    fingerprint=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
    report=dict(at=datetime.now().astimezone().isoformat(),recipe=recipe,fingerprint=fingerprint,
        physical_gpu=torch.cuda.get_device_name(0),device_uuid=str(torch.cuda.get_device_properties(0).uuid),
        model_init_seconds=init_seconds,samples=[],video_changed=False,final_acceptance=False)
    try:
        for pair in inputs:
            before=cv2.imread(pair['source']['path']);start=time.monotonic()
            if before.shape!=(1080,1920,3):raise ValueError('Expected native source PNG')
            result,stats=processor.process_bgr(before)
            if not np.array_equal(result[760:],before[760:]):raise ValueError('Subtitle strip changed')
            output=args.output/f"{pair['second']}-proposal.png"
            if not cv2.imwrite(str(output),result):raise RuntimeError('PNG write failed')
            contact=Image.new('RGB',(2160,750));draw=ImageDraw.Draw(contact)
            for column,(label,path) in enumerate([('source',pair['source']['path']),
                    ('current encoded rework',pair['rework']['path']),('ROI pilot before encode',output)]):
                with Image.open(path) as image:
                    crop=image.crop((roi[0],roi[1],roi[0]+roi[2],roi[1]+roi[3]))
                    contact.paste(crop.resize((720,720),Image.Resampling.NEAREST),(column*720,30))
                draw.text((column*720+5,5),f"{pair['second']}s {label} |3x nearest ROI",fill='white')
            preview=args.output/f"{pair['second']}-comparison.png";contact.save(preview)
            report['samples'].append(dict(**pair,proposal=dict(path=str(output),sha256=digest(output)),
                comparison=dict(path=str(preview),sha256=digest(preview)),detections=detections[-1],
                statistics=stats,seconds=time.monotonic()-start))
    finally:
        with (args.output/'report.json').open('x') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(report=str(args.output/'report.json'),
        restored=[x['statistics']['faces_restored'] for x in report['samples']],video_changed=False)))


if __name__=='__main__':main()
