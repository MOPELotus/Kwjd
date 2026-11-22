"""Inspect a nonconforming export without approving it for production."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file',required=True,type=Path)
    p.add_argument('--reference',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();before=args.file.stat()
    args.output.mkdir(parents=True,exist_ok=False)
    actual,reference=probe(args.file),probe(args.reference)
    video=next(s for s in actual['streams'] if s['codec_type']=='video')
    audio=next(s for s in actual['streams'] if s['codec_type']=='audio')
    source_video=next(s for s in reference['streams'] if s['codec_type']=='video')
    duration=float(actual['format']['duration']);expected=float(reference['format']['duration'])
    issues=[]
    if video['r_frame_rate']!=source_video['r_frame_rate']:issues.append('MASTER_FPS_DIFFERS_FROM_PROGRAM')
    if abs(duration-expected)>.5:issues.append('MASTER_DURATION_DIFFERS_FROM_PROGRAM')
    if (video['width'],video['height'],video['codec_name'],video.get('profile'))!=(1920,1080,'hevc','Main 10'):
        issues.append('VIDEO_FORMAT_MISMATCH')
    if audio['codec_name']!='pcm_s24le' or int(audio['sample_rate'])<48000 or audio['channels']!=2:
        issues.append('AUDIO_FORMAT_MISMATCH')
    samples=[0,60,duration/2,expected-2,expected+.5,duration-.4]
    for i,start in enumerate(samples):
        start=max(0,min(start,duration-.1))
        subprocess.run(['ffmpeg','-v','error','-xerror','-nostdin','-threads','2','-ss',str(start),'-i',str(args.file),
                        '-t','1','-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
        subprocess.run(['ffmpeg','-v','error','-nostdin','-threads','2','-ss',str(start),'-i',str(args.file),
                        '-frames:v','1','-vf','scale=640:360','-n',str(args.output/f'sample-{i}.png')],check=True)
    with args.file.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    after=args.file.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Master changed')
    report={'state':'INSPECTED_NOT_ADMITTED','path':str(args.file),'sha256':digest,'bytes':after.st_size,
            'video':video,'audio':audio,'duration_seconds':duration,'expected_duration_seconds':expected,
            'duration_delta_seconds':duration-expected,'issues':issues,'sampled_av_decode_passed':True,
            'sample_seconds':samples,'uploaded':False,'quality_approved':False}
    (args.output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ['state','sha256','issues','duration_delta_seconds']}))


if __name__=='__main__':main()
