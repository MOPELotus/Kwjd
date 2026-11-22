"""Capture paired physical-CUDA-decoded calibration frames; never approve them."""
import argparse
import json
from pathlib import Path
import subprocess

from enhance_stage import atomic_json, sha256, check_space
from ffmpeg_timestamps import showinfo_frames


def capture(result_path, output, times, gpu=1):
    from PIL import Image, ImageDraw
    result_path=Path(result_path).resolve(strict=True)
    result=json.loads(result_path.read_text())
    if (result.get('scope')!='TECHNICAL_CALIBRATION_NOT_PRODUCTION'
            or result.get('production_eligible') is not False
            or result.get('technical_checked') is not True):
        raise ValueError('A completed isolated technical calibration is required')
    # reports/calibration/EP/masterSHA/trial/result.json
    area=result_path.parent;post=area.parents[4]
    if area.parents[3].name!='reports' or area.parents[2].name!='calibration':
        raise ValueError('Calibration must remain in the expected post report tree')
    sample=Path(result['sample_path']);master=post/'incoming'/(result['episode']+'.clean-master.mov')
    if sample.parent!=area or sample.is_symlink() or master.is_symlink():
        raise ValueError('Unexpected calibration media paths')
    if sha256(sample)!=result['sha256'] or sha256(master)!=result['master_sha256']:
        raise ValueError('Calibration media identity changed')
    duration=result['input_frames']/result['input_rate']
    if not times or len(times)>12 or any(not 0<=t<duration for t in times):
        raise ValueError('Choose 1..12 actual sample times within calibration')
    output=Path(output).absolute()
    if output.is_symlink() or output.parent!=area:
        raise ValueError('Frame evidence must be a new directory inside the trial')
    check_space(area,128*1024**2,25);output.mkdir(exist_ok=False)
    physical=subprocess.check_output(['nvidia-smi','--id='+str(gpu),
        '--query-gpu=name,uuid,driver_version','--format=csv,noheader'],text=True).strip()
    rows=[];sheets=[]
    for number,t in enumerate(times):
        row={'sample_seconds':t,'images':{}}
        for kind,path,seek in [('master',master,t+result['source_start']/result['input_rate']),('sample',sample,t)]:
            image=output/f'{number:02d}-{kind}.png'
            command=['ffmpeg','-nostdin','-hide_banner','-v','verbose','-n','-hwaccel','cuda',
                '-hwaccel_device',str(gpu),'-copyts','-ss',str(seek),'-i',str(path),
                '-map','0:v:0','-frames:v','1','-vf','trim=end_frame=1,showinfo',
                '-fps_mode','passthrough',str(image)]
            process=subprocess.run(command,capture_output=True,text=True,check=True)
            log=output/f'{number:02d}-{kind}.decode.log';log.write_text(process.stderr)
            pts=showinfo_frames(process.stderr)
            if len(pts)!=1:raise ValueError('Actual decoded frame PTS missing')
            row['images'][kind]={'path':str(image),'sha256':sha256(image),
                'requested_seconds':seek,'decoded_pts_seconds':pts[0]['seconds'],
                'decoded_pts':pts[0]['pts'],'source_time_base':pts[0]['time_base'],
                'log_path':str(log),'log_sha256':sha256(log),'command':command}
        sheet=Image.new('RGB',(1920,580),'#151515');draw=ImageDraw.Draw(sheet)
        for column,kind in enumerate(('master','sample')):
            with Image.open(row['images'][kind]['path']) as frame:
                sheet.paste(frame.resize((960,540)),(column*960,40))
            draw.text((column*960+12,12),f'{kind} / sample {t:.6f}s / decoded PTS {row["images"][kind]["decoded_pts_seconds"]:.6f}',fill='white')
        target=output/f'{number:02d}-pair.jpg';sheet.save(target,quality=94)
        row['pair_path']=str(target);row['pair_sha256']=sha256(target);rows.append(row);sheets.append(sheet)
    # Small grouped sheets supplement, never replace, the preserved native PNGs.
    contacts=[]
    for start in range(0,len(sheets),3):
        batch=sheets[start:start+3];contact=Image.new('RGB',(1920,580*len(batch)))
        for i,sheet in enumerate(batch):contact.paste(sheet,(0,i*580))
        target=output/f'contact-{start//3}.jpg';contact.save(target,quality=94)
        contacts.append({'path':str(target),'sha256':sha256(target)})
    data={'schema_version':1,'scope':'Paired actual CUDA-decoded calibration frames; no visual approval',
        'result_path':str(result_path),'result_sha256':sha256(result_path),
        'master_sha256':result['master_sha256'],'sample_sha256':result['sha256'],
        'physical_decoder':physical,'rows':rows,'contacts':contacts,'visual_checked':False}
    atomic_json(output/'evidence.json',data)
    return {'evidence':str(output/'evidence.json'),'pairs':len(rows),'contacts':contacts}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--times',type=float,nargs='+',required=True);parser.add_argument('--gpu',type=int,default=1)
    args=parser.parse_args();print(json.dumps(capture(args.result,args.output,args.times,args.gpu)))
