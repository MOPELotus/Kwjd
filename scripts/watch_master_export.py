"""Wait for a finalized export, then validate it. Does not operate Resolve UI."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',type=Path,required=True)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--timeout-hours',type=float,default=4)
    args=parser.parse_args()
    args.state.parent.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+args.timeout_hours*3600
    previous=None; stable_since=None
    while time.monotonic()<deadline:
        stat=args.file.stat() if args.file.exists() else None
        current=(stat.st_size,stat.st_mtime_ns) if stat else None
        if current!=previous:
            previous=current; stable_since=time.monotonic()
        state={'file':str(args.file),'stage':'WAIT_EXPORT_FINALIZED','bytes':stat.st_size if stat else 0,
               'checked_at':time.time()}
        args.state.write_text(json.dumps(state,indent=2),encoding='utf8')
        if stat and stat.st_size>1024**2 and stable_since and time.monotonic()-stable_since>=90:
            probe=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(args.file)],
                                 capture_output=True,text=True)
            if probe.returncode==0:
                info=json.loads(probe.stdout)
                video=next(s for s in info['streams'] if s['codec_type']=='video')
                if video['codec_name']!='hevc' or video.get('profile')!='Main 10' or video.get('pix_fmt')!='yuv420p10le':
                    raise ValueError('Export does not match HEVC Main10 policy')
                reference=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(args.reference)]))
                command=[sys.executable,str(Path(__file__).with_name('validate_master.py')),
                         '--file',str(args.file),'--receipt',str(args.receipt),
                         '--expected-duration',reference['format']['duration']]
                print('EXPORT_STABLE_AND_READABLE_START_VALIDATION',flush=True)
                subprocess.run(command,check=True)
                state.update(stage='TECHNICALLY_VALIDATED_VISUAL_REVIEW_PENDING',receipt=str(args.receipt))
                args.state.write_text(json.dumps(state,indent=2),encoding='utf8')
                return
        time.sleep(20)
    raise TimeoutError('Export did not finalize within bounded watch')


if __name__=='__main__':
    main()
