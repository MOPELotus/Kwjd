#!/usr/bin/env python3
"""Bounded-memory NCNN restoration/RIFE pipeline. Never processes subtitles/audio.

Models and binaries are local, hash-pinned in post/backend/runtime.json. Actual
25fps packet timestamps are validated; output frames use an exact rational60fps
clock. At most55 source frames per block are buffered, never an episode image sequence.
Models reuse their GPU context across each bounded block; benchmark actual masters
before making production throughput claims.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from enhance_stage import check_space, probe, sha256, GIB
from master_policy import reject_retired
from stage_storage import choose_scratch


def frame_position(output_index):
    position = Fraction(output_index * 25, 60)
    left = position.numerator // position.denominator
    return left, position - left


def output_count(source_count):
    return math.ceil(Fraction(source_count * 60, 25))


def is_cut(left, right, threshold=.05, center_threshold=.10):
    import numpy as np
    a = np.asarray(left.resize((96,54)),dtype=np.int16)
    b = np.asarray(right.resize((96,54)),dtype=np.int16)
    difference=np.abs(a-b)/255
    # Replaced scenery stays similar when the original camera jumps wide/close.
    # A whole-frame20% threshold missed an actual cut at59.36s in E49.
    return (float(difference.mean())>=threshold
            or float(difference[5:50,24:72].mean())>=center_threshold)


def config(path):
    data = json.loads(Path(path).read_text())
    if type(data.get('gpu')) is not int or data['gpu'] < 0:
        raise ValueError('Explicit nonnegative physical Vulkan GPU index required; no CPU fallback')
    if data.get('gpu_name') not in ('Tesla T10','NVIDIA RTX A4000'):
        raise ValueError('Physical GPU identity must be configured and verified')
    if data.get('restore') not in (True,False):
        raise ValueError('Explicit restoration policy required')
    if data.get('restoration_backend','ncnn') not in ('ncnn','cuda_srvgg'):
        raise ValueError('Unknown restoration backend; implicit fallbacks forbidden')
    for filename, expected in data['file_sha256'].items():
        if sha256(filename) != expected:
            raise ValueError('Backend/model hash mismatch: '+filename)
    if data.get('rife_backend','ncnn') not in ('ncnn','cuda_onnx','cuda_trt'):
        raise ValueError('Unknown RIFE backend; implicit fallback forbidden')
    needed = ([data['rife_trt_engine'],data['rife_trt_model'],data['rife_trt_provenance']]
              if data.get('rife_backend')=='cuda_trt' else
              [data['rife_onnx']] if data.get('rife_backend')=='cuda_onnx' else
              [data['rife'],str(Path(data['rife_model'])/'flownet.bin'),
              str(Path(data['rife_model'])/'flownet.param')])
    if data['restore'] and data.get('restoration_backend','ncnn') == 'ncnn':
        needed += [data['esrgan'],str(Path(data['esrgan_models'])/'realesrgan-x4plus.bin'),
                   str(Path(data['esrgan_models'])/'realesrgan-x4plus.param')]
    if data['restore'] and data.get('restoration_backend') == 'cuda_srvgg':
        needed += [data['srvgg_source'],data['srvgg_general'],data['srvgg_wdn']]
    if any(n not in data['file_sha256'] for n in needed):
        raise ValueError('All executable/model files must be hash-pinned')
    if not Path(data['nvidia_icd']).is_file():
        raise ValueError('NVIDIA Vulkan ICD missing; software fallback forbidden')
    data['fingerprint'] = hashlib.sha256(Path(path).read_bytes()+Path(__file__).read_bytes()).hexdigest()
    if data.get('restoration_backend') == 'cuda_srvgg':
        data['fingerprint']=hashlib.sha256((data['fingerprint']+sha256(Path(__file__).with_name('cuda_restore.py'))).encode()).hexdigest()
    if data.get('rife_backend') in ('cuda_onnx','cuda_trt'):
        helper='cuda_trt.py' if data['rife_backend']=='cuda_trt' else 'cuda_rife.py'
        for name in (helper,'cuda_pipeline.py'):
            data['fingerprint']=hashlib.sha256((data['fingerprint']+sha256(Path(__file__).with_name(name))).encode()).hexdigest()
    scratch, selection = choose_scratch(Path(path).parent.parent, 8*GIB,
        candidates=None if data.get('scratch') is None else _scratch_candidates(data, path))
    data['scratch'] = str(scratch)
    data['post_root'] = str(Path(path).parent.parent)
    data['storage_selection'] = selection
    return data


def _scratch_candidates(data, path):
    from stage_storage import mounted_scratch_candidates
    yield Path(data['scratch'])
    yield from mounted_scratch_candidates(Path(path).parent.parent)


def source_info(path):
    data = probe(path)
    video = next(s for s in data['streams'] if s['codec_type']=='video')
    if (video['width'],video['height']) != (1920,1080):
        raise ValueError('Only native1920x1080 masters supported')
    if Fraction(video['avg_frame_rate']) != 25 or Fraction(video['r_frame_rate']) != 25:
        raise ValueError('Expected native CFR25; never force input frame rate')
    if video.get('color_primaries') not in (None,'unknown','bt709'):
        raise ValueError('Only SDR Rec709 masters supported; HDR conversion must not be implicit')
    relative_start = float(video.get('start_time',0))-float(data['format'].get('start_time',0))
    if abs(relative_start) > .000001:
        raise ValueError('Nontrivial master start offset requires explicit sync handling')
    return data


def source_packet_count(path):
    raw = subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','packet=pts_time','-of','csv=p=0',str(path)],text=True)
    stamps = sorted(float(line.split(',')[0]) for line in raw.splitlines() if line.strip())
    if not stamps or any(abs(b-a-.04)>.00002 for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Master packet timestamps are not continuous CFR25')
    return len(stamps)


def disk_plan(data, cfg):
    seconds = float(data['format']['duration'])
    # Video capped40Mbps;45Mbps includes VBV/mux allowance. Up to55
    # restored4x PNGs plus native/input/output PNGs fit the8GiB scratch bound.
    return {'additional_peak_bytes':math.ceil(seconds*45_000_000/8)+8*GIB,
            'scratch_path':cfg['scratch'],'scratch_peak_bytes':8*GIB,
            'backend_fingerprint':cfg['fingerprint'],'input_duration':seconds,
            'max_video_bitrate':40_000_000,'reserve_gib':25,
            'precision_note':'NCNN portable models consume8bitRGB;10bit HEVC encoding does not recover discarded source precision'}


def run(master, output, episode, cfg, *, source_start=0, source_frames=None, prepared=None, restorer=None):
    if cfg.get('rife_backend') in ('cuda_onnx','cuda_trt'):
        from cuda_pipeline import run as cuda_run
        return cuda_run(master,output,episode,cfg,source_start=source_start,source_frames=source_frames,
                        prepared=prepared,restorer=restorer)
    from PIL import Image
    if not re.fullmatch(r'S01E\d{2,3}',episode):raise ValueError('Invalid episode')
    if Path(output).exists():raise ValueError('Refusing to overwrite output')
    if prepared is None:
        reject_retired(sha256(master));info=source_info(master);full_count=source_packet_count(master)
    else:
        if tuple((Path(master).stat().st_ino,Path(master).stat().st_size,Path(master).stat().st_mtime_ns)) != prepared['stat']:
            raise ValueError('Master changed between resumable segments')
        reject_retired(prepared['sha256']);info=prepared['info'];full_count=prepared['count']
    count=min(full_count-source_start,source_frames if source_frames is not None else full_count)
    if source_start<0 or count<=0 or source_start%5:
        raise ValueError('Segment must start on a native five-frame boundary')
    available=min(full_count-source_start,count+5)
    window_info={**info,'format':{**info['format'],'duration':str(count/25)}}
    plan=disk_plan(window_info,cfg)
    print(json.dumps({'storage_selection':cfg.get('storage_selection')}),flush=True)
    check_space(Path(output).parent,plan['additional_peak_bytes'],25)
    check_space(cfg['scratch'],plan['scratch_peak_bytes'],25)
    frames=output_count(count)
    expected=float(info['format']['duration'])
    if abs(full_count/25-expected)>.08:raise ValueError('Packet count/duration mismatch')
    env=os.environ.copy();env['VK_ICD_FILENAMES']=cfg['nvidia_icd']
    env['VK_DRIVER_FILES']=cfg['nvidia_icd']
    Path(cfg['scratch']).mkdir(parents=True,exist_ok=True)
    decoder=encoder=None;start=time.monotonic();cut_intervals=0;interpolated=0
    if restorer is None and cfg['restore'] and cfg.get('restoration_backend') == 'cuda_srvgg':
        from cuda_restore import Restorer
        restorer=Restorer(cfg)
    # Process stderr goes directly to the worker's persistent log, not an unread pipe.
    def invoke(args):
        check_space(cfg['scratch'],128*1024**2,25)
        model_start=time.monotonic()
        result=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        print(json.dumps({'model':Path(args[0]).name,'seconds':round(time.monotonic()-model_start,3)}),flush=True)
        if result.returncode:raise RuntimeError(result.stdout[-4000:])
        identity=f'[{cfg["gpu"]} {cfg["gpu_name"]}]'
        if identity not in result.stdout:raise ValueError('NCNN log did not prove selected physical GPU')
        if not hasattr(invoke,'proved'):
            print(result.stdout,flush=True);invoke.proved=True
    try:
        decoder=subprocess.Popen(['ffmpeg','-nostdin','-v','error','-ss',str(source_start/25),'-i',str(master),
            '-map','0:v:0','-frames:v',str(available),'-vsync','0','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],stdout=subprocess.PIPE)
        encoder=subprocess.Popen(['ffmpeg','-nostdin','-v','warning','-n','-f','rawvideo',
            '-pix_fmt','rgb24','-s','1920x1080','-r','60','-i','pipe:0','-an',
            '-c:v','hevc_nvenc','-gpu',str(cfg.get('encoder_gpu',1)),'-preset','p6',
            '-rc','vbr','-cq','18','-b:v','0','-maxrate','40M','-bufsize','80M',
            '-vf','scale=out_color_matrix=bt709:out_range=tv','-pix_fmt','p010le','-color_range','tv','-color_primaries','bt709','-color_trc','bt709',
            '-colorspace','bt709',str(output)],stdin=subprocess.PIPE)
        block_size=int(cfg.get('chunk_source_frames',50))
        maximum=125 if restorer is not None else 50
        if not 5<=block_size<=maximum or block_size%5:
            raise ValueError(f'Block size must be a multiple of5 between5 and{maximum}')
        def read_frame():
            buf=bytearray();needed=1920*1080*3
            while len(buf)<needed:
                chunk=decoder.stdout.read(needed-len(buf))
                if not chunk:break
                buf.extend(chunk)
            if len(buf)!=needed:raise ValueError('Truncated decoded frame')
            return Image.frombytes('RGB',(1920,1080),bytes(buf))
        buffered=[];restored_overlap=[];loaded=0;source_start=0;emitted=0
        while source_start<count:
            payload=min(block_size,count-source_start)
            while len(buffered)<payload+5 and loaded<available:
                buffered.append(read_frame());loaded+=1
            cuts=[is_cut(buffered[i],buffered[i+1]) for i in range(len(buffered)-1)]
            cut_intervals+=sum(cuts[:payload])
            remaining=frames-emitted;to_emit=min(payload*12//5,remaining) if payload==block_size else remaining
            with tempfile.TemporaryDirectory(prefix='kwjd-block-',dir=cfg['scratch']) as tmp:
                directory=Path(tmp);native=directory/'native';upscaled=directory/'upscaled';flow=directory/'flow'
                for d in [native,upscaled,flow]:d.mkdir()
                if restorer is None:
                    for i,image in enumerate(buffered):image.save(native/f'{i:08d}.png',compress_level=1)
                enhanced=[]
                if restorer:
                    restore_start=time.monotonic()
                    with ThreadPoolExecutor(max_workers=4) as writers:
                        pending=[]
                        for i,image in enumerate(buffered):
                            result=restored_overlap[i] if i<len(restored_overlap) else restorer.restore(image)
                            enhanced.append(result)
                            pending.append(writers.submit(result.save,native/f'{i:08d}.png',compress_level=1))
                            if len(pending)>=8:pending.pop(0).result()
                        for future in pending:future.result()
                    restorer.release_workspace()
                    print(json.dumps({'model':'cuda-general-srvgg','frames':len(buffered),
                        'seconds':round(time.monotonic()-restore_start,3)}),flush=True)
                elif cfg['restore']:
                    invoke([cfg['esrgan'],'-i',str(native),'-o',str(upscaled),'-m',cfg['esrgan_models'],
                            '-n','realesrgan-x4plus','-s','4','-t','256','-g',str(cfg['gpu']),'-j','1:2:2'])
                    for i,image in enumerate(buffered):
                        path=upscaled/f'{i:08d}.png'
                        with Image.open(path) as im:
                            restored=im.convert('RGB').resize((1920,1080),Image.Resampling.LANCZOS)
                        result=Image.blend(image,restored,float(cfg.get('restore_strength',.35)))
                        result.save(native/f'{i:08d}.png');enhanced.append(result);path.unlink()
                else:enhanced=list(buffered)
                # NCNN maps i -> i * input_count/output_count, not endpoints.
                # Pad source count to a multiple of5, so count/(count*12/5) is
                # exactly25/60. Emit only the payload; next5 source frames are
                # overlap to interpolate the right boundary without time drift.
                padded=((len(buffered)+4)//5)*5
                for i in range(len(buffered),padded):
                    enhanced[-1].save(native/f'{i:08d}.png')
                target=padded*12//5
                rife_jobs=cfg.get('rife_jobs','1:2:2')
                if not re.fullmatch(r'[1-4]:[1-4]:[1-4]',rife_jobs):raise ValueError('Invalid bounded RIFE jobs')
                invoke([cfg['rife'],'-i',str(native),'-o',str(flow),'-n',str(target),
                        '-m',cfg['rife_model'],'-g',str(cfg['gpu']),'-f','%08d.png','-j',rife_jobs])
                for k in range(to_emit):
                    left,alpha=frame_position(k)
                    if not alpha or left>=len(buffered)-1 or cuts[left]:
                        frame=enhanced[min(left,len(enhanced)-1)]
                    else:
                        with Image.open(flow/f'{k+1:08d}.png') as im:frame=im.convert('RGB')
                        interpolated+=1
                    check_space(Path(output).parent,16*1024**2,25)
                    encoder.stdin.write(frame.tobytes())
                emitted+=to_emit
            restored_overlap=enhanced[payload:] if restorer is not None else []
            buffered=buffered[payload:];source_start+=payload
            print(json.dumps({'episode':episode,'output_frames':emitted,'total':frames,
                'elapsed':round(time.monotonic()-start,2),'cut_intervals':cut_intervals}),flush=True)
        if decoder.stdout.read(1):raise ValueError('Unexpected extra decoded frames')
        encoder.stdin.close()
        if decoder.wait()!=0 or encoder.wait()!=0:raise RuntimeError('FFmpeg decode/encode failed')
        print(json.dumps({'output_frames':frames,'input_frames':count,'interpolated_frames':interpolated,'cut_intervals':cut_intervals,'wall_seconds':time.monotonic()-start,'restoration':cfg['restore']}),flush=True)
    finally:
        for process in [decoder,encoder]:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true');parser.add_argument('--fingerprint',action='store_true')
    parser.add_argument('input',nargs='?')
    parser.add_argument('output',nargs='?');parser.add_argument('episode',nargs='?')
    parser.add_argument('--config',default=str(Path.home()/'qingqingwu/post/backend/runtime.json'))
    args=parser.parse_args();cfg=config(args.config)
    if args.fingerprint:print(json.dumps({'backend_fingerprint':cfg['fingerprint']}));return
    if not args.input:parser.error('INPUT required')
    if args.plan:print(json.dumps(disk_plan(source_info(args.input),cfg)));return
    if not args.output or not args.episode:parser.error('INPUT OUTPUT EPISODE required')
    run(Path(args.input),Path(args.output),args.episode,cfg)


if __name__=='__main__':main()
