"""Isolated current-master fullchain short test, explicitly not production."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from enhance_stage import atomic_json,sha256,check_space,WaitDisk,WaitGPU


def prepare(post,episode,area,evidence,start,count):
    if not re.fullmatch(r'S0[1-4]E\d{2,3}',episode):raise ValueError('Invalid calibration episode')
    post=post.resolve(strict=True);area=area.resolve(strict=True)
    proof=json.loads(evidence.read_text());digest=proof['master_sha256']
    master=post/'incoming'/(episode+'.clean-master.mov')
    if master.is_symlink():raise ValueError('Master must be the received regular file')
    receipt=json.loads((post/'handoffs'/(episode+'.master-received.json')).read_text())
    if (receipt.get('transfer_verified') is not True or receipt.get('episode')!=episode
            or receipt.get('sha256')!=digest or receipt.get('bytes')!=master.stat().st_size
            or sha256(master)!=digest):
        raise ValueError('Actual complete master and matching transfer receipt required')
    if evidence.resolve().parent!=area:raise ValueError('Pixel evidence must be retained in isolated trial directory')
    from kwjd import cmd_make_ass
    srt=area/'temporary-not-final.srt';ass=area/'temporary-not-final.ass'
    if not srt.is_file():raise ValueError('Explicit temporary pixel-reviewed SRT required')
    style=post/'styles/template.ass';fonts=post/'styles/fonts'
    if not ass.exists():
        cmd_make_ass(argparse.Namespace(srt=str(srt),template=str(style),output=str(ass)))
    data=dict(schema_version=1,scope='technical_calibration',production_eligible=False,
        episode=episode,master_sha256=digest,post_root=str(post),program_origin_seconds=0,
        source_start=start,source_frames=count,calibration_directory=str(area),
        pixel_review_evidence_path=str(evidence.resolve()),pixel_review_evidence_sha256=sha256(evidence),
        fonts_path=str(fonts),fonts={p.name:sha256(p) for p in fonts.iterdir() if p.suffix in ['.ttf','.otf','.ttc']},
        subtitle_timing={'offset_ms':0,'scope':'temporary diagnostic only'})
    for key,path in [('srt',srt),('ass',ass),('style',style)]:
        data[key+'_path']=str(path);data[key+'_sha256']=sha256(path)
    recipe=area/'recipe.json'
    if recipe.exists() and json.loads(recipe.read_text())!=data:raise ValueError('Existing calibration recipe differs')
    atomic_json(recipe,data)
    from native_face_recipe import validate_recipe
    validate_recipe(recipe,sha256(recipe))
    cfg=json.loads((post/'backend/runtime.native-template.json').read_text())
    if episode[:3] not in cfg.get('admitted_seasons',['S01']):raise ValueError('Candidate season differs')
    cfg.update(native_recipe=str(recipe),native_recipe_sha256=sha256(recipe))
    config=area/'runtime.calibration.json'
    if config.exists() and json.loads(config.read_text())!=cfg:raise ValueError('Existing calibration runtime differs')
    atomic_json(config,cfg)
    return master,config


def run(post,episode,area,evidence,start,count):
    began=time.monotonic()
    master,config=prepare(post,episode,area,evidence,start,count)
    import enhance_backend as engine
    from native_face_pipeline import run as pipeline
    from face_rework_worker import validate_video
    from render_face_samples import pcm_update
    cfg=engine.config(config);rate=cfg.get('input_frame_rate',25)
    info=engine.source_info(master,rate);full=engine.source_packet_count(master,rate)
    if start+count>full:raise ValueError('Calibration extends beyond master')
    check_space(area,1024**3,25)
    video=area/'sample.video.mkv';final=area/'sample.with-mix.mkv'
    if final.exists() or (area/'result.json').exists():raise FileExistsError('Existing trial retained; review or select a new trial')
    before=master.stat();prepared=dict(sha256=sha256(master),count=full,info=info,
        stat=(before.st_ino,before.st_size,before.st_mtime_ns))
    pipeline(master,video,episode,cfg,source_start=start,source_frames=count,prepared=prepared)
    pipeline_seconds=time.monotonic()-began
    frames=engine.output_count(count,rate)
    subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-n','-i',str(video),
        '-ss',str(start/rate),'-i',str(master),'-t',str(count/rate),
        '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','flac','-sample_fmt','s32',
        '-bits_per_raw_sample','24',str(final)],check=True)
    validation=validate_video(final,frames,area,audio=True)
    original=hashlib.sha256();actual=hashlib.sha256()
    pcm_update(master,original,start/rate,count/rate);pcm_update(final,actual)
    if original.digest()!=actual.digest():raise ValueError('Calibration PCM differs from original mix')
    details=json.loads(video.with_name(video.name+'.pipeline.json').read_text())
    if (details['coverage']['restored_frames']==0 or details['interpolated_frames']==0
            or details['coverage']['failed_frames']!=0):raise ValueError('GFPGAN/RIFE not exercised successfully')
    result=dict(scope='TECHNICAL_CALIBRATION_NOT_PRODUCTION',production_eligible=False,
        episode=episode,master_sha256=prepared['sha256'],input_rate=rate,full_master_frames=full,
        source_start=start,input_frames=count,output_frames=frames,technical_checked=True,
        visual_checked=False,final_decode_checked=True,final_pcm_equivalent=True,
        decoded_pcm_sha256=actual.hexdigest(),sample_path=str(final),sha256=sha256(final),
        pipeline_seconds_including_setup=pipeline_seconds,wall_seconds_including_all_qc=time.monotonic()-began,
        processed_source_fps_including_setup=count/pipeline_seconds,
        pipeline=details,validation=validation,config_sha256=sha256(config),
        limitation='Temporary reviewed excerpt; no final subtitle seal or production readiness created')
    atomic_json(area/'result.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',required=True,type=Path)
    parser.add_argument('--episode',required=True)
    parser.add_argument('--area',required=True,type=Path)
    parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--source-start',required=True,type=int)
    parser.add_argument('--source-frames',required=True,type=int)
    args=parser.parse_args()
    try:run(args.post,args.episode,args.area,args.evidence,args.source_start,args.source_frames)
    except WaitDisk as error:print(str(error),flush=True);raise SystemExit(75)
    except WaitGPU as error:print(str(error),flush=True);raise SystemExit(76)
