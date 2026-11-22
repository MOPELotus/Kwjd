"""Bounded actual-footage checks for recovered GFPGAN and CUDA ONNX RIFE."""
import argparse
import json
import subprocess
import time
from pathlib import Path

from face_frame_processor import GFPGANProcessor
from cuda_rife import CudaRife,MODEL_SHA256


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--post',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    (Path.home()/'qingqingwu/post-home').mkdir(parents=True,exist_ok=True)
    import torch,numpy as np,cv2
    torch.set_num_threads(4);cv2.setNumThreads(4)
    model=args.post/'tools/rife-onnx/rife_v4.6.onnx'
    face=GFPGANProcessor(args.post/'tools/face-candidates/weights')
    restored=[];metrics=[]
    for index in range(2):
        path=args.output/f'input-{index}.png'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-threads','2','-n','-ss',str(300+index/25),
                        '-i',str(args.source),'-frames:v','1',str(path)],check=True)
        image=cv2.imread(str(path));start=time.monotonic()
        result,report=face.process_bgr(image)
        report['seconds']=time.monotonic()-start
        if report['faces_restored']<1:raise ValueError('No face exercised')
        cv2.imwrite(str(args.output/f'face-{index}.png'),result)
        restored.append(result);metrics.append(report)
    rife=CudaRife({'gpu':1,'gpu_name':'Tesla T10','rife_onnx':str(model),
                   'file_sha256':{str(model):MODEL_SHA256}})
    a,b=[torch.from_numpy(np.ascontiguousarray(image[:,:,::-1].transpose(2,0,1))).unsqueeze(0).float().to('cuda:1')/255
         for image in restored]
    start=time.monotonic();middle=rife.interpolate(a,b,.5)
    result=(middle[0].permute(1,2,0).cpu().numpy()*255).round().astype(np.uint8)[:,:,::-1]
    cv2.imwrite(str(args.output/'rife-middle.png'),result)
    report={'state':'BOUNDED_MODELS_TESTED_NOT_PRODUCTION_BACKEND','face':metrics,
            'rife_seconds':time.monotonic()-start,'rife_provider_verified':rife.provider_verified,
            'shape':list(result.shape),'torch':torch.__version__,'opencv':cv2.__version__,
            'pipeline_activated':False,'actual_master_calibrated':False}
    (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report))


if __name__=='__main__':main()
