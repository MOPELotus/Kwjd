#!/usr/bin/env python3
"""Bounded face-only audition on a delivered final, never a production backend.

Official GFPGAN1.4 and CodeFormer weights; common facexlib detection/alignment/
parsing/blending at native output size. All frames are streamed, not PNG cached.
Uses an independent environment; refuses CPU model fallback and existing outputs.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import inspect
import re
import textwrap
import types
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                             '-show_format', '-of', 'json', str(path)]))


def run(command, log):
    start = time.monotonic()
    with log.open('w') as stream:
        subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
    return time.monotonic() - start


def pcm_update(path, value, start=None, duration=None):
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror']
    if start is not None:
        command += ['-ss', str(start)]
    command += ['-i', str(path)]
    if duration is not None:
        command += ['-t', str(duration)]
    command += ['-map', '0:a:0', '-vn', '-c:a', 'pcm_s32le', '-f', 's32le', '-']
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        while block := proc.stdout.read(1024 * 1024):
            value.update(block)
        error = proc.stderr.read().decode(errors='replace')
        if proc.wait():
            raise RuntimeError(error)


def validate(path, frames, seconds, folder):
    before = time.monotonic()
    data = probe(path)
    video = next(x for x in data['streams'] if x['codec_type'] == 'video')
    audio = next(x for x in data['streams'] if x['codec_type'] == 'audio')
    assert (video['width'], video['height'], video['avg_frame_rate']) == (1920, 1080, '25/1')
    assert video['codec_name'] == 'hevc' and video['pix_fmt'] == 'yuv420p10le'
    assert all(video.get(k) == 'bt709' for k in ['color_space', 'color_transfer', 'color_primaries'])
    assert video['color_range'] == 'tv'
    assert audio['codec_name'] == 'flac' and audio['sample_rate'] == '48000'
    assert audio['channels'] == 2 and audio.get('bits_per_raw_sample') == '24'
    assert abs(float(data['format']['duration']) - seconds) < .05
    packets=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_packets',
        '-show_entries','packet=stream_index,pts_time,duration_time','-of','json',str(path)]))['packets']
    vpts=sorted(float(x['pts_time']) for x in packets if x['stream_index']==video['index'])
    apts=sorted((float(x['pts_time']),float(x.get('duration_time',0))) for x in packets if x['stream_index']==audio['index'])
    maximum_video_grid_error=max(abs(t-i/25) for i,t in enumerate(vpts))
    audio_gaps=[apts[i][0]-sum(apts[i-1]) for i in range(1,len(apts))]
    maximum_audio_gap=max(map(abs,audio_gaps),default=0)
    assert len(vpts)==frames and len(set(vpts))==frames and maximum_video_grid_error<.0011
    assert abs(apts[0][0])<.0011 and abs(sum(apts[-1])-seconds)<.0011 and maximum_audio_gap<.0011
    packet_clock={'video_first':vpts[0],'video_last':vpts[-1],'max_video_grid_error':maximum_video_grid_error,
        'audio_first':apts[0][0],'audio_end':sum(apts[-1]),'max_audio_gap':maximum_audio_gap,
        'scope':'All packet PTS examined, including60/120s concatenation seams; quantization<=1ms'}
    progress = folder / (path.stem + '.decode-progress.txt')
    with progress.open('w') as out, (folder / (path.stem + '.decode.log')).open('w') as err:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
                        '-i', str(path), '-map', '0:v:0', '-an', '-progress', 'pipe:1',
                        '-nostats', '-f', 'null', '-'], stdout=out, stderr=err, check=True)
    counts = [int(s.split('=', 1)[1]) for s in progress.read_text().splitlines() if s.startswith('frame=')]
    assert counts[-1] == frames, (counts[-1], frames)
    return {'bytes': path.stat().st_size, 'sha256': digest(path), 'frames': counts[-1],
            'probe': data, 'packet_clock':packet_clock, 'qc_wall_seconds': time.monotonic() - before}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--gpu', type=int, default=1)
    parser.add_argument('--clip', action='append', help='start:duration; up to3clips, total<=180seconds')
    parser.add_argument('--variant', action='append', choices=['gfpgan14', 'codeformer_w05', 'codeformer_w08'])
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--fusion', choices=['official','float32','roi'], default='official')
    parser.add_argument('--precision', choices=['fp32','tf32','fp16'], default='fp32')
    parser.add_argument('--original-stills-dir', type=Path, help='Optional3preserved original-master PNGs, not video baseline')
    args = parser.parse_args()
    clips = [tuple(map(float, s.split(':'))) for s in (args.clip or ['60:60', '1700:60', '2800:60'])]
    if not (1 <= len(clips) <= 3 and all(s >= 0 and 0 < d <= 60 and abs(d*25-round(d*25)) < 1e-6 for s,d in clips)
            and sum(d for s,d in clips) <= 180 and 1 <= args.threads <= 8 and args.gpu >= 0):
        parser.error('Bounded valid clips/threads/physical GPU required')
    if args.output.exists():
        raise FileExistsError('Use a new directory; previous auditions are preserved')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for path, budget in [(args.output.parent, 6), (args.tools, 1)]:
        if shutil.disk_usage(path).free < (25+budget)*2**30:
            raise RuntimeError('Insufficient measured disk budget above25GiB floor')
    actual_hash = digest(args.input)
    if actual_hash != args.input_sha256:
        raise ValueError('Input SHA mismatch')
    args.output.mkdir()
    lock = (args.output.parent / '.face-audition.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    encoder_environment=os.environ.copy()
    encoder_environment.pop('CUDA_VISIBLE_DEVICES',None)
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu)
    import_start = time.monotonic()
    import cv2
    import numpy as np
    import torch
    import torchvision.transforms.functional as functional
    # BasicSR1.4.2 imports a module removed in torchvision0.17+. Same official
    # rgb_to_grayscale function, compatibility alias only; no production install edits.
    sys.modules['torchvision.transforms.functional_tensor'] = functional
    from basicsr.utils import img2tensor, tensor2img
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper
    from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean
    for name in ['vqgan_arch', 'codeformer_arch']:
        module_name = 'basicsr.archs.' + name
        path = args.tools / 'CodeFormer/basicsr/archs' / (name + '.py')
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    from basicsr.utils.registry import ARCH_REGISTRY
    torch.set_num_threads(args.threads)
    cv2.setNumThreads(args.threads)
    torch.manual_seed(1234)
    if not torch.cuda.is_available():
        raise RuntimeError('Physical CUDA required; CPU model fallback forbidden')
    device = torch.device('cuda:0')
    free_before,total_memory=torch.cuda.mem_get_info(0)
    allocated_budget=min(total_memory*.60,free_before-3*2**30)
    if allocated_budget<1.5*2**30:raise RuntimeError('Cannot safely admit model above3GiB shared reserve')
    torch.cuda.set_per_process_memory_fraction(allocated_budget/total_memory, 0)
    if args.precision=='tf32' and torch.cuda.get_device_capability(0)[0]<8:
        raise RuntimeError('TF32 requires measured compute capability>=8')
    torch.backends.cuda.matmul.allow_tf32 = args.precision=='tf32'
    torch.backends.cudnn.allow_tf32 = args.precision=='tf32'
    test = torch.ones((16,16),device=device) @ torch.ones((16,16),device=device)
    assert test[0,0].item() == 16
    physical = subprocess.check_output(['nvidia-smi', '--id='+str(args.gpu),
        '--query-gpu=name,uuid,driver_version,memory.total,compute_cap', '--format=csv,noheader'], text=True).strip()
    assert torch.cuda.get_device_name(0) in physical
    thermal_limits=subprocess.check_output(['nvidia-smi','--id='+str(args.gpu),'-q','-d','TEMPERATURE'],text=True)
    max_operating=int(re.search(r'GPU Max Operating Temp\s*:\s*(\d+) C',thermal_limits).group(1))
    pause_temperature=min(95,max_operating-2);resume_temperature=pause_temperature-4
    report = {'started_at': now(), 'status':'running', 'scope':__doc__, 'input':str(args.input.resolve()),
              'input_sha256':actual_hash,'runner_sha256':digest(Path(__file__)), 'source_basis':'delivered60fps final, incrementally enhanced at25fps; NOT original master',
              'clips':clips, 'fps':25, 'physical_gpu':physical, 'logical_device':str(device),
              'torch':torch.__version__, 'cuda_runtime':torch.version.cuda,
              'import_and_cuda_check_seconds':time.monotonic()-import_start,
              'timing_note':'Pipeline wall includes decode, detection/alignment/restoration/fusion, raw pipe, originalaudio and NVENC. Encoder process lifetime includes waiting for model and overlaps it; encode_write is pipe blocking time, not standaloneNVENC compute time. QC/modelinit/import/preparation reported separately.',
              'encoder_environment':'CUDA visibility mask removed ONLY for ownedFFmpeg encoder, so explicit NVENC gpu argument is physical. Torch remains visibility-masked.',
              'encoder':'NVENC Main10 p6 hq CQ16; explicit RGBfull->Rec709TV; FLAC24 48kStereo original gain',
              'fusion':args.fusion,'thermal_limits':{'measured_max_operating':max_operating,'pause':pause_temperature,'resume':resume_temperature},
              'model_precision':args.precision+'; FP32 weights; fp16 option uses autocast only for restoration+parsing, detector staysFP32; no quantization',
              'allocation_budget_bytes':allocated_budget,'free_memory_before_model_bytes':free_before,
              'shared_gpu_load_before_model':subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,utilization.gpu,temperature.gpu','--format=csv,noheader'],text=True),
              'detector':'facexlib0.3.0 retinaface_resnet50; resize640; eye_dist_threshold5; all faces',
              'face_size':512, 'parsing':'parsenet', 'background_upscaler':None,
              'processing_color':'BGR uint8; sourceTV709->full RGB explicit; encoderfullRGB->TV709 explicit;10bit container does not imply10bit model precision',
              'variants':[]}
    weights = args.tools / 'weights'
    report['weight_sha256'] = {p.name:digest(p) for p in weights.glob('*.pth')}
    save(args.output/'report.json', report)
    expected_audio = hashlib.sha256()
    audio_start = time.monotonic()
    for start,duration in clips:
        pcm_update(args.input, expected_audio, start, duration)
    report['expected_source_audio_pcm_sha256'] = expected_audio.hexdigest()
    report['reference_audio_preparation_seconds'] = time.monotonic()-audio_start
    def timed(fn):
        torch.cuda.synchronize()
        t=time.monotonic();v=fn();torch.cuda.synchronize()
        return v,time.monotonic()-t
    thermal_paused = 0.
    last_guard_time=0.
    last_guard_snapshot=None
    def guard(force=False):
        nonlocal thermal_paused,last_guard_time,last_guard_snapshot
        if not force and time.monotonic()-last_guard_time<2 and last_guard_snapshot is not None:
            return last_guard_snapshot
        snapshot = subprocess.check_output(['nvidia-smi','--id='+str(args.gpu),
            '--query-gpu=temperature.gpu,memory.free','--format=csv,noheader,nounits'],text=True)
        temp,free = map(int,snapshot.strip().split(','))
        if free < 3072:
            raise RuntimeError('Shared physical GPU free memory below3GiB; no unbounded retry')
        if temp >= pause_temperature:
            t=time.monotonic()
            while temp > resume_temperature:
                print(json.dumps({'stage':'THERMAL_PAUSE','temperature':temp,'at':now()}),flush=True)
                time.sleep(2)
                temp=int(subprocess.check_output(['nvidia-smi','--id='+str(args.gpu),'--query-gpu=temperature.gpu','--format=csv,noheader,nounits'],text=True))
            thermal_paused += time.monotonic()-t
            snapshot=subprocess.check_output(['nvidia-smi','--id='+str(args.gpu),'--query-gpu=temperature.gpu,memory.free','--format=csv,noheader,nounits'],text=True)
            temp,free=map(int,snapshot.strip().split(','))
            if free<3072:raise RuntimeError('Shared GPU free memory dropped below3GiB during cooldown')
        last_guard_time=time.monotonic();last_guard_snapshot=(temp,free)
        return last_guard_snapshot
    try:
        for variant in args.variant or ['gfpgan14','codeformer_w05','codeformer_w08']:
            folder=args.output/variant;folder.mkdir()
            t=time.monotonic()
            guard(force=True)
            helper=FaceRestoreHelper(1,face_size=512,crop_ratio=(1,1),det_model='retinaface_resnet50',
                use_parse=True,device=device,model_rootpath=str(weights))
            official_fuse=helper.paste_faces_to_input_image
            if args.fusion=='float32':
                # Runtime-local one-line adaptation of facexlib0.3.0's MIT function.
                # Copyright2020 XintaoWang; license retained with preparation evidence.
                original_source=textwrap.dedent(inspect.getsource(type(helper).paste_faces_to_input_image))
                assert original_source.count('mask = np.zeros(out.shape)')==1
                patched_source=original_source.replace('mask = np.zeros(out.shape)','mask = np.zeros(out.shape, dtype=np.float32)')
                scope={};exec(compile(patched_source,'<face-audition-float32-mask>','exec'),official_fuse.__func__.__globals__,scope)
                helper.paste_faces_to_input_image=types.MethodType(scope['paste_faces_to_input_image'],helper)
            if args.fusion=='roi':
                from face_audition_fusion import paste_faces_roi
                helper.paste_faces_to_input_image=types.MethodType(lambda owner,save_path=None,upsample_img=None:paste_faces_roi(owner),helper)
            parsing_wall=0.
            original_parse_forward=helper.face_parse.forward
            def measured_parse_forward(*items,**kwargs):
                nonlocal parsing_wall
                torch.cuda.synchronize();begin=time.monotonic()
                output=original_parse_forward(*items,**kwargs)
                torch.cuda.synchronize();parsing_wall+=time.monotonic()-begin
                return output
            helper.face_parse.forward=measured_parse_forward
            if variant=='gfpgan14':
                model=GFPGANv1Clean(out_size=512,num_style_feat=512,channel_multiplier=2,
                    decoder_load_path=None,fix_decoder=False,num_mlp=8,input_is_latent=True,
                    different_w=True,narrow=1,sft_half=True)
                state=torch.load(weights/'GFPGANv1.4.pth',map_location='cpu',weights_only=True)
                model.load_state_dict(state['params_ema'],strict=True)
                fidelity=None
            else:
                model=ARCH_REGISTRY.get('CodeFormer')(dim_embd=512,codebook_size=1024,n_head=8,n_layers=9,
                    connect_list=['32','64','128','256'])
                state=torch.load(weights/'codeformer.pth',map_location='cpu',weights_only=True)
                model.load_state_dict(state['params_ema'],strict=True)
                fidelity=.5 if variant.endswith('05') else .8
            guard(force=True)
            model.eval().to(device)
            assert next(model.parameters()).device==device
            entry={'name':variant,'status':'running','model_init_seconds':time.monotonic()-t,
                'fusion_helper_sha256':digest(Path(__file__).with_name('face_audition_fusion.py')) if args.fusion=='roi' else None,
                'fidelity_weight':fidelity,'gfpgan_randomize_noise':False if fidelity is None else None,
                'fusion_equivalence_samples':[],'segments':[]};report['variants'].append(entry);save(args.output/'report.json',report)
            variant_start=time.monotonic();pause_before=thermal_paused
            @torch.inference_mode()
            def process_image(img,check_fusion=False):
                detail={'times':{k:0. for k in ['preparation','detection','alignment','restoration','fusion']}}
                _,dt=timed(lambda:(helper.clean_all(),helper.read_image(img)));detail['times']['preparation']=dt
                count,dt=timed(lambda:helper.get_face_landmarks_5(resize=640,eye_dist_threshold=5))
                count=int(count or 0);detail['faces_detected']=count;detail['times']['detection']=dt
                if count:
                    _,dt=timed(helper.align_warp_face);detail['times']['alignment']=dt
                    @torch.autocast(device_type='cuda',dtype=torch.float16,enabled=args.precision=='fp16')
                    def restore():
                        for crop in helper.cropped_faces:
                            guard()
                            tensor=img2tensor(crop.astype(np.float32)/255.,bgr2rgb=True,float32=True)
                            tensor=functional.normalize(tensor,(.5,.5,.5),(.5,.5,.5)).unsqueeze(0).to(device)
                            output=model(tensor,return_rgb=False,randomize_noise=False)[0] if fidelity is None else model(tensor,w=fidelity,adain=True)[0]
                            if not torch.isfinite(output).all().item():raise RuntimeError('Nonfinite restored face')
                            restored=tensor2img(output,rgb2bgr=True,min_max=(-1,1))
                            helper.add_restored_face(restored.astype(np.uint8))
                    _,dt=timed(restore);detail['times']['restoration']=dt
                    @torch.autocast(device_type='cuda',dtype=torch.float16,enabled=args.precision=='fp16')
                    def fuse():
                        helper.get_inverse_affine(None)
                        return helper.paste_faces_to_input_image(upsample_img=None)
                    parsing_before=parsing_wall
                    result,dt=timed(fuse);detail['times']['fusion']=dt
                    parse_dt=parsing_wall-parsing_before
                    detail['fusion_breakdown']={'parsing_network_seconds':parse_dt,'cpu_warp_blur_blend_transfer_seconds':max(0,dt-parse_dt)}
                    if check_fusion and args.fusion!='official':
                        with torch.autocast(device_type='cuda',dtype=torch.float16,enabled=args.precision=='fp16'):
                            reference,reference_dt=timed(lambda:official_fuse(upsample_img=None))
                        difference=cv2.absdiff(reference,result)
                        comparison={'max_uint8_delta':int(difference.max()),'mean_uint8_delta':float(difference.mean()),
                            'changed_values':int(np.count_nonzero(difference)),'official_reference_extra_seconds_excluded_from_model_timing':reference_dt}
                        entry['fusion_equivalence_samples'].append(comparison)
                        if comparison['max_uint8_delta']>2:raise RuntimeError('Optimized fusion differs by>2LSB; reject optimization')
                else:result=img
                assert result.shape==img.shape and result.dtype==np.uint8
                detail['faces_restored']=len(helper.restored_faces)
                return result,detail
            for idx,(start,duration) in enumerate(clips):
                part=folder/f'part_{idx:02d}.mkv';partial=folder/f'part_{idx:02d}.partial.mkv';frames=round(duration*25)
                dec_cmd=['ffmpeg','-nostdin','-v','error','-xerror','-err_detect','explode','-threads',str(args.threads),'-filter_threads',str(args.threads),
                    '-ss',str(start),'-i',str(args.input),'-t',str(duration),'-an','-sn','-vf',
                    'fps=25,scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=bgr24',
                    '-frames:v',str(frames),'-f','rawvideo','-']
                enc_cmd=['ffmpeg','-nostdin','-v','warning','-n','-threads',str(args.threads),'-filter_threads',str(args.threads),
                    '-f','rawvideo','-pixel_format','bgr24','-video_size','1920x1080','-framerate','25','-i','pipe:0',
                    '-ss',str(start),'-i',str(args.input),'-map','0:v:0','-map','1:a:0','-t',str(duration),
                    '-vf','scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=p010le',
                    '-c:v','hevc_nvenc','-gpu',str(args.gpu),'-preset','p6','-tune','hq','-profile:v','main10',
                    '-rc','vbr','-cq','16','-b:v','0','-colorspace','bt709','-color_trc','bt709',
                    '-color_primaries','bt709','-color_range','tv','-c:a','flac','-sample_fmt','s32',
                    '-bits_per_raw_sample','24','-ar','48000','-map_metadata','-1',str(partial)]
                stats={'source_start':start,'duration':duration,'frames':0,'detected_frames':0,'no_face_frames':0,
                    'restored_frames':0,'restored_faces':0,'failed_frames':0,'finite_output_checked':True,
                    'fusion_breakdown_seconds':{'parsing_network_seconds':0.,'cpu_warp_blur_blend_transfer_seconds':0.},
                    'times':{k:0. for k in ['decode_read','preparation','detection','alignment','restoration','fusion','encode_write']},
                    'decode_command':dec_cmd,'encode_command':enc_cmd};entry['segments'].append(stats)
                segment_start=time.monotonic();segment_pause=thermal_paused
                with (folder/f'part_{idx:02d}.decoder.log').open('w') as dl,(folder/f'part_{idx:02d}.encoder.log').open('w') as el,(folder/f'part_{idx:02d}.frames.jsonl').open('w') as frame_log:
                    decoder=subprocess.Popen(dec_cmd,stdout=subprocess.PIPE,stderr=dl)
                    encoder_start=time.monotonic()
                    encoder=subprocess.Popen(enc_cmd,stdin=subprocess.PIPE,stdout=el,stderr=el,env=encoder_environment)
                    try:
                        with torch.inference_mode():
                            for frame_idx in range(frames):
                                temp,free=guard()
                                t=time.monotonic();raw=decoder.stdout.read(1920*1080*3)
                                if len(raw)!=1920*1080*3:raise RuntimeError('Truncated decoded frame')
                                img=np.frombuffer(raw,dtype=np.uint8).reshape(1080,1920,3).copy();stats['times']['decode_read']+=time.monotonic()-t
                                result,detail=process_image(img,check_fusion=frame_idx==0)
                                for key,value in detail.get('fusion_breakdown',{}).items():stats['fusion_breakdown_seconds'][key]+=value
                                count=detail['faces_detected']
                                for key,seconds in detail['times'].items():stats['times'][key]+=seconds
                                if count:
                                    stats['detected_frames']+=1;stats['restored_frames']+=1
                                    stats['restored_faces']+=detail['faces_restored']
                                else:stats['no_face_frames']+=1
                                t=time.monotonic();encoder.stdin.write(result.tobytes());stats['times']['encode_write']+=time.monotonic()-t
                                stats['frames']+=1
                                frame_log.write(json.dumps({'frame':frame_idx,'nominal_25fps_target_time':start+frame_idx/25,'faces_detected':count,
                                    'faces_restored':len(helper.restored_faces),'restoration_failed':False,'temperature':temp,
                                    'changed_pixels':int(np.any(result!=img,axis=2).sum())})+'\n')
                                if frame_idx in [0,min(25,frames-1),min(250,frames-1)]:
                                    cv2.imwrite(str(folder/f'part_{idx:02d}_{frame_idx:04d}_input.png'),img)
                                    cv2.imwrite(str(folder/f'part_{idx:02d}_{frame_idx:04d}_output.png'),result)
                                if frame_idx%25==24:
                                    stats['elapsed_seconds']=time.monotonic()-segment_start;save(args.output/'report.json',report)
                                    print(json.dumps({'variant':variant,'clip':idx,'frames':stats['frames'],'of':frames,
                                        'restored_frames':stats['restored_frames'],'elapsed':stats['elapsed_seconds']}),flush=True)
                        flush_start=time.monotonic()
                        encoder.stdin.close()
                        if decoder.stdout.read(1):raise RuntimeError('Unexpected extra decoded frames')
                        decoder_code=decoder.wait();encoder_code=encoder.wait()
                        if decoder_code or encoder_code:raise RuntimeError('Decode/encode failed; see per-part logs')
                        stats['encoder_process_lifetime_overlaps_model_seconds']=time.monotonic()-encoder_start
                        stats['encode_flush_and_decoder_drain_seconds']=time.monotonic()-flush_start
                    except BaseException:
                        stats['failed_frames']+=1
                        for process in [decoder,encoder]:
                            if process.poll() is None:process.terminate()
                        decoder.stdout.close()
                        try:encoder.stdin.close()
                        except BrokenPipeError:pass
                        for process in [decoder,encoder]:
                            try:process.wait(timeout=3)
                            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
                        raise
                stats['pipeline_wall_seconds']=time.monotonic()-segment_start
                stats['thermal_pause_seconds']=thermal_paused-segment_pause
                stats['effective_fps']=frames/stats['pipeline_wall_seconds']
                stats['validation']=validate(partial,frames,duration,folder)
                partial.rename(part);stats['path']=str(part);stats['status']='verified';save(args.output/'report.json',report)
            concat=folder/'concat.txt';concat.write_text(''.join(f"file 'part_{i:02d}.mkv'\n" for i in range(len(clips))))
            output=folder/(variant+'.mkv')
            entry['join_seconds']=run(['ffmpeg','-nostdin','-v','warning','-n','-f','concat','-safe','1','-i',str(concat),
                '-map','0:v:0','-map','0:a:0','-c','copy',str(output)],folder/'join.log')
            entry['validation']=validate(output,round(sum(d for s,d in clips)*25),sum(d for s,d in clips),folder)
            actual_audio=hashlib.sha256();pcm_update(output,actual_audio)
            entry['decoded_pcm_sha256']=actual_audio.hexdigest()
            assert actual_audio.hexdigest()==expected_audio.hexdigest(),'Audio changed vs exact input ranges'
            entry['pipeline_render_and_join_seconds']=sum(s['pipeline_wall_seconds'] for s in entry['segments'])+entry['join_seconds']
            entry['model_init_plus_pipeline_seconds']=entry['model_init_seconds']+entry['pipeline_render_and_join_seconds']
            entry['total_variant_wall_including_qc_seconds']=time.monotonic()-variant_start+entry['model_init_seconds']
            entry['effective_fps_with_init']=sum(d for s,d in clips)*25/entry['model_init_plus_pipeline_seconds']
            entry['thermal_pause_seconds']=thermal_paused-pause_before
            if args.original_stills_dir:
                originals=folder/'original_master_stills';originals.mkdir()
                entry['original_master_stills']=[]
                for second in [60,1700,2800]:
                    source=args.original_stills_dir/f'source_{second}.png'
                    original=cv2.imread(str(source))
                    if original is None:raise ValueError('Missing original-master still '+str(source))
                    guard();t=time.monotonic();result,detail=process_image(original,check_fusion=True)
                    destination=originals/f'{variant}_{second}.png'
                    if not cv2.imwrite(str(destination),result):raise RuntimeError('PNG write failed')
                    entry['original_master_stills'].append({'source':str(source),'source_sha256':digest(source),
                        'path':str(destination),'sha256':digest(destination),'wall_seconds':time.monotonic()-t,
                        'basis':'Preserved ORIGINAL master still, NOT delivered-final video sample',**detail})
            entry['path']=str(output);entry['status']='verified';save(args.output/'report.json',report)
            entry['peak_torch_allocated_bytes']=torch.cuda.max_memory_allocated()
            entry['peak_torch_reserved_bytes']=torch.cuda.max_memory_reserved()
            del model,helper,state;torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats()
        report['status']='verified_pending_visual_review';report['completed_at']=now()
    except BaseException as error:
        report['status']='failed';report['error']=repr(error);raise
    finally:
        report['source_hash_unchanged']=digest(args.input)==actual_hash
        if not report['source_hash_unchanged']:
            report['status']='failed';report['error']='Input changed during audition; reject all outputs'
        save(args.output/'report.json',report)
        if not report['source_hash_unchanged']:raise RuntimeError(report['error'])


if __name__=='__main__':
    main()
