"""Select bounded disk-backed scratch space without touching other workloads."""
import os
from pathlib import Path
import re

from enhance_stage import check_space, WaitDisk


def filesystem_kind(path):
    resolved=Path(path).resolve();best=(-1,None)
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right=line.split(' - ',1)
        mount=Path(re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),left.split()[4]))
        if resolved.is_relative_to(mount) and len(mount.parts)>best[0]:
            best=(len(mount.parts),right.split()[0])
    return best[1]


def mounted_scratch_candidates(post):
    post = Path(post)
    yield post/'cache/scratch'
    yield Path.home()/'qingqingwu/post-home/stage-scratch'
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields = left.split();kind = right.split()[0]
        if kind in ('tmpfs','devtmpfs','proc','sysfs','cgroup','cgroup2','overlay'):
            continue  # RAM or virtual filesystems are not durable stage storage.
        mount = Path(re.sub(r'\\([0-7]{3})', lambda m:chr(int(m[1],8)),fields[4]))
        if 'ro' in fields[5].split(','):
            continue
        try:
            if mount.is_dir() and mount.stat().st_uid == os.getuid() and os.access(mount,os.W_OK|os.X_OK):
                yield mount/'kwjd-staging'/str(os.getuid())/'scratch'
        except OSError:
            continue  # Inaccessible container namespaces are not usable storage.


def choose_scratch(post, required_bytes, reserve_gib=25, *, candidates=None):
    """Return only a user-owned writable directory with independent peak reserve.

    Prefer the configured disk, then search actual writable mounted disks. Never
    combine their free space, delete files to make room or alter mount settings.
    """
    errors=[];seen=set()
    for candidate in (mounted_scratch_candidates(post) if candidates is None else candidates):
        path=Path(candidate);ancestor=path
        while not ancestor.exists():ancestor=ancestor.parent
        device=ancestor.stat().st_dev
        if device in seen:continue
        if ancestor.stat().st_uid != os.getuid() or not os.access(ancestor,os.W_OK|os.X_OK):
            continue
        if filesystem_kind(path) in ('tmpfs','devtmpfs','proc','sysfs','cgroup','cgroup2','overlay'):
            continue
        try:
            free=check_space(path,required_bytes,reserve_gib)
        except WaitDisk as exc:
            errors.append(str(exc));seen.add(device);continue
        path.mkdir(parents=True,exist_ok=True)
        # Test write permission, not just mode bits; never replace existing data.
        import tempfile
        with tempfile.TemporaryFile(dir=path) as stream:stream.write(b'kwjd-storage-probe');stream.flush()
        return path, {'path':str(path.resolve()),'device':device,'free_bytes':free,
                      'additional_peak_bytes':required_bytes,'reserve_gib':reserve_gib}
    raise WaitDisk('No user-owned disk-backed scratch volume meets peak reserve: '+'; '.join(errors))
