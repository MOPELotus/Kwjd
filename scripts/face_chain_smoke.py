#!/usr/bin/env python3
"""Bounded native25 -> none/CAS -> GFPGAN -> TRT RIFE60 -> ASS -> NVENC.

Isolated experiment; never changes the production backend or episode receipts.
One video decode, streaming CPU BGR fusion, one final lossy video encode.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from collections import defaultdict

from enhance_backend import frame_position, output_count, is_cut
from face_frame_processor import GFPGANProcessor, WEIGHTS
from render_face_samples import digest, now, save, probe, pcm_update

NATIVE_SHA = 'e224ef25d8f9f4d600e02a31344f6c09f730e5cc1459e9ef53a115091a6c98cf'
FINAL_SHA = 'f2bae90ba1b50bc7953867433c25b6a70be233828f14257fe646c975e8b4d3fc'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('native','audio-source','ass','fonts','weights','rife-config','output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--frames', type=int, default=150)
    parser.add_argument('--start-frame', type=int, default=350)
    args = parser.parse_args()
    if not 5 <= args.frames <= 750 or args.frames % 5 or args.start_frame < 0 or args.start_frame+args.frames+1 > 1500:
        parser.error('Bounded native25 clock required: 5..750frames and one lookahead within retained1500frames')
    if args.output.exists():
        raise FileExistsError('Refusing existing audition directory')
    if shutil.disk_usage(args.output.parent).free < 26*2**30:
        raise RuntimeError('Requires25GiB floor plus1GiB bounded budget')
    args.output.mkdir()
    lock=(args.output.parent/'.native-face-chain.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    if digest(args.native) != NATIVE_SHA or digest(args.audio_source) != FINAL_SHA:
        raise ValueError('Changed native cache or mixed-audio source')
    import torch
    import cv2
    import numpy as np
    from PIL import Image
    from cuda_trt import CudaRife
    torch.set_num_threads(4); cv2.setNumThreads(4)
    def preflight():
        rows=subprocess.check_output(['nvidia-smi','--query-gpu=index,temperature.gpu,memory.free','--format=csv,noheader,nounits'],text=True)
        for row in rows.splitlines():
            i,temp,free=map(int,row.split(','))
            if temp >= (95 if i==0 else 85) or free < (5120 if i==0 else 3072):
                raise RuntimeError('Model initialization requires GPU headroom/cooling: '+rows)
    preflight()
    cfg=json.loads(args.rife_config.read_text())
    initialized=time.monotonic()
    processor=GFPGANProcessor(args.weights)
    gf_init=time.monotonic()-initialized
    # GPU0 is already occupied by our admitted face model at this point.
    row=subprocess.check_output(['nvidia-smi','--id=1','--query-gpu=temperature.gpu,memory.free','--format=csv,noheader,nounits'],text=True)
    temp,free=map(int,row.strip().split(','))
    if temp>=85 or free<3072:raise RuntimeError('TRT initialization requires T10 headroom/cooling')
    initialized=time.monotonic(); rife=CudaRife(cfg); rife_init=time.monotonic()-initialized
    torch.cuda.synchronize(1)
    physical=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,uuid,driver_version,compute_cap','--format=csv,noheader'],text=True)
    report={'started_at':now(),'status':'running','scope':__doc__,'native_sha256':NATIVE_SHA,
        'native_origin_seconds':45,'start_frame':args.start_frame,'input_frames':args.frames,
        'lookahead_frames':1,'output_frames':output_count(args.frames),'native_fps':25,'output_fps':60,
        'gf_init_seconds':gf_init,'rife_init_seconds':rife_init,'physical_devices':physical,
        'audio_source_sha256':FINAL_SHA,'ass_sha256':digest(args.ass),
        'font_sha256':{p.name:digest(p) for p in args.fonts.glob('*.ttf')},'gf_weights':WEIGHTS,
        'source_files':{name:digest(Path(__file__).with_name(name)) for name in
             ['face_chain_smoke.py','face_frame_processor.py','cuda_trt.py','enhance_backend.py']},
        'rife_config_sha256':digest(args.rife_config),'variants':[],
        'limitations':'Bounded original-master native25 cache; not a full-episode timing/quality guarantee. GF fusion returns through CPU; no ROI optimization. Native video cache contains no audio; exact original mix sourced from preserved delivered final.'}
    fingerprint=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    report['experiment_fingerprint']=fingerprint
    save(args.output/'report.json',report)
    original_time=45+args.start_frame/25; seconds=args.frames/25
    expected=hashlib.sha256();pcm_update(args.audio_source,expected,original_time,seconds)
    report['expected_audio_pcm_sha256']=expected.hexdigest()
    last_check=0
    def guard():
        nonlocal last_check
        if time.monotonic()-last_check < 2:return
        rows=subprocess.check_output(['nvidia-smi','--query-gpu=index,temperature.gpu,memory.free','--format=csv,noheader,nounits'],text=True)
        for row in rows.splitlines():
            i,temp,free=map(int,row.split(','))
            if temp >= (95 if i==0 else 85) or free < (3072 if i==0 else 1024):
                raise RuntimeError('GPU headroom/temperature wait required: '+rows)
        if shutil.disk_usage(args.output).free < 25*2**30:raise RuntimeError('Disk floor reached')
        last_check=time.monotonic()
    try:
        for variant in ['none','clarity']:
            folder=args.output/variant;folder.mkdir();partial=folder/'sample.partial.mkv'
            light=('format=yuv444p,cas=strength=.6:planes=1,eq=contrast=1.03:saturation=1.02,' if variant=='clarity' else '')
            filters=light+'scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=bgr24'
            dec=['ffmpeg','-nostdin','-v','error','-xerror','-threads','4','-filter_threads','4',
                '-ss',str(args.start_frame/25),'-i',str(args.native),'-map','0:v:0','-an',
                '-vf',filters,'-frames:v',str(args.frames+1),'-fps_mode','passthrough','-f','rawvideo','-']
            # PTS shift lets unchanged approved ASS retain its original program clock.
            for path in [args.ass,args.fonts]:
                if any(x in str(path) for x in "'\\:[],;"):
                    raise ValueError('Use paths safe for the owned FFmpeg filter expression')
            vf=(f"setpts=PTS+{original_time}/TB,ass=filename={args.ass}:fontsdir={args.fonts},"
                'setpts=PTS-STARTPTS,scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=p010le')
            enc=['ffmpeg','-nostdin','-v','verbose','-n','-threads','4','-filter_threads','4',
                '-f','rawvideo','-pixel_format','rgb24','-video_size','1920x1080','-framerate','60','-i','pipe:0',
                '-ss',str(original_time),'-i',str(args.audio_source),'-t',str(seconds),'-map','0:v:0','-map','1:a:0',
                '-vf',vf,'-c:v','hevc_nvenc','-gpu','1','-preset','p6','-tune','hq','-profile:v','main10',
                '-rc','vbr','-cq','16','-b:v','0','-maxrate','16M','-bufsize','32M',
                '-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709','-color_range','tv',
                '-c:a','flac','-sample_fmt','s32','-bits_per_raw_sample','24','-ar','48000',str(partial)]
            entry={'name':variant,'status':'running','decode_command':dec,'encode_command':enc,
                'input_frames_processed':0,'restored_frames':0,'no_detection_frames':0,'failed_frames':0,
                'interpolated_frames':0,'cut_intervals':[],'output_frames':0}
            report['variants'].append(entry);save(args.output/'report.json',report)
            times=defaultdict(float); start=time.monotonic(); decoder=encoder=None
            try:
                with (folder/'decode.log').open('w') as dl,(folder/'encode.log').open('w') as el,(folder/'frames.jsonl').open('w') as fl:
                    decoder=subprocess.Popen(dec,stdout=subprocess.PIPE,stderr=dl)
                    encoder=subprocess.Popen(enc,stdin=subprocess.PIPE,stdout=el,stderr=el)
                    def read():
                        guard(); t=time.monotonic(); raw=decoder.stdout.read(1920*1080*3)
                        if len(raw)!=1920*1080*3:raise ValueError('Truncated native decoded frame')
                        img=np.frombuffer(raw,dtype=np.uint8).reshape(1080,1920,3).copy();times['decode_read']+=time.monotonic()-t
                        t=time.monotonic();result,detail=processor.process_bgr(img);times['face_total']+=time.monotonic()-t
                        for key,value in detail['times'].items():times['face_'+key]+=value
                        n=entry['input_frames_processed'];fl.write(json.dumps(dict(frame=n,**detail))+'\n')
                        entry['input_frames_processed']+=1
                        entry['restored_frames' if detail['faces_detected'] else 'no_detection_frames']+=1
                        if n in [0,9,25,149]:
                            cv2.imwrite(str(folder/f'{n:04d}_native_filtered.png'),img)
                            cv2.imwrite(str(folder/f'{n:04d}_face_before_rife_ass.png'),result)
                        t=time.monotonic()
                        rgb=np.ascontiguousarray(result[:,:,::-1])
                        tensor=torch.from_numpy(rgb).permute(2,0,1).unsqueeze(0).to(rife.device,dtype=torch.float32)/255
                        torch.cuda.synchronize(1);times['cpu_to_rife']+=time.monotonic()-t
                        return Image.fromarray(img[:,:,::-1]),tensor
                    previous,a=read()
                    with torch.inference_mode():
                        for index in range(args.frames):
                            following,b=read();t=time.monotonic();cut=is_cut(previous,following);times['cut_detection']+=time.monotonic()-t
                            if cut:entry['cut_intervals'].append(index)
                            while entry['output_frames']<output_count(args.frames):
                                left,alpha=frame_position(entry['output_frames'])
                                if left!=index:break
                                t=time.monotonic()
                                if alpha and not cut:
                                    frame=rife.interpolate(a,b,float(alpha));entry['interpolated_frames']+=1
                                else:frame=a
                                torch.cuda.synchronize(1);times['rife']+=time.monotonic()-t
                                t=time.monotonic();pixels=(frame[0].permute(1,2,0)*255).round().clamp_(0,255).to(torch.uint8).contiguous().cpu().numpy()
                                times['output_transfer']+=time.monotonic()-t
                                t=time.monotonic();encoder.stdin.write(pixels.tobytes());times['encode_pipe']+=time.monotonic()-t
                                entry['output_frames']+=1
                            previous,a=following,b
                            if index%25==24:
                                entry['progress_seconds']=time.monotonic()-start;save(args.output/'report.json',report)
                                print(json.dumps({'variant':variant,'input_processed':index+1,'total':args.frames,'output':entry['output_frames']}),flush=True)
                    t=time.monotonic();encoder.stdin.close()
                    if decoder.stdout.read(1) or decoder.wait() or encoder.wait():raise RuntimeError('FFmpeg chain failed')
                    times['encode_flush']=time.monotonic()-t
                entry['pipeline_seconds']=time.monotonic()-start;entry['stage_seconds']=dict(times)
                entry['fps_native']=args.frames/entry['pipeline_seconds'];entry['fps_output']=entry['output_frames']/entry['pipeline_seconds']
                from face_rework_worker import validate_video
                entry['validation']=validate_video(partial,output_count(args.frames),folder,audio=True)
                actual=hashlib.sha256();pcm_update(partial,actual)
                if actual.hexdigest()!=expected.hexdigest():raise ValueError('Original mixed PCM differs')
                entry['audio_pcm_sha256']=actual.hexdigest()
                fontlog=(folder/'encode.log').read_text()
                if 'MiSans-Subtitle-Demibold' not in fontlog:raise ValueError('Expected actual libass font resolution missing')
                entry['font_resolution_verified']=True
                destination=folder/'sample.mkv';partial.rename(destination);entry['path']=str(destination)
                entry['status']='verified_pending_visual_review';save(args.output/'report.json',report)
            finally:
                for proc in [decoder,encoder]:
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                        try:proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        report['status']='verified_pending_visual_review'
    except BaseException as error:
        report.update(status='failed',error=repr(error));raise
    finally:
        report['completed_at']=now();save(args.output/'report.json',report)


if __name__=='__main__':main()
