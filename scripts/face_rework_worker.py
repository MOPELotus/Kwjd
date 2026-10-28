#!/usr/bin/env python3
"""Resumable face-only rework of an already subtitled60fps delivered final.

Explicit isolated manifest; no RIFE, new ASS, SRVGG or production-default edits.
Original source survives. Segments are decoded, processed and encoded once;
final concatenation plus unchanged FLAC is stream copied, never re-encoded.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from render_face_samples import digest, save, probe, pcm_update

GIB=2**30


def timestamp():
    return datetime.now(timezone.utc).astimezone().isoformat()


def log(folder,message):
    line=timestamp()+' '+message
    print(line,flush=True)
    with (folder/'progress.log').open('a') as stream:stream.write(line+'\n')


def validate_video(path,frames,folder,*,audio=False):
    """Strict full decode, frame clock and actual output format; no presence gate."""
    started=time.monotonic();data=probe(path)
    video=next(s for s in data['streams'] if s['codec_type']=='video')
    if ((video['width'],video['height'],video['avg_frame_rate'],video['pix_fmt'])
            !=(1920,1080,'60/1','yuv420p10le') or video['codec_name']!='hevc'
            or video.get('color_range')!='tv'
            or any(video.get(k)!='bt709' for k in ['color_space','color_transfer','color_primaries'])):
        raise ValueError('Invalid60fps final video specification')
    if abs(float(data['format']['duration'])-frames/60)>.05:
        raise ValueError('Output duration differs from exact60fps frame clock')
    if audio:
        a=next(s for s in data['streams'] if s['codec_type']=='audio')
        if (a['codec_name'],a['sample_rate'],a['channels'],a.get('bits_per_raw_sample'))!=('flac','48000',2,'24'):
            raise ValueError('Invalid original-mix FLAC specification')
    progress=folder/(path.stem+'.strict-decode.txt')
    with progress.open('w') as out,(folder/(path.stem+'.strict-decode.log')).open('w') as err:
        subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-err_detect','explode','-i',str(path),
            '-map','0:v:0']+(['-map','0:a:0'] if audio else [])+['-progress','pipe:1','-nostats','-f','null','-'],
            stdout=out,stderr=err,check=True)
    counts=[int(x.split('=')[1]) for x in progress.read_text().splitlines() if x.startswith('frame=')]
    if not counts or counts[-1]!=frames:raise ValueError('Full decoded frame count differs')
    pts=[]
    with subprocess.Popen(['ffprobe','-v','error','-select_streams','v:0','-show_packets',
            '-show_entries','packet=pts_time','-of','csv=p=0',str(path)],stdout=subprocess.PIPE,text=True) as proc:
        for line in proc.stdout:
            if line.strip():pts.append(float(line.strip().split(',')[0]))
        if proc.wait():raise RuntimeError('Packet clock audit failed')
    pts.sort()
    error=max((abs(t-i/60) for i,t in enumerate(pts)),default=99)
    if len(pts)!=frames or len(set(pts))!=frames or error>.0011:
        raise ValueError('Frame PTS missing/duplicate/off60fps grid')
    return dict(frames=frames,sha256=digest(path),bytes=path.stat().st_size,
                max_video_grid_error=error,probe=data,qc_seconds=time.monotonic()-started)


def fingerprint(manifest):
    """Own code/dependency/assets/settings bind a distinct checkpoint context."""
    from face_frame_processor import WEIGHTS
    import importlib.metadata
    import importlib.util
    packages={key:importlib.metadata.version(key) for key in
        ['torch','torchvision','facexlib','gfpgan','basicsr','numpy','opencv-python-headless']}
    sources={name:digest(Path(__file__).with_name(name)) for name in
             ['face_rework_worker.py','face_frame_processor.py','render_face_samples.py']}
    import inspect
    from facexlib.utils.face_restoration_helper import FaceRestoreHelper
    sources['installed_face_helper']=hashlib.sha256(inspect.getsource(FaceRestoreHelper).encode()).hexdigest()
    dependency_code={}
    for package in ['gfpgan','facexlib','basicsr']:
        root=Path(next(iter(importlib.util.find_spec(package).submodule_search_locations)))
        dependency_code[package]={str(p.relative_to(root)):digest(p) for p in sorted(root.rglob('*.py'))}
    bundle=dict(manifest=manifest,code=sources,packages=packages,weights=WEIGHTS,
        dependency_python_sources=dependency_code,
        ffmpeg=subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0])
    return hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest(),bundle


def guard(folder):
    if shutil.disk_usage(folder).free<25*GIB:
        raise RuntimeError('WAIT_DISK:25GiB floor')
    rows=subprocess.check_output(['nvidia-smi','--id=0',
        '--query-gpu=temperature.gpu,memory.free','--format=csv,noheader,nounits'],text=True)
    temperature,free=map(int,rows.strip().split(','))
    if temperature>=95 or free<3072:
        raise RuntimeError('WAIT_GPU:temperature/headroom '+rows.strip())
    return dict(temperature=temperature,free_mib=free)


def run(manifest_path):
    manifest=json.loads(manifest_path.read_text());folder=Path(manifest['output'])
    folder.mkdir(parents=True,exist_ok=True)
    lock=(folder/'worker.lock').open('a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    source=Path(manifest['source']);start=int(manifest.get('start_frame',0));total=int(manifest['frames'])
    segment_frames=int(manifest.get('segment_frames',600))
    if (manifest.get('mode')!='face_only_existing60' or manifest.get('fps')!=60
            or not 0<=start<211054 or start%60 or not 0<total<=211054-start
            or not 60<=segment_frames<=1200 or segment_frames%60
            or manifest.get('protected_bottom')!=760 or manifest.get('cq')!=16
            or manifest.get('maxrate_mbps')!=16 or manifest.get('source_sha256')!=
            'f2bae90ba1b50bc7953867433c25b6a70be233828f14257fe646c975e8b4d3fc'):
        raise ValueError('Explicit bounded E49 rework policy required')
    if manifest.get('rife') is not False or manifest.get('burn_ass') is not False:
        raise ValueError('Existing60fps/subtitled final must not repeat RIFE/ASS')
    # Model import installs only an in-process legacy torchvision alias.
    import torch
    import cv2
    import numpy as np
    from face_frame_processor import GFPGANProcessor
    torch.set_num_threads(4);cv2.setNumThreads(4)
    import torchvision.transforms.functional as functional
    sys.modules['torchvision.transforms.functional_tensor']=functional
    fp,bundle=fingerprint(manifest)
    plan_path=folder/'plan.json'
    if plan_path.exists():
        plan=json.loads(plan_path.read_text())
        if plan['fingerprint']!=fp:raise ValueError('Changed processor/config/assets; prior checkpoints retained')
    else:
        if any(folder.glob('segment-*.mkv')):raise ValueError('Unbound old segments; refuse adoption')
        save(plan_path,dict(fingerprint=fp,bundle=bundle,created_at=timestamp()))
    log(folder,'VERIFY_SOURCE full SHA before this worker attempt')
    if digest(source)!=manifest['source_sha256']:raise ValueError('Unique original source changed')
    stat=source.stat();source_stat=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
    # Conservative cap: completed segments plus final copy, original FLAC and1GiB scratch.
    segments=folder/'segments';segments.mkdir(exist_ok=True)
    owned_bytes=sum(p.stat().st_size for p in segments.glob('segment-*.mkv') if '.partial.' not in p.name)
    full_upper=total/60*16e6/8*1.08
    source_info=probe(source);a=next(s for s in source_info['streams'] if s['codec_type']=='audio')
    audio_upper=total/60*48000*2*3
    additional=max(0,full_upper-owned_bytes)+full_upper+audio_upper+GIB
    if (folder/'final.pending.json').exists():
        # Existing, hash-bound media only needs verification and small receipt
        # publication; do not require room for another full output copy.
        additional=64*1024**2
    if shutil.disk_usage(folder).free<25*GIB+additional:
        raise RuntimeError('WAIT_DISK:bounded final+segments+mix budget above25GiB floor')
    state_path=folder/'status.json'
    state=dict(fingerprint=fp,stage='STARTING',frames_completed=0,total_frames=total,fps=60,
        source=str(source),source_sha256=manifest['source_sha256'],source_retained=True,
        pid=os.getpid(),started_at=timestamp(),protected_bottom=760,rife=False,burn_ass=False)
    save(state_path,state)
    processor=None;model_init=0.;completed=[]
    for offset in range(0,total,segment_frames):
        count=min(segment_frames,total-offset);index=offset//segment_frames
        destination=segments/f'segment-{index:05d}.mkv';receipt_path=segments/f'segment-{index:05d}.json'
        pending_path=segments/f'segment-{index:05d}.pending.json'
        if not receipt_path.exists() and pending_path.exists():
            pending=json.loads(pending_path.read_text());candidate=Path(pending['partial_path'])
            if (pending.get('fingerprint')!=fp or pending.get('source_start_frame')!=start+offset
                    or pending.get('frames')!=count or pending.get('processed_frames')!=count
                    or pending.get('protected_bottom_unchanged_frames')!=count
                    or pending.get('failed_frames')!=0 or candidate.parent!=segments
                    or not candidate.name.startswith(f'segment-{index:05d}.')
                    or not candidate.name.endswith('.partial.mkv')):
                raise ValueError('Pending segment publication does not bind this context')
            actual=destination if destination.exists() else candidate
            if not actual.exists() or digest(actual)!=pending['validation']['sha256']:
                raise ValueError('Pending segment media changed; preserve evidence')
            validate_video(actual,count,segments)
            if actual!=destination:candidate.rename(destination)
            save(receipt_path,pending)
            log(folder,f'RECOVER verified pending publication segment={index}')
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text())
            if (receipt.get('fingerprint')!=fp or receipt.get('source_start_frame')!=start+offset
                or receipt.get('frames')!=count or not destination.exists()
                or digest(destination)!=receipt['validation']['sha256']
                or receipt.get('processed_frames')!=count or receipt.get('failed_frames')!=0
                or receipt.get('protected_bottom_unchanged_frames')!=count):
                raise ValueError('Checkpoint evidence mismatch; retain and stop')
            completed.append(receipt)
            state.update(stage='RESUMING_VERIFIED',frames_completed=offset+count,updated_at=timestamp())
            save(state_path,state);continue
        if destination.exists():
            raise ValueError('Unreceipted segment retained; manual recovery required, no presence adoption')
        guard(folder)
        if processor is None:
            log(folder,'INITIALIZE persistent GFPGAN on physical A4000; no RIFE/ASS')
            t=time.monotonic();processor=GFPGANProcessor(Path(manifest['weights']),protected_bottom=760)
            model_init=time.monotonic()-t;guard(folder)
            state['physical_gpu']=subprocess.check_output(['nvidia-smi','--id=0',
                '--query-gpu=name,uuid,driver_version,compute_cap','--format=csv,noheader'],text=True).strip()
        stat=source.stat()
        if (stat.st_ino,stat.st_size,stat.st_mtime_ns)!=source_stat:raise ValueError('Source changed mid-attempt')
        partial=segments/f'segment-{index:05d}.{os.getpid()}.partial.mkv'
        if partial.exists():raise FileExistsError('Retain old partial attempt')
        dec=['ffmpeg','-nostdin','-v','error','-xerror','-err_detect','explode','-threads','4','-filter_threads','4',
            '-ss',format((start+offset)/60,'.9f'),'-i',str(source),'-map','0:v:0','-an',
            '-vf','scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=bgr24',
            '-frames:v',str(count),'-fps_mode','passthrough','-f','rawvideo','-']
        enc=['ffmpeg','-nostdin','-v','warning','-n','-threads','4','-filter_threads','4',
            '-f','rawvideo','-pixel_format','bgr24','-video_size','1920x1080','-framerate','60','-i','pipe:0',
            '-an','-vf','scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=p010le',
            '-c:v','hevc_nvenc','-gpu','0','-preset','p6','-tune','hq','-profile:v','main10',
            '-rc','vbr','-cq','16','-b:v','0','-maxrate','16M','-bufsize','32M',
            '-colorspace','bt709','-color_trc','bt709','-color_primaries','bt709','-color_range','tv',str(partial)]
        entry=dict(fingerprint=fp,source_start_frame=start+offset,frames=count,processed_frames=0,
            restored_frames=0,no_detection_frames=0,faces_restored=0,failed_frames=0,
            protected_bottom_unchanged_frames=0,decode_command=dec,encode_command=enc,
            model_init_seconds=model_init if not completed else 0,started_at=timestamp())
        times=defaultdict(float);begin=time.monotonic();decoder=encoder=None;last_check=0
        log(folder,f'PROCESS segment={index} input={offset}/{total} all60fps frames')
        try:
            frame_log=segments/f'{index:05d}.{os.getpid()}.frames.jsonl'
            with (segments/f'{index:05d}.{os.getpid()}.decode.log').open('w') as dl,(segments/f'{index:05d}.{os.getpid()}.encode.log').open('w') as el,frame_log.open('w') as fl:
                decoder=subprocess.Popen(dec,stdout=subprocess.PIPE,stderr=dl)
                encoder=subprocess.Popen(enc,stdin=subprocess.PIPE,stdout=el,stderr=el)
                for i in range(count):
                    if time.monotonic()-last_check>=2:hardware=guard(folder);last_check=time.monotonic()
                    t=time.monotonic();raw=decoder.stdout.read(1920*1080*3)
                    if len(raw)!=1920*1080*3:raise ValueError('Truncated60fps source decode')
                    image=np.frombuffer(raw,dtype=np.uint8).reshape(1080,1920,3).copy()
                    times['decode_read']+=time.monotonic()-t
                    t=time.monotonic();result,detail=processor.process_bgr(image);times['face_total']+=time.monotonic()-t
                    for key,value in detail['times'].items():times['face_'+key]+=value
                    if not np.array_equal(result[760:],image[760:]):raise ValueError('Subtitle region changed before encode')
                    entry['protected_bottom_unchanged_frames']+=1
                    entry['restored_frames' if detail['faces_detected'] else 'no_detection_frames']+=1
                    entry['faces_restored']+=detail['faces_restored'];entry['processed_frames']+=1
                    fl.write(json.dumps(dict(frame=start+offset+i,**detail,**hardware))+'\n')
                    if i in [0,count-1] and (index<2 or index%60==0):
                        cv2.imwrite(str(segments/f'{index:05d}.{i:04d}.before.png'),image)
                        cv2.imwrite(str(segments/f'{index:05d}.{i:04d}.after.png'),result)
                    t=time.monotonic();encoder.stdin.write(result.tobytes());times['encode_pipe']+=time.monotonic()-t
                    if i%60==59:
                        elapsed=time.monotonic()-begin
                        state.update(stage='PROCESSING',frames_completed=offset+i+1,current_segment=index,
                            updated_at=timestamp(),attempt_segment_fps=(i+1)/elapsed,hardware=hardware)
                        save(state_path,state)
                        log(folder,f'PROGRESS frames={offset+i+1}/{total} segment_fps={(i+1)/elapsed:.3f}')
                t=time.monotonic();encoder.stdin.close()
                if decoder.stdout.read(1) or decoder.wait() or encoder.wait():raise RuntimeError('Decode/encode failed')
                times['encode_flush']=time.monotonic()-t
            entry['pipeline_seconds']=time.monotonic()-begin;entry['stage_seconds']=dict(times)
            entry['frame_log']=str(frame_log);entry['frame_log_sha256']=digest(frame_log)
            entry['validation']=validate_video(partial,count,segments)
            entry['completed_at']=timestamp();entry['partial_path']=str(partial)
            save(pending_path,entry)
            partial.rename(destination)
            save(receipt_path,entry);completed.append(entry)
            state.update(stage='SEGMENT_VERIFIED',frames_completed=offset+count,updated_at=timestamp())
            save(state_path,state);log(folder,f'VERIFIED segment={index} frames={offset+count}/{total}')
        finally:
            for proc in [decoder,encoder]:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
    # Free NN contexts before full-file QC/mux; no source or segment deletion.
    del processor
    torch.cuda.empty_cache()
    listing=folder/'segments.ffconcat'
    listing.write_text('ffconcat version 1.0\n'+''.join(f"file 'segments/segment-{i:05d}.mkv'\n" for i in range(len(completed))))
    final=folder/'S01E49.face-reworked-1080p60.mkv'
    final_intent=folder/'final.pending.json'
    if final_intent.exists():
        evidence=json.loads(final_intent.read_text());candidate=Path(evidence['partial_path'])
        if (evidence.get('fingerprint')!=fp or evidence.get('frames')!=total
                or evidence.get('source_sha256')!=manifest['source_sha256']
                or candidate.parent!=folder or not candidate.name.startswith('final.')
                or not candidate.name.endswith('.partial.mkv')):
            raise ValueError('Final pending publication belongs to another context')
        actual=final if final.exists() else candidate
        if not actual.exists() or digest(actual)!=evidence['validation']['sha256']:
            raise ValueError('Final pending media changed; preserve evidence')
        validate_video(actual,total,folder,audio=True)
        pcm=hashlib.sha256();pcm_update(actual,pcm)
        if pcm.hexdigest()!=evidence['audio_pcm_sha256']:raise ValueError('Recovered final PCM changed')
        if actual!=final:candidate.rename(final)
        save(folder/'technical_result.json',evidence)
        state.update(stage='TECHNICAL_PASS_PENDING_VISUAL',frames_completed=total,
            output=str(final),sha256=evidence['validation']['sha256'],updated_at=timestamp())
        save(state_path,state);log(folder,'RECOVER final publication verified '+str(final));return
    if final.exists():raise FileExistsError('Unbound final retained, not overwritten')
    partial=folder/f'final.{os.getpid()}.partial.mkv'
    command=['ffmpeg','-nostdin','-v','warning','-n','-f','concat','-safe','1','-i',str(listing),
        '-ss',format(start/60,'.9f'),'-i',str(source),'-map','0:v:0','-map','1:a:0',
        '-c','copy']
    if start or total!=211054:
        command+=['-c:a','flac','-sample_fmt','s32','-bits_per_raw_sample','24']
    command+=['-t',format(total/60,'.9f'),'-map_metadata','-1',str(partial)]
    state.update(stage='FINAL_MUX_AND_QC',updated_at=timestamp());save(state_path,state)
    log(folder,'FINAL_MUX stream-copy verified video segments + unchanged original FLAC')
    with (folder/'mux.log').open('w') as stream:subprocess.run(command,stderr=stream,stdout=stream,check=True)
    validation=validate_video(partial,total,folder,audio=True)
    actual=hashlib.sha256();pcm_update(partial,actual)
    expected=hashlib.sha256()
    if start==0 and total==211054:pcm_update(source,expected)
    else:pcm_update(source,expected,start/60,total/60)
    if actual.hexdigest()!=expected.hexdigest():raise ValueError('Original mixed PCM changed')
    if digest(source)!=manifest['source_sha256']:raise ValueError('Unique original source hash changed')
    final_report=dict(status='TECHNICAL_PASS_PENDING_VISUAL',fingerprint=fp,source_sha256=manifest['source_sha256'],
        source_retained=True,frames=total,processed_frames=sum(x['processed_frames'] for x in completed),
        restored_frames=sum(x['restored_frames'] for x in completed),no_detection_frames=sum(x['no_detection_frames'] for x in completed),
        failed_frames=0,protected_bottom_unchanged_frames=total,protected_bottom=760,
        validation=validation,audio_pcm_sha256=actual.hexdigest(),output=str(final),
        pipeline_seconds=sum(x['pipeline_seconds'] for x in completed),model_init_seconds=model_init,
        rife=False,burn_ass=False,completed_at=timestamp(),mux_command=command,
        limitations='Face fusion limited to y<760 to protect existing subtitle strip before encode. Final lossy encoding may change pixels. No claim of perfect temporal restoration. No source/segments deleted.')
    final_report['partial_path']=str(partial)
    save(final_intent,final_report)
    partial.rename(final);save(folder/'technical_result.json',final_report)
    state.update(stage='TECHNICAL_PASS_PENDING_VISUAL',frames_completed=total,output=str(final),
                 sha256=validation['sha256'],updated_at=timestamp());save(state_path,state)
    log(folder,'TECHNICAL_PASS_PENDING_VISUAL '+str(final)+' SHA256='+validation['sha256'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path)
    args=parser.parse_args()
    try:run(args.manifest)
    except BaseException as error:
        manifest=json.loads(args.manifest.read_text());folder=Path(manifest['output'])
        folder.mkdir(parents=True,exist_ok=True);log(folder,'ERROR '+repr(error))
        save(folder/'last_error.json',dict(at=timestamp(),error=repr(error),pid=os.getpid()))
        state_path=folder/'status.json'
        if state_path.exists():
            state=json.loads(state_path.read_text());state.update(stage='ERROR',error=repr(error),updated_at=timestamp())
            save(state_path,state)
        if isinstance(error,RuntimeError) and ('WAIT_' in str(error) or 'headroom' in str(error)):
            raise SystemExit(76)
        if error.__class__.__name__=='OutOfMemoryError':raise SystemExit(76)
        raise


if __name__=='__main__':main()
