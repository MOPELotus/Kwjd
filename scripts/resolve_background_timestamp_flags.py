"""Check original timing flags without encoding or changing source media."""
import argparse
import json
from pathlib import Path
import subprocess

from process_background_exceptions import atomic,sha,timestamp_check,FFMPEG,FFPROBE


def resolve(root):
    count=0
    for path in sorted((root/'review').glob('*/technical.json')):
        tech=json.loads(path.read_text(encoding='utf-8-sig'))
        if tech['state']!='TECHNICAL_REVIEW_REQUIRED':continue
        # Resolve only source pictures already explicitly accepted as clean.
        inventory=json.loads((root/'bulk-review-v1/inventory.json').read_text(encoding='utf-8-sig'))
        rows={r['id']:r for r in inventory['items']}
        if rows[path.parent.name]['action']!='TECHNICAL_REVIEW':continue
        source=Path(tech['source']);before=source.stat()
        if sha(source)!=tech['sha256']:raise ValueError('Source SHA changed')
        packet=subprocess.Popen([FFPROBE,'-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=pts,dts','-of','csv=p=0',str(source)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:n=timestamp_check(packet.stdout)
        except BaseException:packet.kill();packet.communicate();raise
        _,stderr=packet.communicate()
        if packet.returncode or stderr:raise ValueError('Packet probe failed')
        duration=tech['duration_seconds'];checks=[]
        for t in [min(1,duration/4),duration/2,max(0,duration-2)]:
            decoded=subprocess.run([FFMPEG,'-v','error','-ss',str(t),'-i',str(source),'-t','2','-map','0:v:0','-fps_mode','passthrough','-enc_time_base','demux','-f','null','-'],capture_output=True,text=True)
            checks.append(dict(start=t,seconds=2,exit_code=decoded.returncode,stderr=decoded.stderr))
        after=source.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed')
        passed=all(c['exit_code']==0 and not c['stderr'].strip() for c in checks)
        atomic(path.parent/'technical-flag-resolution.json',dict(state='ORIGINAL_TIMING_VERIFIED' if passed else 'REVIEW_REQUIRED',source_sha256=tech['sha256'],packet_count=n,all_pts_unique=True,all_dts_strictly_monotonic=True,decode_windows=checks,
            scope='Every source packet plus three 2-second video decode windows using native demux time base. Original null-mux warnings retained. No video encoded or source changed.'))
        count+=int(passed)
    print(json.dumps(dict(originals_resolved=count)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    resolve(parser.parse_args().root)
