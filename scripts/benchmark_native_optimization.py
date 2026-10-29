#!/usr/bin/env python3
"""Isolated E50 native full-chain experiment. Never grants production approval.

Requires a live controller's hash-bound isolation token and takes the worker lock.
No retries by default; every retry gets new paths and its wasted wall is recorded.
Physical playback/screenshot review remains a separate human/controller evidence step.
"""
import argparse
from contextlib import contextmanager
import fcntl
import gc
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import traceback
import time

CASES = [('serial', False, False, 1), ('serial-gate', False, True, 1),
         ('overlap1-gate', True, True, 1), ('overlap2-gate', True, True, 2)]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, data):
    # All caller paths are new immutable artifacts, except the final report.
    with Path(path).open('x') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def clock_bounds(start, count, full):
    if (type(start) is not int or type(count) is not int or start < 0
            or start % 5 or count <= 0 or count % 5 or count > 250
            or start + count > full):
        raise ValueError('Explicit <=250 native frames on the five-frame clock required')
    return dict(input_frames=count, lookahead_frames=int(start+count < full),
                output_frames=count*12//5, audio_start_sample=start*1920,
                audio_sample_count=count*1920)


def new_output(path, post):
    path = Path(path).absolute()
    base = (Path(post)/'cache/scratch').resolve()
    if path.exists() or path.is_symlink() or not path.resolve().is_relative_to(base) or path.resolve() == base:
        raise ValueError('Use a new directory strictly under post/cache/scratch')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    return path


def selected_cases(names=None):
    names=[item[0] for item in CASES] if names is None else list(names)
    allowed={item[0] for item in CASES}
    if not names or names[0]!='serial' or len(set(names))!=len(names) or not set(names)<=allowed:
        raise ValueError('Cases must be unique known modes, with serial baseline first')
    return names


def variant_configs(active, output, runid, names=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', runid):
        raise ValueError('Unsafe run ID')
    raw = json.loads(Path(active).read_text())
    result = []
    modes={item[0]:item for item in CASES}
    for case_name in selected_cases(names):
        name,overlap,gate,depth=modes[case_name]
        cfg = dict(raw, pipeline_prefetch=overlap, resource_wait_in_process=gate,
                   pipeline_prefetch_frames=depth, benchmark_native_frame_sha256=True)
        path = Path(active).parent/f'runtime.optimization-{runid}-{name}.json'
        # Keep backend/runtime's location: engine.config derives post_root from it.
        if Path(active).parent.name != 'backend':
            raise ValueError('Active config must be in post/backend')
        result.append((name, path, cfg))
    if any(path.exists() or path.is_symlink() for _, path, _ in result):
        raise FileExistsError('Variant config already exists')
    for _, path, cfg in result:
        save(path, cfg)
        path.chmod(0o444)
    return [(name, path) for name, path, _ in result]


def process_identity(pid):
    proc = Path('/proc')/str(pid)
    stat = (proc/'stat').read_text().rsplit(')', 1)[1].split()
    return dict(start_ticks=stat[19], terminal=os.readlink(proc/'fd/0'), uid=proc.stat().st_uid)


def validate_token(path, episode, master_sha, fp, *, now=None):
    path = Path(path)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
        raise ValueError('Isolation token must be same-user owned and not group/world writable')
    token = json.loads(path.read_text())
    identity = process_identity(int(token['owner_pid']))
    now = time.time() if now is None else now
    if (token.get('episode') != episode or token.get('master_sha256') != master_sha
            or token.get('backend_fingerprint') != fp or token.get('production_paused') is not True
            or not now < token['expires_unix'] <= now+3600
            or str(token.get('owner_start_ticks')) != identity['start_ticks']
            or token.get('owner_terminal') != identity['terminal'] or identity['uid'] != os.getuid()
            or int(token['owner_pid']) == os.getpid()):
        raise ValueError('Stale/unbound isolation token; controller must remain alive')
    return token


@contextmanager
def exclusive(path):
    with Path(path).open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def probe_compute():
    # NativeFrames acquires and retains this actual lease during model lifetime.
    with exclusive(Path.home()/'qingqingwu/post-home/.face-compute.lock'):
        pass


class Telemetry:
    """Bounded append-only physical NVIDIA samples; no device mutation."""
    def __init__(self, path, interval=1):
        self.path=Path(path);self.interval=interval;self.stop=threading.Event()
        self.peaks={};self.errors=[];self.samples=0;self.ready=threading.Event();self.latest=None

    def collect(self):
        with self.path.open('x') as stream:
            while not self.stop.is_set():
                began=time.monotonic();row=dict(unix=time.time(), monotonic=began)
                try:
                    data=subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,name,memory.used,memory.free,temperature.gpu,utilization.gpu',
                        '--format=csv,noheader,nounits'], text=True, timeout=5)
                    row['gpus']=[]
                    for line in data.strip().splitlines():
                        index, uuid, name, used, free, temp, util=[x.strip() for x in line.split(',')]
                        gpu=dict(index=int(index),uuid=uuid,name=name,used_mib=int(used),free_mib=int(free),temperature_c=int(temp),utilization=int(util))
                        row['gpus'].append(gpu)
                        peak=self.peaks.setdefault(uuid,dict(name=name,index=int(index),peak_used_mib=0,min_free_mib=int(free),peak_temperature_c=0))
                        peak['peak_used_mib']=max(peak['peak_used_mib'],int(used))
                        peak['min_free_mib']=min(peak['min_free_mib'],int(free))
                        peak['peak_temperature_c']=max(peak['peak_temperature_c'],int(temp))
                    self.samples+=1
                except Exception as error:
                    row['error']=repr(error);self.errors.append(repr(error))
                row['query_seconds']=time.monotonic()-began
                stream.write(json.dumps(row)+'\n');stream.flush();self.latest=row;self.ready.set()
                self.stop.wait(self.interval)

    def __enter__(self):
        self.thread=threading.Thread(target=self.collect, daemon=True);self.thread.start()
        if not self.ready.wait(6):
            self.__exit__();raise RuntimeError('Physical telemetry query timed out before model load')
        names={gpu['name'] for gpu in self.peaks.values()}
        if self.errors or not {'Tesla T10','NVIDIA RTX A4000'}.issubset(names):
            self.__exit__();raise RuntimeError('Required physical GPU telemetry unavailable before model load')
        return self

    def __exit__(self, *args):
        self.stop.set();self.thread.join(timeout=7)
        if self.thread.is_alive():raise RuntimeError('Telemetry failed to stop')

    def summary(self):
        return dict(samples=self.samples,peaks=self.peaks,errors=self.errors,
                    path=str(self.path),sha256=digest(self.path))



def wait_cold_start(path, result, timeout=120):
    """Observe unloaded physical GPUs; never change fans/power or wait forever."""
    began=time.monotonic()
    result.update(status='WAITING',timeout_seconds=timeout,
                  thresholds_c={'0':85,'1':78},samples_path=str(path))
    telemetry=Telemetry(path)
    try:
        with telemetry:
            while True:
                latest=telemetry.latest
                if latest is None or latest.get('error') or telemetry.errors:
                    raise RuntimeError('Cold-start physical telemetry failed')
                gpus={gpu['index']:gpu for gpu in latest['gpus']}
                if (gpus.get(0,{}).get('name')!='NVIDIA RTX A4000'
                        or gpus.get(1,{}).get('name')!='Tesla T10'):
                    raise RuntimeError('Cold-start physical GPU indices differ')
                result['last_raw_sample']=latest
                result.setdefault('first_raw_sample',latest)
                if gpus[0]['temperature_c']<=85 and gpus[1]['temperature_c']<=78:
                    result['status']='READY';break
                remaining=timeout-(time.monotonic()-began)
                if remaining<=0:
                    result['status']='TIMEOUT'
                    raise TimeoutError('Physical GPUs did not reach cold-start thresholds within120s')
                time.sleep(min(1,remaining))
    except Exception as error:
        result['error']=repr(error)
        if result['status']=='WAITING':result['status']='FAILED'
        raise
    finally:
        result['wait_seconds']=time.monotonic()-began
        result['telemetry']=telemetry.summary()


def media_hash(path, output, *, audio=False):
    args=['ffmpeg','-nostdin','-v','error','-xerror','-err_detect','explode','-i',str(path)]
    args += ['-map','0:a:0','-c:a','pcm_s24le'] if audio else ['-map','0:v:0','-an','-pix_fmt','yuv420p10le']
    args += ['-f','framemd5','-hash','sha256',str(output)]
    subprocess.run(args,check=True,timeout=600)
    rows=[line for line in Path(output).read_text().splitlines() if line and not line.startswith('#')]
    return dict(path=str(output),sha256=digest(output),frames=len(rows),
                frame_hashes=[line.rsplit(',',1)[-1].strip() for line in rows])


def pcm_hash(path, output):
    # Hash the complete decoded byte stream, independent of codec packet boundaries.
    subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(path),'-map','0:a:0',
                    '-c:a','pcm_s24le','-f','hash','-hash','sha256',str(output)],check=True,timeout=600)
    return Path(output).read_text().strip()


def audio_slice(master, folder, clocks):
    target=folder/'native-mix.flac'
    filt=f"atrim=start_sample={clocks['audio_start_sample']}:end_sample={clocks['audio_start_sample']+clocks['audio_sample_count']},asetpts=PTS-STARTPTS"
    subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(master),'-map','0:a:0',
        '-af',filt,'-c:a','flac','-sample_fmt','s32','-bits_per_raw_sample','24',str(target)],check=True,timeout=600)
    expected=folder/'source-mix-pcm.sha256'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(master),'-map','0:a:0','-af',filt,
        '-c:a','pcm_s24le','-f','hash','-hash','sha256',str(expected)],check=True,timeout=600)
    actual=pcm_hash(target,folder/'slice-pcm.sha256')
    if actual != expected.read_text().strip():raise ValueError('FLAC slice changed native PCM24')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(target)],text=True))['streams'][0]
    if int(probe['duration_ts'])*Fraction(probe['time_base'])*48000 != clocks['audio_sample_count']:
        raise ValueError('Audio slice sample count differs')
    return target,actual


def frame_evidence(path, start, clocks):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    expected=list(range(start,start+clocks['input_frames']+clocks['lookahead_frames']))
    if [row['source_frame'] for row in rows] != expected:
        raise ValueError('Native frame order/coverage lost or duplicated')
    if [row['lookahead'] for row in rows] != [i>=clocks['input_frames'] for i in range(len(rows))]:
        raise ValueError('Native lookahead differs')
    hashes=[row.get('restored_bgr_sha256') for row in rows]
    if any(value is not None and (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)) for value in hashes):
        raise ValueError('Invalid restored BGR byte hash')
    return dict(sha256=digest(path),frames=len(rows),lost_native_frames=0,
                raw_native_hashes=hashes if all(hashes) else None,
                raw_native_hash_limitation=None if all(hashes) else 'Pipeline does not expose per-frame restored bytes; frame log is processing evidence only')


def release_models(processor):
    """Drain both physical devices, release owners/caches, then surrender lease.

    CUDA contexts may stay resident until process exit; this releases model owners
    and allocator caches without claiming that the CUDA context was destroyed.
    """
    torch=processor.torch
    cfg=processor.cfg
    devices=sorted({int(cfg['restore_gpu']),int(cfg['gpu'])})
    lease=getattr(getattr(processor,'face',None),'_lease',None)
    errors=[]
    for device in devices:
        try:torch.cuda.synchronize(device)
        except Exception as error:errors.append(f'synchronize device {device}: {error!r}')
    processor.__dict__.clear()
    try:gc.collect()
    except Exception as error:errors.append(f'collect owners: {error!r}')
    for device in devices:
        try:
            with torch.cuda.device(device):torch.cuda.empty_cache()
        except Exception as error:errors.append(f'empty cache device {device}: {error!r}')
    if lease is not None:
        try:lease.close()
        except Exception as error:errors.append(f'close compute lease: {error!r}')
    if errors:raise RuntimeError('; '.join(errors))


def clear_failed_frames(error):
    """Retain textual error evidence while dropping completed CUDA owner frames."""
    seen=set()
    pending=[error]
    while pending:
        current=pending.pop()
        if id(current) in seen:continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        for child in (current.__cause__,current.__context__):
            if child is not None:pending.append(child)


def compare(base, candidate):
    left=base['video_hashes']['frame_hashes'];right=candidate['video_hashes']['frame_hashes']
    if len(left)!=len(right):raise ValueError('Decoded comparison frame count differs')
    return dict(decoded_frames=len(left),different_decoded_frames=sum(a!=b for a,b in zip(left,right)),
                cuts_equal=base['pipeline']['cuts']==candidate['pipeline']['cuts'],
                raw_native_equal=(base['frame_evidence']['raw_native_hashes']==candidate['frame_evidence']['raw_native_hashes'])
                    if base['frame_evidence']['raw_native_hashes'] and candidate['frame_evidence']['raw_native_hashes'] else None,
                pcm_equal=base.get('pcm_sha256')==candidate.get('pcm_sha256') if base.get('pcm_sha256') else None)


def execute(args):
    import enhance_backend as engine
    from segmented_backend import fingerprint
    from native_face_recipe import validate_recipe
    from native_face_pipeline import NativeFrames, run, validate_pipeline
    from face_rework_worker import validate_video
    from enhance_stage import WaitGPU, check_space
    from storage_policy import minimum_free_gib
    suite_start=time.monotonic()
    names=selected_cases(getattr(args,'cases',None))
    cold=bool(getattr(args,'cold_start_each_case',False))
    active=args.config.resolve();active_sha=digest(active);post=active.parent.parent
    if active.name!='runtime.native-active.json':
        raise ValueError('Explicit existing runtime.native-active.json required')
    cfg=engine.config(active)
    fp=fingerprint(cfg)
    if args.episode!='S01E50' or fp!=args.backend_fingerprint:
        raise ValueError('Explicit E50/current backend fingerprint required')
    if cfg.get('restoration_backend')!='cuda_gfpgan_fused':raise ValueError('Native fused config required')
    master=args.master.resolve();master_sha=digest(master)
    if master_sha!=args.master_sha256:raise ValueError('Master SHA differs')
    recipe=validate_recipe(cfg['native_recipe'],cfg['native_recipe_sha256'])
    if recipe['episode']!=args.episode or recipe['master_sha256']!=master_sha or recipe['scope']!='production':
        raise ValueError('Use approved production recipe for exact native master')
    info=engine.source_info(master);full=engine.source_packet_count(master)
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    if video['avg_frame_rate']!='25/1' or (video['width'],video['height'])!=(1920,1080):
        raise ValueError('Original native 1080p25 master required')
    clocks=clock_bounds(args.source_start,args.source_frames,full)
    if args.with_audio:
        audio=next(s for s in info['streams'] if s['codec_type']=='audio')
        if (audio.get('codec_name'),audio.get('sample_rate'),audio.get('channels'),audio.get('bits_per_raw_sample'))!=('pcm_s24le','48000',2,'24'):
            raise ValueError('Exact native 48k stereo PCM24 audio required')
    token=validate_token(args.isolation_token,args.episode,master_sha,fp)
    check_space(post/'cache/scratch',2*1024**3,minimum_free_gib(cfg))
    output=new_output(args.output,post)
    report=dict(schema_version=1,episode=args.episode,master=str(master),master_sha256=master_sha,
        active_config_sha256=active_sha,backend_fingerprint=fp,source_start=args.source_start,
        source_total_frames=full,clocks=clocks,isolation_token=token,cases=[],status='FAILED',
        production_approval=False,final_approval=False,cold_start_each_case=cold,selected_cases=names,
        timing_scope=('Complete suite includes preparation/shared audio/final model teardown; each case complete wall includes prior-model release, cooling, init/run/decode/PTS/hash/mux. Cold mode releases/reloads every case and waits for GPU0<=85C/T10<=78C (maximum120s).' if cold else 'Complete suite includes preparation/shared audio/model teardown; case complete wall includes prior-model release, init/run/decode/PTS/hash/mux. Gate modes share a warm instance; compare both init-inclusive and run-only timings.'),
        visual_review=dict(status='PENDING',continuous_samples='Each case sample.mkv covers the entire bounded native interval',
            requirement='Controller must play each sample on physical GPU, operate player and inspect screenshots; decode/hash checks do not establish visual quality'),
        recovery_scope='Reports actual gate waits/OOM/failures only; absent real recovery events does not prove recovery; no injected GPU pressure or thermal workload')
    stat=master.stat();prepared=dict(sha256=master_sha,count=full,info=info,
        stat=(stat.st_ino,stat.st_size,stat.st_mtime_ns))
    processor=None;total_loads=0;failure=None
    try:
        with exclusive(post/'jobs/.worker.lock'):
            variants=variant_configs(active,output,args.run_id,names)
            save(output/'input-binding.json',dict(master_sha256=master_sha,active_config_sha256=active_sha,
                recipe_sha256=cfg['native_recipe_sha256'],recipe=recipe,prepared_frame_count=full,clocks=clocks))
            audio,pcm=(audio_slice(master,output,clocks) if args.with_audio else (None,None))
            for name,path in variants:
                case_start=time.monotonic()
                validate_token(args.isolation_token,args.episode,master_sha,fp)
                case_cfg=engine.config(path);case_fp=fingerprint(case_cfg)
                if case_cfg['post_root']!=str(post):raise ValueError('Variant post_root changed')
                case=dict(name=name,config=str(path),config_sha256=digest(path),backend_fingerprint=case_fp,attempts=[])
                report['cases'].append(case)
                if processor is not None and (cold or bool(processor.gate)!=case_cfg.get('resource_wait_in_process',False)):
                    release_start=time.monotonic()
                    try:release_models(processor)
                    except Exception as error:
                        report['cleanup_error']=repr(error);report['requires_process_exit']=True
                        raise
                    finally:
                        processor=None;case['prior_model_release_seconds']=time.monotonic()-release_start
                if cold:
                    case['cold_start_wait']={}
                    try:wait_cold_start(output/f'{name}.cold-start-gpu.jsonl',case['cold_start_wait'])
                    finally:case['complete_wall_seconds']=time.monotonic()-case_start
                for attempt in range(args.max_retries+1):
                    folder=output/f'{name}-attempt{attempt}';folder.mkdir()
                    sample=folder/'sample.mkv';began=time.monotonic()
                    item=dict(attempt=attempt,output=str(sample),model_reused=processor is not None)
                    case['attempts'].append(item)
                    telemetry=Telemetry(folder/'gpu.jsonl')
                    try:
                        with telemetry:
                            init=time.monotonic()
                            if processor is None:
                                probe_compute()
                                try:processor=NativeFrames(case_cfg)
                                except Exception:
                                    item['init_failed']=True;item['requires_process_exit']=True
                                    item['model_init_seconds']=time.monotonic()-init
                                    report['requires_process_exit']=True
                                    raise
                                loaded=processor.model_load_count;total_loads+=loaded
                            else:loaded=0
                            item['model_init_seconds']=time.monotonic()-init
                            item['model_loads_this_attempt']=loaded
                            item['model_load_count_instance']=processor.model_load_count
                            item['model_load_seconds']=dict(processor.model_load_seconds)
                            item['gate_before']=processor.gate.summary() if processor.gate else None
                            run_start=time.monotonic()
                            run(master,sample,args.episode,case_cfg,source_start=args.source_start,
                                source_frames=args.source_frames,prepared=prepared,restorer=processor)
                            item['run_seconds']=time.monotonic()-run_start
                            item['generation_with_init_seconds']=time.monotonic()-began
                            item['gate_after']=processor.gate.summary() if processor.gate else None
                            item['pipeline_binding']=validate_pipeline(sample,case_cfg,args.source_start,args.source_frames,full)
                            case['pipeline']=json.loads(sample.with_name(sample.name+'.pipeline.json').read_text())
                            case['gfpgan_exercised']=case['pipeline']['coverage']['restored_frames']>0
                            if not case['gfpgan_exercised']:
                                raise ValueError('Choose an explicit bounded window that actually exercises face restoration')
                            case['frame_evidence']=frame_evidence(sample.with_name(sample.name+'.face-frames.jsonl'),args.source_start,clocks)
                            if case['frame_evidence']['raw_native_hashes'] is None:
                                raise ValueError('Requested restored BGR byte hashes missing from native frame evidence')
                            case['validation']=validate_video(sample,clocks['output_frames'],folder)
                            case['video_hashes']=media_hash(sample,folder/'decoded-video.sha256')
                            if case['video_hashes']['frames']!=clocks['output_frames']:raise ValueError('Decoded hash coverage differs')
                            if audio:
                                mux=folder/'sample-with-native-mix.mkv'
                                subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(sample),'-i',str(audio),
                                    '-map','0:v:0','-map','1:a:0','-c','copy',str(mux)],check=True,timeout=600)
                                case['audio_validation']=validate_video(mux,clocks['output_frames'],folder,audio=True)
                                case['pcm_sha256']=pcm_hash(mux,folder/'mux-pcm.sha256')
                                if case['pcm_sha256']!=pcm:raise ValueError('Mux changed mixed PCM')
                                case['continuous_sample']=str(mux)
                            else:case['continuous_sample']=str(sample)
                            item['status']='CHECKED'
                        item['telemetry']=telemetry.summary()
                        names={gpu['name'] for gpu in item['telemetry']['peaks'].values()}
                        if not {'Tesla T10','NVIDIA RTX A4000'}.issubset(names) or telemetry.errors:
                            raise ValueError('Physical GPU telemetry missing/incomplete')
                        break
                    except Exception as error:
                        item['status']='FAILED';item['error']=repr(error)
                        item['traceback']=''.join(traceback.format_exception(type(error),error,error.__traceback__))
                        clear_failed_frames(error)
                        item['telemetry']=telemetry.summary()
                        if processor is not None:
                            item['gate_failure']=processor.gate.summary() if processor.gate else None
                            try:release_models(processor)
                            except Exception as cleanup_error:
                                item['cleanup_error']=repr(cleanup_error)
                                item['requires_process_exit']=True;report['requires_process_exit']=True
                            finally:processor=None
                        retry=(isinstance(error,WaitGPU) and attempt<args.max_retries
                               and not item.get('requires_process_exit',False))
                        item['retry_authorized']=retry
                        if not retry:raise
                    finally:
                        item['complete_attempt_wall_seconds']=time.monotonic()-began
                case['complete_attempts_wall_seconds']=sum(x['complete_attempt_wall_seconds'] for x in case['attempts'])
                case['complete_wall_seconds']=time.monotonic()-case_start
                case['comparison_to_serial']=compare(report['cases'][0],case)
                if not case['comparison_to_serial']['cuts_equal']:raise ValueError('Cut decisions changed')
                if digest(active)!=active_sha or digest(master)!=master_sha:
                    raise ValueError('Active config/master changed; artifacts retained')
                save(output/f'{name}.result.json',case)
            report['status']='TECHNICAL_BENCHMARK_COMPLETE_VISUAL_REVIEW_PENDING'
    except Exception as error:
        failure=error;report['error']=repr(error)
    finally:
        if processor is not None:
            try:release_models(processor)
            except Exception as error:
                report['cleanup_error']=repr(error);report['status']='FAILED';failure=failure or error
                report['requires_process_exit']=True
        report['complete_suite_wall_seconds']=time.monotonic()-suite_start
        report['total_successful_model_loads']=total_loads
        report['model_load_limitations']='Counts are successful load completions; failed initialization may allocate/load partially and is separately recorded in attempt errors'
        report['lost_native_frames']=sum(c.get('frame_evidence',{}).get('lost_native_frames',0) for c in report['cases']) if not failure else None
        report['retained_intermediates']=str(output)
        save(output/'result.json',report)
    if failure:raise failure
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('master','config','output','isolation-token'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('episode','master-sha256','backend-fingerprint','run-id'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--source-start',type=int,required=True)
    parser.add_argument('--source-frames',type=int,required=True)
    parser.add_argument('--max-retries',type=int,choices=(0,1),default=0)
    parser.add_argument('--with-audio',action='store_true')
    parser.add_argument('--cold-start-each-case',action='store_true',help='Release/reload models each case; wait at most120s for physical GPU0<=85C and GPU1<=78C')
    parser.add_argument('--cases',nargs='+',choices=[item[0] for item in CASES],help='Selected modes in requested order; serial must be first')
    args=parser.parse_args()
    execute(args)


if __name__=='__main__':
    main()
