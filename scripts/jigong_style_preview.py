"""Render temporary actual-master style previews on physical T10, no approval."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kwjd import cmd_make_ass
from enhance_stage import atomic_json,sha256,check_space
from wait_jigong_post_input import master_ready
from jigong_post_supervisor import EPISODE,MASTER


def render(root,srt,area,times):
    root=root.resolve(strict=True);area=area.resolve()
    if not master_ready(root):raise ValueError('Complete master and real receipt required')
    if not area.is_relative_to((root/'post').resolve()):raise ValueError('Preview evidence must remain in post')
    area.mkdir(parents=True,exist_ok=True);check_space(area,256*1024**2,25)
    post=root/'post';master=post/'incoming'/(EPISODE+'.clean-master.mov')
    template=post/'styles/template.ass';fonts=post/'styles/fonts'
    ass=area/'temporary-not-final.ass'
    if ass.exists():raise FileExistsError('Existing preview retained')
    cmd_make_ass(argparse.Namespace(srt=str(srt.resolve()),template=str(template),output=str(ass)))
    devices=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,driver_version','--format=csv,noheader'],text=True)
    if not any(row.split(',')[0].strip()=='1' and 'Tesla T10' in row for row in devices.splitlines()):
        raise ValueError('Physical GPU1 Tesla T10 required')
    samples=[]
    for i,point in enumerate(times):
        if not 0<=point<3702:raise ValueError('Invalid preview timestamp')
        clip=area/f'preview-{i:02d}.mkv';picture=area/f'preview-{i:02d}.png'
        for path in [ass,fonts]:
            if any(c in str(path) for c in "'\\:[],;"):raise ValueError('Unsafe FFmpeg filter path')
        vf=(f'setpts=PTS+{point}/TB,ass=filename={ass}:fontsdir={fonts},'
            'setpts=PTS-STARTPTS,format=p010le')
        command=['ffmpeg','-nostdin','-hide_banner','-v','verbose','-n','-hwaccel','cuda',
            '-hwaccel_device','1','-ss',str(point),'-i',str(master),'-an','-vf',vf,
            '-frames:v','3','-c:v','hevc_nvenc','-gpu','1','-profile:v','main10',
            '-preset','p6','-cq','16',str(clip)]
        log=area/f'preview-{i:02d}.render.log'
        with log.open('w') as stream:subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True)
        if 'MiSans-Subtitle-Demibold' not in log.read_text():raise ValueError('Packaged MiSans not selected')
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-v','error','-n','-hwaccel','cuda',
            '-hwaccel_device','1','-i',str(clip),'-frames:v','1','-update','1',str(picture)],check=True)
        samples.append(dict(time_seconds=point,image=str(picture),image_sha256=sha256(picture),
            clip=str(clip),clip_sha256=sha256(clip),render_log=str(log),render_log_sha256=sha256(log),command=command))
    data=dict(schema_version=1,episode=EPISODE,master_sha256=MASTER,
        temporary_not_final=True,visual_checked=False,technical_checked=True,
        template_sha256=sha256(template),temporary_srt_path=str(srt),temporary_srt_sha256=sha256(srt),
        fonts={p.name:sha256(p) for p in fonts.glob('*.ttf')},physical_gpu_evidence=devices,samples=samples,
        scope='Temporary pixel-reviewed excerpt for layout; not a sealed episode subtitle or timing approval')
    atomic_json(area/'preview.json',data);return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--srt',type=Path,required=True)
    parser.add_argument('--area',type=Path,required=True)
    parser.add_argument('--times',type=float,nargs='+',required=True)
    args=parser.parse_args();print(json.dumps(render(args.root,args.srt,args.area,args.times),ensure_ascii=False))
