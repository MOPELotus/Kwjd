"""Frame-count durations for stream-copy concatenation of fractional-second MKVs."""
import json
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
import subprocess


def subtitle_frame_origin(program_origin_seconds,source_start,source_fps):
    origin=Fraction(str(program_origin_seconds))*60+Fraction(source_start*60,source_fps)
    if origin.denominator!=1:raise ValueError('ASS origin must lie on the actual60fps frame grid')
    return int(origin)


def write_concat(path, media, frame_counts):
    if len(media)!=len(frame_counts) or not media:raise ValueError('Bound segment frame counts required')
    lines=[]
    for item,frames in zip(media,frame_counts):
        if type(frames) is not int or frames<=0:raise ValueError('Positive frame count required')
        name=str(Path(item).resolve())
        if '\n' in name or '\r' in name:raise ValueError('Invalid concat path')
        with localcontext() as context:
            context.prec=40
            seconds=format(Decimal(frames)/Decimal(60),'.18f')
        lines.extend(["file '"+name.replace("'","'\\''")+"'\n",'duration '+seconds+'\n'])
    Path(path).write_text(''.join(lines))


def verify_packet_clock(path,frames):
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=time_base:packet=pts','-show_packets','-of','json',str(path)]))
    base=Fraction(data['streams'][0]['time_base'])
    stamps=sorted(int(row['pts']) for row in data['packets'])
    if len(stamps)!=frames or len(set(stamps))!=frames or not stamps or stamps[0]!=0:
        raise ValueError('Missing/duplicate/nonzero-origin packet PTS')
    error=max(abs(pts*base-Fraction(index,60)) for index,pts in enumerate(stamps))
    if error>Fraction(11,10000):raise ValueError('Concatenated PTS off60fps grid')
    return dict(frames=frames,max_video_grid_error=float(error),time_base=str(base))


def normalize_copy_timestamps(source,output,frames):
    """Correct bounded timestamp jumps while retaining encoded packet order/data."""
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=time_base,has_b_frames:packet=pts','-show_packets','-of','json',str(source)]))
    packets=data['packets'];stream=data['streams'][0];base=Fraction(stream['time_base'])
    if len(packets)!=frames:raise ValueError('Packet count changed')
    order=sorted(range(frames),key=lambda i:(int(packets[i]['pts']),i))
    ranks=[0]*frames
    for rank,index in enumerate(order):ranks[index]=rank
    delay=int(stream['has_b_frames'])
    if max(i-rank for i,rank in enumerate(ranks))>delay:
        raise ValueError('Packet reordering exceeds declared codec delay')
    offsets=[]
    for index,packet in enumerate(packets):
        error=int(packet['pts'])*base-Fraction(ranks[index],60)
        shift=round(error*60)
        if abs(shift)>2 or abs(error-Fraction(shift,60))>Fraction(11,10000):
            raise ValueError('Unbounded/subframe timestamp error; refuse automatic remux')
        offsets.append(shift)
    groups=[];start=0
    for end in range(1,frames+1):
        if end==frames or offsets[end]!=offsets[start]:
            if offsets[start]:groups.append((start,end,offsets[start]))
            start=end
    if len(groups)>32:raise ValueError('Too many clock discontinuities for bounded repair')
    terms=[f'{shift}*gte(N,{start})*lt(N,{end})' for start,end,shift in groups]
    correction='+'.join(terms) or '0'
    expression=f"setts=pts='PTS-({correction})/(60*TB)':dts='(N-{delay})/(60*TB)':duration='1/(60*TB)'"
    command=['ffmpeg','-nostdin','-v','error','-n','-i',str(source),'-map','0:v:0','-an',
        '-c:v','copy','-bsf:v',expression,str(output)]
    subprocess.run(command,check=True)
    return dict(command=command,corrected_packet_ranges=groups,reorder_delay=delay,
        clock=verify_packet_clock(output,frames),new_lossy_encodes=0)
