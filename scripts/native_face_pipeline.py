"""Native25 light/GFPGAN/RIFE -> approved ASS -> one lossy video encode.

Uses the common segmented backend for recovery and existing source/audio handoff.
No worker may treat this styled checkpoint as a subtitle-free enhancement.
"""
from collections import defaultdict
from contextlib import contextmanager, ExitStack, nullcontext
import threading
from pathlib import Path
from storage_policy import minimum_free_gib
import json
import hashlib
import math
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


def pipeline_options(cfg):
    """Validate candidate knobs before any GPU-runtime import or allocation."""
    for flag in ('pipeline_prefetch','resource_wait_in_process','benchmark_native_frame_sha256'):
        if type(cfg.get(flag,False)) is not bool:raise ValueError(flag+' must be boolean')
    capacity=cfg.get('pipeline_prefetch_frames',1)
    if type(capacity) is not int or capacity not in (1,2):
        raise ValueError('pipeline_prefetch_frames must be 1 or 2')
    poll=cfg.get('resource_wait_poll_seconds',1.5)
    timeout=cfg.get('resource_wait_timeout_seconds',120.)
    for name,value,low,high in (('resource_wait_poll_seconds',poll,1,2),
                               ('resource_wait_timeout_seconds',timeout,1,300)):
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'{name} must be finite and within {low}–{high} seconds')
    return dict(capacity=capacity,poll_seconds=float(poll),thermal_timeout=float(timeout))


class NativeFrames:
    """Persistent GF(A4000) and RIFE(T10), with CPU face fusion between them."""
    def __init__(self,cfg):
        options=pipeline_options(cfg)
        from face_frame_processor import GFPGANProcessor
        from cuda_trt import CudaRife
        import torch
        import cv2
        self.torch=torch;self.cv2=cv2;self.cfg=cfg
        torch.set_num_threads(4);cv2.setNumThreads(4)
        self.model_load_count=0;self.model_load_seconds={}
        self.gate=None
        if cfg.get('resource_wait_in_process',False):
            from native_resource_gate import NativeResourceGate
            self.gate=NativeResourceGate(poll_seconds=options['poll_seconds'],
                thermal_timeout=options['thermal_timeout'],synchronize=self.synchronize_devices,
                empty_cache=self.empty_device_caches)
        self.check_resources(initial=True)
        try:
            with self.work('face-load'):
                load_start=time.monotonic();self.face=GFPGANProcessor(Path(cfg['face_weights']))
                self.model_load_seconds['face']=time.monotonic()-load_start
            self.model_load_count+=1
        except RuntimeError as error:
            if 'WAIT_GPU' in str(error) or 'headroom' in str(error) or error.__class__.__name__=='OutOfMemoryError':
                raise WaitGPU('Shared face initialization must wait: '+str(error)) from error
            raise
        self.check_resources()
        try:
            with self.work('rife-load'):
                load_start=time.monotonic();self.rife=CudaRife(cfg)
                self.model_load_seconds['rife']=time.monotonic()-load_start
            self.model_load_count+=1
        except torch.cuda.OutOfMemoryError as error:
            raise WaitGPU('Shared RIFE initialization must wait') from error
        self.device=self.rife.device
        self.stream=torch.cuda.Stream(device=self.device)
        self.check_resources()

    def synchronize_devices(self):
        # Admission can cool already-hot physical hardware before CUDA is loaded.
        # Do not create CUDA contexts merely to wait at that first admission.
        if not self.torch.cuda.is_initialized():return
        for index in (0,1):
            with self.torch.cuda.device(index):self.torch.cuda.synchronize(index)

    def empty_device_caches(self):
        # Gate invokes this only after GPU leases quiesce. Live models/TRT state
        # remain owned by this object; allocator cache release is not teardown.
        if not self.torch.cuda.is_initialized():return
        for index in (0,1):
            with self.torch.cuda.device(index):self.torch.cuda.empty_cache()

    def work(self,stage):
        return self.gate.work(stage=stage) if self.gate is not None else nullcontext()

    def check_resources(self,initial=False,cancel=None):
        if self.gate is not None:
            return self.gate.check(initial=initial,stage='native-resource-check',cancel=cancel)
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
    options=pipeline_options(cfg);capacity=options['capacity']
    model_start=time.monotonic();loaded_this_run=restorer is None
    restorer=restorer or NativeFrames(cfg)
    torch=restorer.torch;rife=restorer.rife;device=rife.device
    gate=getattr(restorer,'gate',None)
    def gpu_work(stage):
        return gate.work(stage=stage,cancel=cancel) if gate is not None else nullcontext()
    times=defaultdict(float);times['initialization']=time.monotonic()-model_start
    prefetch=cfg.get('pipeline_prefetch',False)
    handoff=None;cancel=threading.Event()
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
        with output.with_name(output.name+'.decoder.log').open('w') as dl,output.with_name(output.name+'.encoder.log').open('w') as el,frame_log.open('w') as fl,ExitStack() as cleanup:
            decoder=subprocess.Popen(decoder_command,stdout=subprocess.PIPE,stderr=dl)
            encoder=subprocess.Popen(encoder_command,stdin=subprocess.PIPE,stderr=el)
            def read_cpu(index):
                nonlocal last_guard
                if time.monotonic()-last_guard>=2:
                    restorer.check_resources(cancel=cancel);last_guard=time.monotonic()
                start=time.monotonic();raw=decoder.stdout.read(needed)
                if len(raw)!=needed:raise ValueError('Truncated native25 frame')
                image=np.frombuffer(raw,dtype=np.uint8).reshape(1080,1920,3).copy();times['decode_read']+=time.monotonic()-start
                return image
            def process_cpu(image,index):
                try:
                    with gpu_work('native-face'):
                        start=time.monotonic();result,detail=restorer.face.process_bgr(image)
                        times['face']+=time.monotonic()-start
                except torch.cuda.OutOfMemoryError as error:
                    if gate is not None:gate.oom(error,stage='native-face')
                    raise WaitGPU('Shared face inference memory changed; retain segment for retry') from error
                for name,seconds in detail.get('times',{}).items():times['face_'+name]+=seconds
                coverage['processed_frames']+=1;coverage['faces_restored']+=detail['faces_restored']
                coverage['restored_frames' if detail['faces_detected'] else 'no_detection_frames']+=1
                if not isinstance(result,np.ndarray) or result.dtype!=np.uint8 or result.shape!=(1080,1920,3):
                    raise ValueError('Native handoff requires CPU BGR8 frames')
                record={'source_frame':source_start+index,'lookahead':index>=count,**detail}
                if cfg.get('benchmark_native_frame_sha256',False):
                    start=time.monotonic()
                    record['restored_bgr_sha256']=hashlib.sha256(memoryview(np.ascontiguousarray(result))).hexdigest()
                    times['native_frame_sha256']+=time.monotonic()-start
                fl.write(json.dumps(record)+'\n')
                return image,result
            @contextmanager
            def producer_context():
                with torch.inference_mode(),torch.cuda.device(restorer.face.device):
                    yield
            def cancel_reader():
                if gate is not None:gate.wake()
                if decoder.poll() is None:
                    decoder.terminate()
                    try:decoder.wait(timeout=3)
                    except subprocess.TimeoutExpired:decoder.kill();decoder.wait()
            if prefetch:
                from native_frame_queue import NativeFrameQueue
                handoff=NativeFrameQueue(read_cpu,process_cpu,primary_count=count,available=available,
                    capacity=capacity,context=producer_context,cancel_reader=cancel_reader,stop=cancel)
                cleanup.callback(handoff.close)
            serial_index=0
            def read():
                nonlocal serial_index
                if handoff is not None:image,result=handoff.get().value
                else:
                    image,result=process_cpu(read_cpu(serial_index),serial_index);serial_index+=1
                start=time.monotonic();pixels=np.ascontiguousarray(result[:,:,::-1])
                times['prepare_rife_cpu']+=time.monotonic()-start
                with gpu_work('native-rife-upload'):
                    start=time.monotonic()
                    tensor=torch.from_numpy(pixels).permute(2,0,1).unsqueeze(0).to(device,dtype=torch.float32)/255
                    restorer.stream.synchronize()
                    times['upload_to_rife']+=time.monotonic()-start
                return Image.fromarray(image[:,:,::-1]),tensor
            with torch.inference_mode(),torch.cuda.device(device),torch.cuda.stream(restorer.stream):
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
                        with gpu_work('native-rife'):
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
            if handoff is not None:handoff.finish()
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
            video_encodes=1,ass_after_rife=True,pipeline_prefetch=prefetch,
            resource_wait_in_process=gate is not None,
            benchmark_native_frame_sha256=cfg.get('benchmark_native_frame_sha256',False),
            timing_semantics='Wall stages may overlap; face substage times are included in face; gate waits are separate',
            frame_queue=dict(handoff.stats) if handoff is not None else None,
            model_load_count=getattr(restorer,'model_load_count',None),
            model_loads_this_run=getattr(restorer,'model_load_count',0) if loaded_this_run else 0,
            model_load_seconds=getattr(restorer,'model_load_seconds',{}),
            resource_gate=gate.summary() if gate is not None else None)
        save(output.with_name(output.name+'.pipeline.json'),receipt)
        print(json.dumps(receipt),flush=True)
    except torch.cuda.OutOfMemoryError as error:
        if gate is not None:gate.oom(error,stage='native-pipeline')
        raise WaitGPU('Shared native inference memory changed; retain segment for retry') from error
    finally:
        cancel.set()
        if gate is not None:gate.wake()
        if handoff is not None:handoff.close()
        for process in [decoder,encoder]:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.kill();process.wait()
