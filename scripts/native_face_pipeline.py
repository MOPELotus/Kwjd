"""Native25 light/GFPGAN/RIFE -> approved ASS -> one lossy video encode.

Uses the common segmented backend for recovery and existing source/audio handoff.
No worker may treat this styled checkpoint as a subtitle-free enhancement.
"""
from collections import defaultdict
from pathlib import Path
from storage_policy import minimum_free_gib
import json
import subprocess
import time

from enhance_stage import WaitGPU,check_space


def validate_pipeline(path,cfg,source_start,count,full):
    """Require actual frame-processing evidence before adopting native partials."""
    from enhance_stage import sha256
    receipt=Path(path).with_name(Path(path).name+'.pipeline.json')
    frame_log=Path(path).with_name(Path(path).name+'.face-frames.jsonl')
    try:
        data=json.loads(receipt.read_text());coverage=data['coverage']
        lookahead=int(source_start+count<full)
        if (data['status']!='PROCESSED_PENDING_DECODE' or data['source_start']!=source_start
                or data['input_frames']!=count or data['lookahead_frames']!=lookahead
                or data['native_recipe_sha256']!=cfg['native_recipe_sha256']
                or data['ass_sha256']!=cfg['fused_subtitle_binding']['ass_sha256']
                or data['video_encodes']!=1 or data['ass_after_rife'] is not True
                or coverage['processed_frames']!=count+lookahead or coverage['failed_frames']!=0
                or coverage['restored_frames']+coverage['no_detection_frames']!=coverage['processed_frames']
                or sha256(frame_log)!=data['frame_log_sha256']):
            raise ValueError('Native frame-processing evidence differs')
    except (OSError,KeyError,json.JSONDecodeError) as error:
        raise ValueError('Native partial lacks complete frame-processing evidence') from error
    return dict(pipeline_receipt_path=str(receipt),pipeline_receipt_sha256=sha256(receipt))


class NativeFrames:
    """Persistent GF(A4000) and RIFE(T10), with CPU face fusion between them."""
    def __init__(self,cfg):
        from face_frame_processor import GFPGANProcessor
        from cuda_trt import CudaRife
        import torch
        import cv2
        self.torch=torch;self.cv2=cv2;self.cfg=cfg
        torch.set_num_threads(4);cv2.setNumThreads(4)
        self.check_resources(initial=True)
        try:
            self.face=GFPGANProcessor(Path(cfg['face_weights']))
        except RuntimeError as error:
            if 'WAIT_GPU' in str(error) or 'headroom' in str(error) or error.__class__.__name__=='OutOfMemoryError':
                raise WaitGPU('Shared face initialization must wait: '+str(error)) from error
            raise
        self.check_resources()
        try:self.rife=CudaRife(cfg)
        except torch.cuda.OutOfMemoryError as error:
            raise WaitGPU('Shared RIFE initialization must wait') from error
        self.device=self.rife.device
        self.stream=torch.cuda.Stream(device=self.device)
        self.check_resources()

    def check_resources(self,initial=False):
        rows=subprocess.check_output(['nvidia-smi','--query-gpu=index,temperature.gpu,memory.free',
            '--format=csv,noheader,nounits'],text=True)
        for line in rows.splitlines():
            i,temp,free=map(int,line.split(','))
            if (temp>=(95 if i==0 else 85)
                    or free<((5120 if initial else 3072) if i==0 else (3072 if initial else 1024))):
                raise WaitGPU('Native face/RIFE shared GPU admission/cooling: '+rows.strip())


def run(master,output,episode,cfg,*,source_start=0,source_frames=None,prepared=None,restorer=None):
    import numpy as np
    from PIL import Image
    import enhance_backend as engine
    from enhance_stage import sha256
    from master_policy import reject_retired
    from render_face_samples import save
    from native_face_recipe import validate_recipe
    began=time.monotonic();output=Path(output)
    if output.exists():raise FileExistsError('Native fused output already exists')
    recipe=validate_recipe(cfg['native_recipe'],cfg['native_recipe_sha256'])
    if prepared is None:
        reject_retired(sha256(master));info=engine.source_info(master);full=engine.source_packet_count(master)
    else:
        stat=Path(master).stat()
        if (stat.st_ino,stat.st_size,stat.st_mtime_ns)!=prepared['stat']:raise ValueError('Native master changed')
        info=prepared['info'];full=prepared['count']
    count=min(full-source_start,source_frames if source_frames is not None else full)
    if source_start<0 or source_start%5 or count<=0 or abs(full/25-float(info['format']['duration']))>.08:
        raise ValueError('Native25 segment clock invalid')
    if prepared is not None and recipe['master_sha256']!=prepared['sha256']:
        raise ValueError('Fused ASS recipe belongs to another native master')
    if prepared is None and recipe['master_sha256']!=sha256(master):raise ValueError('Fused recipe master differs')
    model_start=time.monotonic();restorer=restorer or NativeFrames(cfg)
    torch=restorer.torch;rife=restorer.rife;device=rife.device
    times=defaultdict(float);times['initialization']=time.monotonic()-model_start
    available=min(count+1,full-source_start);needed=1920*1080*3
    light=cfg.get('light_filter')
    if light not in ['none','clarity']:raise ValueError('Explicit tested lightweight filter required')
    vf=('format=yuv444p,cas=strength=.6:planes=1,eq=contrast=1.03:saturation=1.02,' if light=='clarity' else '')
    vf+='scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=bgr24'
    decoder_command=['ffmpeg','-nostdin','-v','error','-xerror','-err_detect','explode','-threads','4',
        '-filter_threads','4','-ss',str(source_start/25),'-i',str(master),'-map','0:v:0','-an','-vf',vf,
        '-frames:v',str(available),'-fps_mode','passthrough','-f','rawvideo','-']
    # Approved ASS has already had the episode's audited offset applied. Only
    # restore its program clock during each segment's final style render.
    for path in [recipe['ass_path'],recipe['fonts_path']]:
        if any(c in str(path) for c in "'\\:[],;"):raise ValueError('Unsafe FFmpeg filter path')
    origin=float(recipe.get('program_origin_seconds',0))+source_start/25
    filters=(f"setpts=PTS+{origin}/TB,ass=filename={recipe['ass_path']}:fontsdir={recipe['fonts_path']},"
        'setpts=PTS-STARTPTS,scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=p010le')
    maxrate=cfg.get('max_video_mbps',16)
    if type(maxrate)is not int or not 8<=maxrate<=40:raise ValueError('Invalid final video cap')
    encoder_command=['ffmpeg','-nostdin','-v','verbose','-n','-threads','4','-filter_threads','4',
        '-f','rawvideo','-pixel_format','rgb24','-video_size','1920x1080','-framerate','60','-i','pipe:0',
        '-an','-vf',filters,'-c:v','hevc_nvenc','-gpu','1','-preset','p6','-tune','hq',
        '-profile:v','main10','-rc','vbr','-cq','16','-b:v','0','-maxrate',str(maxrate)+'M',
        '-bufsize',str(maxrate*2)+'M','-colorspace','bt709','-color_trc','bt709',
        '-color_primaries','bt709','-color_range','tv',str(output)]
    frame_log=output.with_name(output.name+'.face-frames.jsonl')
    coverage=dict(processed_frames=0,restored_frames=0,no_detection_frames=0,faces_restored=0,failed_frames=0)
    emitted=generated=0;cuts=[];decoder=encoder=None;last_guard=0
    try:
        with output.with_name(output.name+'.decoder.log').open('w') as dl,output.with_name(output.name+'.encoder.log').open('w') as el,frame_log.open('w') as fl:
            decoder=subprocess.Popen(decoder_command,stdout=subprocess.PIPE,stderr=dl)
            encoder=subprocess.Popen(encoder_command,stdin=subprocess.PIPE,stderr=el)
            def read():
                nonlocal last_guard
                if time.monotonic()-last_guard>=2:restorer.check_resources();last_guard=time.monotonic()
                start=time.monotonic();raw=decoder.stdout.read(needed)
                if len(raw)!=needed:raise ValueError('Truncated native25 frame')
                image=np.frombuffer(raw,dtype=np.uint8).reshape(1080,1920,3).copy();times['decode_read']+=time.monotonic()-start
                start=time.monotonic()
                try:result,detail=restorer.face.process_bgr(image)
                except torch.cuda.OutOfMemoryError as error:
                    raise WaitGPU('Shared face inference memory changed; retain segment for retry') from error
                times['face']+=time.monotonic()-start
                coverage['processed_frames']+=1;coverage['faces_restored']+=detail['faces_restored']
                coverage['restored_frames' if detail['faces_detected'] else 'no_detection_frames']+=1
                fl.write(json.dumps({'source_frame':source_start+coverage['processed_frames']-1,**detail})+'\n')
                start=time.monotonic()
                pixels=np.ascontiguousarray(result[:,:,::-1])
                tensor=torch.from_numpy(pixels).permute(2,0,1).unsqueeze(0).to(device,dtype=torch.float32)/255
                restorer.stream.synchronize();times['upload_to_rife']+=time.monotonic()-start
                return Image.fromarray(image[:,:,::-1]),tensor
            with torch.inference_mode(),torch.cuda.stream(restorer.stream):
                previous,a=read()
                for index in range(count):
                    if index+1<available:
                        following,b=read();start=time.monotonic();cut=engine.is_cut(previous,following)
                        times['cut_detection']+=time.monotonic()-start
                    else:following,b,cut=previous,a,False
                    if cut:cuts.append(source_start+index+1)
                    while emitted<engine.output_count(count):
                        left,alpha=engine.frame_position(emitted)
                        if left!=index:break
                        start=time.monotonic()
                        if alpha and not cut and index+1<available:
                            result=rife.interpolate(a,b,float(alpha));generated+=1
                        else:result=a
                        restorer.stream.synchronize();times['rife']+=time.monotonic()-start
                        start=time.monotonic();pixels=(result[0].permute(1,2,0)*255).round().clamp_(0,255).to(torch.uint8).contiguous().cpu().numpy()
                        times['output_transfer']+=time.monotonic()-start
                        start=time.monotonic();encoder.stdin.write(pixels.tobytes());times['encode_pipe']+=time.monotonic()-start
                        emitted+=1
                    previous,a=following,b
                    if index%25==24:
                        check_space(output.parent,16*1024**2,minimum_free_gib(cfg))
                        print(json.dumps(dict(episode=episode,source_start=source_start,processed=index+1,total=count,
                            output_frames=emitted,elapsed=time.monotonic()-began)),flush=True)
            start=time.monotonic();encoder.stdin.close()
            if decoder.stdout.read(1) or decoder.wait() or encoder.wait():raise RuntimeError('Native decode/final encode failed')
            times['encode_flush']=time.monotonic()-start
        if emitted!=engine.output_count(count) or coverage['processed_frames']!=available:
            raise ValueError('Native whole segment frame mapping incomplete')
        validate_recipe(cfg['native_recipe'],cfg['native_recipe_sha256'])
        encoder_log=output.with_name(output.name+'.encoder.log').read_text()
        if 'MiSans-Subtitle-Demibold' not in encoder_log:raise ValueError('Actual approved MiSans font not resolved')
        receipt=dict(status='PROCESSED_PENDING_DECODE',episode=episode,source_start=source_start,
            input_frames=count,lookahead_frames=available-count,output_frames=emitted,interpolated_frames=generated,
            cuts=cuts,coverage=coverage,pipeline_seconds=time.monotonic()-began,times=dict(times),
            native_recipe_sha256=cfg['native_recipe_sha256'],ass_sha256=recipe['ass_sha256'],
            frame_log_sha256=sha256(frame_log),decode_command=decoder_command,encode_command=encoder_command,
            video_encodes=1,ass_after_rife=True)
        save(output.with_name(output.name+'.pipeline.json'),receipt)
        print(json.dumps(receipt),flush=True)
    finally:
        for process in [decoder,encoder]:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait()
