#!/usr/bin/env python3
"""Actual bounded current-master test for the native one-encode backend."""
from storage_policy import minimum_free_gib
import argparse
import json
import os
from pathlib import Path
import time

from enhance_stage import atomic_json,sha256,check_space,WaitDisk,WaitGPU


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['master','config','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--backend-fingerprint',required=True)
    args=parser.parse_args()
    import enhance_backend as engine
    from native_face_pipeline import run
    from face_rework_worker import validate_video
    cfg=engine.config(args.config);info=engine.source_info(args.master)
    from segmented_backend import fingerprint
    if fingerprint(cfg)!=args.backend_fingerprint:raise ValueError('Calibration requested stale backend fingerprint')
    full=engine.source_packet_count(args.master);digest=sha256(args.master)
    if cfg.get('restoration_backend')!='cuda_gfpgan_fused':raise ValueError('Explicit native fused config required')
    check_space(args.output,128*1024**2,minimum_free_gib(cfg));args.output.mkdir(parents=True,exist_ok=True)
    result_path=args.output/'result.json';pending=args.output/'pending.json';final=args.output/'sample.mkv'
    if result_path.exists():raise FileExistsError('Existing benchmark result retained; caller must validate/reuse')
    if pending.exists():
        data=json.loads(pending.read_text());candidate=Path(data['partial'])
        if data['master_sha256']!=digest or data['backend_fingerprint']!=args.backend_fingerprint:
            raise ValueError('Native benchmark publication belongs to another context')
        actual=final if final.exists() else candidate
        if actual.parent!=args.output or sha256(actual)!=data['sample_sha256']:
            raise ValueError('Native benchmark pending bytes changed')
        validate_video(actual,data['output_frames'],args.output)
        if actual!=final:candidate.rename(final)
        atomic_json(result_path,data);return
    if final.exists():raise ValueError('Unbound native benchmark sample retained')
    count=min(125,full)
    # At most5seconds, centered, start on the same5-frame segment-clock grid.
    start=max(0,((full-count)//2)//5*5)
    stat=args.master.stat();prepared=dict(sha256=digest,count=full,info=info,
        stat=(stat.st_ino,stat.st_size,stat.st_mtime_ns))
    began=time.monotonic()
    from native_face_pipeline import NativeFrames
    processor=NativeFrames(cfg);model_init=time.monotonic()-began
    trials=[]
    starts=list(dict.fromkeys([start,max(0,((full-count)//4)//5*5),max(0,((full-count)*3//4)//5*5)]))
    for trial,start in enumerate(starts):
        partial=args.output/f'sample.{os.getpid()}.{trial}.partial.mkv';trial_start=time.monotonic()
        run(args.master,partial,'S01E49' if cfg.get('native_test_only') else
            json.loads(Path(cfg['native_recipe']).read_text())['episode'],cfg,
            source_start=start,source_frames=count,prepared=prepared,restorer=processor)
        candidate_pipeline=json.loads(partial.with_name(partial.name+'.pipeline.json').read_text())
        trials.append(dict(source_start=start,seconds=time.monotonic()-trial_start,
            restored_frames=candidate_pipeline['coverage']['restored_frames'],partial=str(partial)))
        if candidate_pipeline['coverage']['restored_frames']>0:break
    elapsed=time.monotonic()-began
    frames=engine.output_count(count);validation=validate_video(partial,frames,args.output)
    pipeline=json.loads(partial.with_name(partial.name+'.pipeline.json').read_text())
    if (pipeline['coverage']['processed_frames']!=min(count+1,full-start)
            or pipeline['coverage']['failed_frames']!=0 or pipeline['video_encodes']!=1):
        raise ValueError('Native benchmark did not actually process all requested frames')
    if sha256(args.master)!=digest:raise ValueError('Native master changed during calibration')
    if fingerprint(engine.config(args.config))!=args.backend_fingerprint:
        raise ValueError('Native active profile changed during calibration')
    result=dict(technical_checked=True,master_sha256=digest,backend_fingerprint=args.backend_fingerprint,
        source_start=start,input_frames=count,output_frames=frames,sample_sha256=validation['sha256'],
        source_fps=count/elapsed,wall_with_init_seconds=elapsed,validation=validation,
        model_init_seconds=model_init,probe_trials=trials,
        gfpgan_exercised=pipeline['coverage']['restored_frames']>0,
        pipeline=pipeline,partial=str(partial),scope='Actual bounded master test, not full acceptance or original-detail truth; source_fps includes all bounded probe trials if earlier windows had no face')
    atomic_json(pending,result);partial.rename(final);atomic_json(result_path,result)


if __name__=='__main__':
    try:main()
    except WaitDisk as error:
        print(json.dumps({'wait_disk':str(error)}),flush=True);raise SystemExit(75)
    except WaitGPU as error:
        print(json.dumps({'wait_gpu':str(error)}),flush=True);raise SystemExit(76)
