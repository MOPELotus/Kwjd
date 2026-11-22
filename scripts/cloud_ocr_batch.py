"""Run serial VideOCR drafts against SHA-verified inputs; never seal reviews."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kwjd import read_srt


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path,data):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    temp.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--engine',type=Path,required=True)
    parser.add_argument('--input-wait-hours',type=float,default=12)
    args=parser.parse_args()
    root=args.root.resolve(strict=True)
    config=json.loads(args.config.read_text(encoding='utf8'))
    if not config['production_admitted']:
        raise ValueError('Cloud pilot has not admitted this profile')
    season=config['season']
    if not re.fullmatch(r'S\d{2}',season) or not all(isinstance(e,int) and e>0 for e in config['episodes']):
        raise ValueError('Invalid season/episodes')
    engine=args.engine.resolve(strict=True)
    work=root/'ocr'/('batch-'+season.lower())
    work.mkdir(exist_ok=True)
    lock=(work/'worker.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    engine_sha,config_sha=sha(engine),sha(args.config)
    drafts=root/'output'/'drafts'
    drafts.mkdir(parents=True,exist_ok=True)
    for number in config['episodes']:
        episode=f'{season}E{number:02}'
        source=root/'input'/(episode+'.mp4')
        transfer=source.with_suffix('.mp4.transfer.json')
        state_path=work/(episode+'.json')
        state={'episode':episode,'stage':'WAITING_INPUT','subtitle_reviewed':False,
               'engine_sha256':engine_sha,'config_sha256':config_sha}
        save(work/'current.json',state)
        deadline=time.monotonic()+args.input_wait_hours*3600
        while not (source.exists() and transfer.exists()):
            if time.monotonic()>deadline:
                raise TimeoutError('Input not received: '+episode)
            time.sleep(20)
        receipt=json.loads(transfer.read_text(encoding='utf8'))
        input_sha=sha(source)
        if not receipt.get('transfer_verified') or receipt.get('sha256')!=input_sha:
            raise ValueError('Input receipt mismatch: '+episode)
        state['input_sha256']=input_sha
        output=drafts/(episode+'.zh.srt')
        if output.exists() or state_path.exists():
            previous=json.loads(state_path.read_text(encoding='utf8')) if state_path.exists() else {}
            if (previous.get('stage')=='OCR_DRAFT_COMPLETE' and previous.get('input_sha256')==input_sha
                    and previous.get('engine_sha256')==engine_sha and previous.get('config_sha256')==config_sha
                    and output.exists() and previous.get('output_sha256')==sha(output)):
                print('REUSED DRAFT '+episode,flush=True)
                continue
            raise ValueError('Existing output/state requires inspection: '+episode)
        free=shutil.disk_usage(root).free/1024**3
        if free<config['minimum_free_gib']+config['temporary_budget_gib']:
            raise RuntimeError('Insufficient per-filesystem space: '+str(free))
        temp=work/(episode+'-temp')
        temp.mkdir(exist_ok=False)
        partial=output.with_suffix('.srt.partial')
        if partial.exists():
            raise FileExistsError(partial)
        env=os.environ.copy()
        env.update(CUDA_VISIBLE_DEVICES=str(config['gpu_physical_index']),TMPDIR=str(temp),
                   OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4')
        crop=config['crop']
        command=[str(engine),'--video_path',str(source),'--output',str(partial),
                 '--lang','ch','--use_gpu','true','--allow_system_sleep','true',
                 '--subtitle_position',config['subtitle_position'],'--crop_x',str(crop['x']),
                 '--crop_y',str(crop['y']),'--crop_width',str(crop['width']),
                 '--crop_height',str(crop['height']),'--frames_to_skip',str(config['frames_to_skip']),
                 '--ocr_image_max_width',str(config['ocr_image_max_width']),
                 '--ssim_threshold',str(config['ssim_threshold']),
                 '--use_server_model',str(config['use_server_model']).lower(),
                 '--normalize_to_simplified_chinese','false','--post_processing','false']
        state.update(stage='RUNNING',started_at=time.time(),log=str(work/(episode+'.log')))
        save(state_path,state); save(work/'current.json',state)
        print('RUNNING '+episode,flush=True)
        with (work/(episode+'.log')).open('x') as log:
            result=subprocess.run(command,cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            state.update(stage='FAILED',returncode=result.returncode)
            save(state_path,state); save(work/'current.json',state)
            raise RuntimeError('VideOCR failed: '+episode)
        cues=read_srt(partial)
        if not cues or any(end<=start for start,end,_ in cues):
            raise ValueError('Empty or malformed draft: '+episode)
        partial.rename(output)
        state.update(stage='OCR_DRAFT_COMPLETE',finished_at=time.time(),cues=len(cues),
                     output=str(output),output_sha256=sha(output),subtitle_reviewed=False)
        save(state_path,state); save(work/'current.json',state)
        print('DRAFT COMPLETE '+episode+' '+str(len(cues))+' cues; REVIEW REQUIRED',flush=True)
    save(work/'current.json',{'stage':'ALL_DRAFTS_COMPLETE_REVIEW_REQUIRED',
                             'episodes':len(config['episodes']),'subtitle_reviewed':False})


if __name__=='__main__':
    main()
