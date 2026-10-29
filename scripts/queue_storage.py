"""Per-filesystem maxima for serialized native production and reserved uploads."""


GIB=2**30


def phase_peaks(seconds,rate,channels,devices):
    video=int(seconds*18_000_000/8);audio=int(seconds*rate*channels*4)
    phases=[]
    for demands in [ [('cache',2*video),('scratch',512*1024**2)],
                     [('cache',video),('final',video+16*1024**2),('scratch',512*1024**2)],
                     [('cache',video+audio),('final',video+audio+GIB)] ]:
        grouped={}
        for area,size in demands:
            device=devices[area];grouped[device]=grouped.get(device,0)+size
        phases.append(grouped)
    return {device:max(phase.get(device,0) for phase in phases) for device in set(devices.values())}


def evaluate(peaks,free,reserve_gib,queued=None):
    queued=queued or {}
    rows={device:dict(free_bytes=free[device],production_peak_bytes=peak,
                     queued_upload_remaining_bytes=queued.get(device,0),reserve_gib=reserve_gib,
                     shortfall_bytes=max(0,peak+queued.get(device,0)+reserve_gib*GIB-free[device]))
          for device,peak in peaks.items()}
    return dict(allowed=all(row['shortfall_bytes']==0 for row in rows.values()),filesystems=rows)
