"""Exact candidate input/output clocks; metadata checks never activate a backend."""
from fractions import Fraction
import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

BACKGROUND = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def input_rate(value):
    rate=Fraction(value)
    if rate not in (25,30):raise ValueError('Only explicitly verified CFR25/CFR30 candidates')
    return rate


def output_count(count,rate=25):
    if type(count)is not int or count<0:raise ValueError('Invalid source frame count')
    return math.ceil(count*Fraction(60,1)/input_rate(rate))


def frame_position(index,rate=25):
    if type(index)is not int or index<0:raise ValueError('Invalid output frame index')
    position=index*input_rate(rate)/60
    left=position.numerator//position.denominator
    return left,position-left


def verify(path,rate):
    rate=input_rate(rate)
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_streams','-show_packets','-show_entries',
        'stream=time_base,r_frame_rate,avg_frame_rate,nb_frames:packet=pts','-of','json',str(path)], **BACKGROUND))
    video=data['streams'][0]
    if Fraction(video['r_frame_rate'])!=rate or Fraction(video['avg_frame_rate'])!=rate:
        raise ValueError('Metadata rate mismatch')
    base=Fraction(video['time_base']);stamps=sorted(int(p['pts']) for p in data['packets'])
    if not stamps or stamps[0]!=0 or any((b-a)*base!=1/rate for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Actual packet clock is not exact continuous zero-origin CFR')
    count=len(stamps)
    if video.get('nb_frames') not in (None,'N/A') and int(video['nb_frames'])!=count:
        raise ValueError('Declared/actual frame count differs')
    with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    target=output_count(count,rate)
    return {'master_sha256':digest,'input_rate':str(rate),'input_packets':count,
            'output_rate':60,'expected_output_frames':target,'input_seconds':float(count/rate),
            'output_seconds':target/60,'packet_clock_verified':True,
            'production_backend_admitted':False,'neural_pipeline_executed':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--file',type=Path,required=True)
    p.add_argument('--verify-rate',required=True);p.add_argument('--report',type=Path,required=True)
    args=p.parse_args();result=verify(args.file,args.verify_rate)
    with args.report.open('x',encoding='utf8') as out:json.dump(result,out,indent=2)
    print(json.dumps(result))
