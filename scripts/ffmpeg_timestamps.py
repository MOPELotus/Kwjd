"""Read exact showinfo integer ticks; pts_time is rounded display text."""
from fractions import Fraction
import re


def showinfo_frames(log):
    bases={Fraction(int(a),int(b)) for a,b in
           re.findall(r'config in time_base:\s*(\d+)\s*/\s*([1-9]\d*)',log)}
    if len(bases)!=1 or next(iter(bases))<=0:
        raise ValueError('One positive showinfo input time base is required')
    base=next(iter(bases))
    matches=re.findall(r'\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:',log)
    if not matches or [int(n) for n,_ in matches]!=list(range(len(matches))):
        raise ValueError('Contiguous unique showinfo frame indices are required')
    return [{'frame_index':int(n),'pts':int(pts),'time_base':str(base),
             'seconds':float(int(pts)*base)} for n,pts in matches]
