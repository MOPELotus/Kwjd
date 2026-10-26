"""Persistent enhancement checkpoint, independent of subtitle/style readiness."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from fractions import Fraction
import shutil
import subprocess
from datetime import datetime, timezone

GIB = 1024 ** 3


class WaitDisk(RuntimeError):
    pass


class WaitGPU(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)
    ], text=True))


def validate_video(source, result):
    video = next(s for s in result['streams'] if s['codec_type'] == 'video')
    fps = Fraction(video['avg_frame_rate'])
    nominal = Fraction(video['r_frame_rate'])
    if (video['width'], video['height']) != (1920, 1080) or fps != 60 or nominal != 60:
        raise ValueError('Enhanced output must be 1920x1080 at nominal and average60fps')
    if any(s['codec_type'] == 'subtitle' for s in result['streams']):
        raise ValueError('Enhanced intermediate must not contain subtitle streams')
    delta = abs(float(source['format']['duration']) - float(result['format']['duration']))
    if delta > .5:
        raise ValueError(f'Enhanced duration differs from master by{delta:.3f}s')
    return {'width':1920, 'height':1080, 'fps':60, 'duration_delta':delta}


def check_space(path, required_bytes, reserve_gib):
    path = Path(path)
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free - required_bytes < reserve_gib * GIB:
        raise WaitDisk(f'{path.resolve()}: need {required_bytes/GIB:.2f}GiB additional '
                       f'+{reserve_gib}GiB reserve; free {free/GIB:.2f}GiB')
    return free


def ensure_enhanced(master, enhanced, backend, episode, log, reserve_gib=25):
    """No ASS/SRT dependency; accept only hash-bound, probe-validated output.

    The executable backend must publish a JSON disk plan with --plan INPUT.
    Output is written to a private partial path, then published without overwrite.
    Existing orphan output/partials are retained for investigation, never trusted.
    """
    master, enhanced, backend, log = map(Path, (master, enhanced, backend, log))
    enhanced.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = enhanced.with_name(enhanced.name + '.ready.json')
    pending_checkpoint = enhanced.with_name(enhanced.name+'.ready.pending.json')
    partial = enhanced.with_name(enhanced.stem + '.partial' + enhanced.suffix)
    master_stat = master.stat()
    master_hash = sha256(master)
    if master.stat().st_mtime_ns != master_stat.st_mtime_ns:
        raise ValueError('Master changed during hashing')
    source = probe(master)
    def backend_call(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except subprocess.CalledProcessError as exc:
            if exc.returncode == 75:
                raise WaitDisk('Backend paused for temporary disk pressure; verified checkpoints retained') from exc
            if exc.returncode == 76:
                raise WaitGPU('Backend paused to preserve shared GPU memory headroom; verified segments retained') from exc
            raise
    def backend_plan():
        result = json.loads(backend_call(subprocess.check_output,
                            [str(backend), '--plan', str(master), str(partial)], text=True))
        if type(result.get('additional_peak_bytes')) is not int or result['additional_peak_bytes'] <= 0 or not result.get('backend_fingerprint'):
            raise ValueError('Backend must provide positive measured/bounded disk plan and fingerprint')
        return result
    if enhanced.exists():
        if not checkpoint.is_file():
            if not pending_checkpoint.is_file():
                raise ValueError('Existing enhanced file has no verified checkpoint; retained')
            record=json.loads(pending_checkpoint.read_text())
        else:
            record = json.loads(checkpoint.read_text())
        if (record.get('episode') != episode or record.get('master_sha256') != master_hash
                or record.get('sha256') != sha256(enhanced)):
            raise ValueError('Enhanced checkpoint hash differs; existing assets retained')
        if record.get('backend_fingerprint') != backend_plan()['backend_fingerprint']:
            raise ValueError('Backend/model fingerprint changed; prior enhancement retained, no automatic rerun')
        validate_video(source, probe(enhanced))
        if not checkpoint.exists():atomic_json(checkpoint,record)
        if partial.exists() and os.path.samefile(partial,enhanced):partial.unlink()
        return record
    if checkpoint.exists():
        raise ValueError('Orphan checkpoint retained; inspect before retry')
    if pending_checkpoint.is_file() and partial.is_file():
        record=json.loads(pending_checkpoint.read_text())
        if (record.get('episode') != episode or record.get('master_sha256') != master_hash
                or record.get('sha256') != sha256(partial)
                or record.get('backend_fingerprint') != backend_plan()['backend_fingerprint']):
            raise ValueError('Pending enhancement publication differs; retained')
        validate_video(source,probe(partial))
        os.link(partial,enhanced);partial.unlink();atomic_json(checkpoint,record)
        return record
    plan = backend_plan()
    required = plan['additional_peak_bytes']
    # Every declared filesystem is checked separately; never add free space across mounts.
    check_space(enhanced.parent, required, reserve_gib)
    for volume in plan.get('extra_volumes',[]):
        if type(volume.get('additional_peak_bytes')) is not int or volume['additional_peak_bytes']<0:
            raise ValueError('Invalid independent-volume enhancement budget')
        check_space(volume['path'],volume['additional_peak_bytes'],reserve_gib)
    scratch = plan.get('scratch_path')
    if scratch:
        if type(plan.get('scratch_peak_bytes')) is not int or plan['scratch_peak_bytes'] < 0:
            raise ValueError('Invalid backend scratch budget')
        ancestor = Path(scratch)
        while not ancestor.exists():
            ancestor = ancestor.parent
        if ancestor.stat().st_dev != enhanced.parent.stat().st_dev:
            check_space(ancestor, plan['scratch_peak_bytes'], reserve_gib)
    if partial.exists():
        allowed=json.loads(backend_call(subprocess.check_output,
                           [str(backend),'--resume-check',str(master),str(partial)],text=True))
        if allowed.get('resume_allowed') is not True:
            raise ValueError('Unverified partial exists; retained, do not overwrite')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a') as out:
        backend_call(subprocess.run, [str(backend), str(master), str(partial), episode],
                     stdout=out, stderr=subprocess.STDOUT, check=True)
    if master.stat().st_mtime_ns != master_stat.st_mtime_ns or sha256(master) != master_hash:
        raise ValueError('Master changed during enhancement')
    checks = validate_video(source, probe(partial))
    record = {'episode':episode, 'master_sha256':master_hash, 'sha256':sha256(partial),
              'backend_fingerprint':plan['backend_fingerprint'], 'validation':checks,
              'completed_at':datetime.now(timezone.utc).isoformat(),
              'stage':'ENHANCED_READY', 'style_required':False,
              'audio_policy':'Final audio must come from untouched mixed master',
              'residual_hardsubs':'Retained per user authorization; no new subtitles burned'}
    # link() refuses overwrite even if another file appeared during processing.
    atomic_json(pending_checkpoint,record)
    os.link(partial, enhanced)
    partial.unlink()
    atomic_json(checkpoint, record)
    return record
