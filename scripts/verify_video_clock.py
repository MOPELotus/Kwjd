#!/usr/bin/env python3
"""Bounded native-frame face matching across a masked master and source.

CPU Haar detection only; no restoration, encoding or pixel modification.
"""
import argparse
import json
from pathlib import Path
import subprocess
import cv2
import numpy as np
from frame_clock import input_rate


def check(source,master,offset):
    def rate(path):
        data=json.loads(subprocess.check_output(['ffprobe','-v','error',
            '-select_streams','v:0','-show_entries','stream=avg_frame_rate,r_frame_rate',
            '-of','json',str(path)]))['streams'][0]
        actual=input_rate(data['avg_frame_rate'])
        if input_rate(data['r_frame_rate'])!=actual:
            raise ValueError('Native clock review requires matching declared CFR rates')
        return int(actual)
    source_rate,master_rate=rate(source),rate(master)
    expected_index=master_rate//5
    window_count=expected_index*2+1
    if not hasattr(cv2,'CascadeClassifier'):
        raise RuntimeError('Clock review requires an OpenCV runtime with CascadeClassifier; keep the active inference environment unchanged')
    detector=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
    if detector.empty():raise ValueError('Native clock face detector asset is unavailable')
    def frames(path,start,count):
        raw=subprocess.check_output(['ffmpeg','-v','error','-nostdin','-ss',str(start),'-i',str(path),
             '-frames:v',str(count),'-vf','scale=960:540,format=gray','-f','rawvideo','-'])
        return np.frombuffer(raw,np.uint8).reshape(-1,540,960)
    rows=[]
    for anchor in [60,900,1200,1500,1800,2100,2400,2700,3000,3300,3500]:
        image=frames(source,anchor,1)[0];candidates=frames(master,anchor+offset-.2,window_count)
        if len(candidates)!=window_count:raise ValueError('Incomplete native clock window')
        faces=detector.detectMultiScale(image,1.1,4,minSize=(35,35))
        if not len(faces):continue
        # Haar boxes can include scenery beside a cheek. Compare the central
        # face interior to reduce sensitivity to the replaced background.
        x,y,w,h=max(faces,key=lambda box:box[2]*box[3]);pad=max(2,int(min(w,h)*.25))
        patch=image[y+pad:y+h-pad,x+pad:x+w-pad].astype(float);patch-=patch.mean()
        scores=[]
        for candidate in candidates:
            area=candidate[y+pad:y+h-pad,x+pad:x+w-pad].astype(float);area-=area.mean()
            denominator=np.linalg.norm(area)*np.linalg.norm(patch)
            scores.append(float(np.sum(area*patch)/denominator) if denominator else 0)
        best=int(np.argmax(scores))
        rows.append(dict(anchor=anchor,face=[int(v) for v in [x,y,w,h]],
                         face_patch=[int(v) for v in [x+pad,y+pad,w-2*pad,h-2*pad]],
                         expected_index=expected_index,best_index=best,scores=scores,
                         match=abs(best-expected_index)/master_rate<=1/source_rate+1e-9 and scores[best]>=.96))
    passing=[row for row in rows if row['match']]
    return dict(offset_seconds=offset,source_frame_rate=source_rate,master_frame_rate=master_rate,
                method=f'Central face interior versus{window_count} native{master_rate} master frames;25percent inset; tolerance one source frame; no generative model',
                rows=rows,verified=len(passing)>=3 and max(r['anchor'] for r in passing)-min(r['anchor'] for r in passing)>=1500)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--master',type=Path,required=True)
    parser.add_argument('--offset',type=float,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=check(args.source,args.master,args.offset)
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    raise SystemExit(0 if result['verified'] else 2)
