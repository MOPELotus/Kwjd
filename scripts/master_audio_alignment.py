"""Measure voice-track alignment after Fairlight mixing; never change audio."""
import argparse
import json
import os
import subprocess
from pathlib import Path
import numpy as np


def lag(reference,mixed,rate=8000,max_seconds=.5):
    reference=reference.astype(np.float64);mixed=mixed.astype(np.float64)
    reference-=reference.mean();mixed-=mixed.mean()
    length=1<<(len(reference)+len(mixed)-1).bit_length()
    a=np.fft.rfft(reference,length);b=np.fft.rfft(mixed,length)
    frequencies=np.fft.rfftfreq(length,1/rate)
    speech=(frequencies>=200)&(frequencies<=3000)
    a*=speech;b*=speech
    corr=np.fft.irfft(b*np.conj(a),length)
    maximum=round(max_seconds*rate)
    shifts=np.arange(-maximum,maximum+1)
    values=corr[shifts%length]
    best=int(np.argmax(values))
    norm=np.sqrt(np.sum(np.fft.irfft(a,length)**2)*np.sum(np.fft.irfft(b,length)**2))
    return {'master_delay_ms':float(shifts[best]/rate*1000),
            'speech_band_correlation':float(values[best]/norm) if norm else 0}


def audio(path,start):
    raw=subprocess.check_output(['ffmpeg','-v','error','-nostdin','-ss',str(start),'-i',str(path),
        '-t','12','-map','0:a:0','-vn','-ac','1','-ar','8000','-f','f32le','-'],
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    return np.frombuffer(raw,dtype='<f4').copy()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--master',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rows=[{'start_seconds':t,**lag(audio(args.source,t),audio(args.master,t))} for t in (60,1800,3600)]
    result={'samples':rows,'scope':'speech-band waveform diagnostic; not whole-video lip-sync approval',
            'audio_changed':False}
    with args.report.open('x',encoding='utf8') as f:json.dump(result,f,indent=2)
    print(json.dumps(result))
