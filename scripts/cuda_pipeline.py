"""Streaming, persistent CUDA restoration and RIFE; compressed segment output.

RGB frames travel to CUDA once for restoration and stay there through RIFE.
Only final RGB output crosses back for FFmpeg/NVENC. No PNG frame exchange.
"""
import json
from pathlib import Path
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from enhance_stage import check_space


def run(master, output, episode, cfg, *, source_start=0, source_frames=None,
        prepared=None, restorer=None):
    import enhance_backend as engine
    from cuda_restore import Restorer
    from cuda_rife import CudaRife
    from PIL import Image
    begin=time.monotonic()
    if Path(output).exists():raise ValueError('Refusing to overwrite CUDA output')
    if prepared is None:
        from master_policy import reject_retired
        from enhance_stage import sha256
        reject_retired(sha256(master))
        info=engine.source_info(master);full=engine.source_packet_count(master)
    else:
        stat=Path(master).stat()
        if (stat.st_ino,stat.st_size,stat.st_mtime_ns)!=prepared['stat']:
            raise ValueError('Master changed between CUDA segments')
        info=prepared['info'];full=prepared['count']
    count=min(full-source_start,source_frames if source_frames is not None else full)
    if source_start<0 or source_start%5 or count<=0:raise ValueError('Invalid CUDA segment clock')
    if abs(full/25-float(info['format']['duration']))>.08:raise ValueError('Master frame clock differs')
    if not cfg['restore']:raise ValueError('CUDA production pipeline requires real restoration')
    timing=dict(decode=0.,restore=0.,peer_transfer=0.,rife=0.,output_transfer=0.,encode_pipe=0.,cut_detection=0.)
    initialization=time.monotonic()
    restorer=restorer or Restorer(cfg)
    if not hasattr(restorer,'rife'):restorer.rife=CudaRife(cfg)
    rife=restorer.rife;torch=restorer.torch
    timing['initialization']=time.monotonic()-initialization
    decoder=encoder=pool=None;emitted=generated=cuts=0;cut_frames=[];frames=engine.output_count(count)
    available=min(count+1,full-source_start)
    try:
        decoder=subprocess.Popen(['ffmpeg','-nostdin','-v','error','-ss',str(source_start/25),'-i',str(master),
            '-map','0:v:0','-frames:v',str(available),'-vsync','0','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],stdout=subprocess.PIPE)
        encoder=subprocess.Popen(['ffmpeg','-nostdin','-v','warning','-n','-f','rawvideo','-pix_fmt','rgb24',
            '-s','1920x1080','-r','60','-i','pipe:0','-an','-c:v','hevc_nvenc','-gpu',str(cfg.get('encoder_gpu',1)),
            '-preset','p6','-rc','vbr','-cq','18','-b:v','0','-maxrate','40M','-bufsize','80M',
            '-vf','scale=out_color_matrix=bt709:out_range=tv','-pix_fmt','p010le','-color_range','tv',
            '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709',str(output)],stdin=subprocess.PIPE)
        def read():
            stamp=time.monotonic();buf=bytearray();needed=1920*1080*3
            while len(buf)<needed:
                block=decoder.stdout.read(needed-len(buf))
                if not block:break
                buf.extend(block)
            if len(buf)!=needed:raise ValueError('Truncated CUDA decoded frame')
            image=Image.frombytes('RGB',(1920,1080),bytes(buf))
            timing['decode']+=time.monotonic()-stamp
            stamp=time.monotonic();tensor=restorer.restore_tensor(image)
            torch.cuda.synchronize(restorer.device)
            timing['restore']+=time.monotonic()-stamp
            return image,tensor
        def to_rife(tensor):
            stamp=time.monotonic()
            if tensor.device!=rife.device:
                tensor=tensor.to(rife.device)
                torch.cuda.synchronize(rife.device)
            timing['peer_transfer']+=time.monotonic()-stamp
            return tensor
        previous,a=read();a=to_rife(a)
        prefetch=bool(cfg.get('pipeline_prefetch',False)) and restorer.device!=rife.device
        future=None
        if prefetch:
            pool=ThreadPoolExecutor(max_workers=1)
            if available>1:future=pool.submit(read)
        with torch.inference_mode():
            for index in range(count):
                if index+1<available:
                    following,b=future.result() if prefetch else read()
                    # Complete this source frame's cross-device handoff BEFORE
                    # launching the next GPU0 restoration. The prefetch worker
                    # never queues a peer-copy barrier or device fence on GPU1.
                    b=to_rife(b)
                    if prefetch and index+2<available:future=pool.submit(read)
                    stamp=time.monotonic();cut=engine.is_cut(previous,following)
                    timing['cut_detection']+=time.monotonic()-stamp
                else:following,b,cut=previous,a,False
                cuts+=int(cut)
                if cut:cut_frames.append(source_start+index+1)
                while emitted<frames:
                    left,alpha=engine.frame_position(emitted)
                    if left!=index:break
                    if alpha and not cut and index+1<available:
                        stamp=time.monotonic();frame=rife.interpolate(a,b,float(alpha))
                        timing['rife']+=time.monotonic()-stamp;generated+=1
                    else:frame=a
                    stamp=time.monotonic()
                    pixels=(frame[0].permute(1,2,0)*255).round().clamp_(0,255).to(torch.uint8).contiguous().cpu().numpy().tobytes()
                    timing['output_transfer']+=time.monotonic()-stamp
                    stamp=time.monotonic();encoder.stdin.write(pixels)
                    timing['encode_pipe']+=time.monotonic()-stamp;emitted+=1
                previous,a=following,b
                if index%25==0:
                    check_space(Path(output).parent,16*1024**2,25)
                    if index%250==0:
                        print(json.dumps({'episode':episode,'segment_source_start':source_start,
                            'source_frames_processed':index+1,'source_frames_total':count,
                            'output_frames':emitted,'elapsed':time.monotonic()-begin}),flush=True)
        if decoder.stdout.read(1):raise ValueError('Unexpected additional CUDA source frames')
        stamp=time.monotonic();encoder.stdin.close()
        if decoder.wait()!=0 or encoder.wait()!=0:raise RuntimeError('CUDA FFmpeg decode/encode failed')
        timing['encode_flush']=time.monotonic()-stamp
        if emitted!=frames:raise ValueError('CUDA frame mapping incomplete')
        wall=time.monotonic()-begin
        print(json.dumps({'cuda_pipeline':True,'episode':episode,'source_start':source_start,'input_frames':count,
            'output_frames':frames,'interpolated_frames':generated,'cut_intervals':cuts,'wall_seconds':wall,
            'source_fps':count/wall,'timing_seconds':timing,'timings_overlap':prefetch,
            'frame_io':'streaming; no PNG exchange'}),flush=True)
        print(json.dumps({'cut_source_frame_indices':cut_frames}),flush=True)
    finally:
        for process in (decoder,encoder):
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        if pool is not None:pool.shutdown(wait=True,cancel_futures=True)
