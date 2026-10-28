#!/usr/bin/env python3
"""Validate master structure, sampled decoding, audio peaks and SHA-256.

This records technical checks, not human visual or lip-sync approval.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def duration_check(path, duration, expected, approval_path=None, master_sha256=None):
    """Default tolerance stays0.5s; explicit exceptions bind one master SHA."""
    if not all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in (duration,expected)):
        raise ValueError('Duration must be finite and positive')
    delta=duration-expected
    audit={'expected_duration_seconds':expected,'actual_duration_seconds':duration,
           'delta_seconds':delta,'default_tolerance_seconds':0.5,
           'default_duration_gate_passed':abs(delta)<=0.5,'exception':None}
    if approval_path is not None:
        raw=Path(approval_path).read_bytes(); approval=json.loads(raw.decode('utf-8-sig'))
        match=re.fullmatch(r'(S01E[0-9]{2,3})[.]clean-master[.](mov|mp4|mkv)',Path(path).name)
        if (not match or type(approval.get('schema_version')) is not int
                or approval.get('schema_version')!=1 or approval.get('approved') is not True
                or approval.get('approved_by')!='human'
                or approval.get('scope')!='extra_tail_background_only'
                or approval.get('episode')!=match[1]
                or not isinstance(master_sha256,str)
                or not re.fullmatch(r'[0-9a-f]{64}',master_sha256)
                or approval.get('master_sha256')!=master_sha256
                or not isinstance(approval.get('human_quote'),str) or not approval['human_quote'].strip()
                or not isinstance(approval.get('reason'),str) or not approval['reason'].strip()
                or delta<=0):
            raise ValueError('Duration exception needs explicit matching human tail approval')
        for key,actual in [('expected_duration_seconds',expected),('actual_duration_seconds',duration)]:
            value=approval.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or abs(value-actual)>0.000001:
                raise ValueError('Duration exception has stale duration binding: '+key)
        audit['exception']={'approval_path':str(Path(approval_path).resolve()),
            'approval_sha256':hashlib.sha256(raw).hexdigest(),'approval':approval}
    if not audit['default_duration_gate_passed'] and audit['exception'] is None:
        raise ValueError('Wrong duration; matching explicit human exception required')
    return audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--expected-duration', type=float, required=True)
    p.add_argument('--duration-approval', type=Path, help='Optional SHA-bound explicit human tail-background exception')
    args = p.parse_args()
    path = args.file.resolve(strict=True)
    before = path.stat()
    info = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    duration = float(info['format']['duration'])
    assert (video['width'], video['height']) == (1920, 1080), 'Wrong dimensions'
    assert video['r_frame_rate'] == '25/1', 'Wrong frame rate'
    digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() if args.duration_approval else None
    duration_audit=duration_check(path,duration,args.expected_duration,args.duration_approval,digest)
    assert audio['codec_name'] == 'pcm_s24le', 'Expected PCM24 mixed audio'
    assert int(audio['sample_rate']) >= 48000 and audio['channels'] == 2, 'Wrong audio format'
    samples = [0, duration / 2, max(0, duration - 4)]
    for start in samples:
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-ss', str(start),
                        '-i', str(path), '-t', '3', '-map', '0:v:0', '-map', '0:a:0',
                        '-f', 'null', '-'], check=True)
    peaks = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path),
                            '-map', '0:a:0', '-af', 'volumedetect', '-vn', '-sn', '-dn',
                            '-f', 'null', '-'], capture_output=True, text=True, check=True)
    peak = re.search(r'max_volume: ([-\d.]+) dB', peaks.stderr)
    digest = digest or hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'File changed'
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'state': 'TECHNICALLY_VALIDATED', 'path': str(path), 'bytes': after.st_size,
              'sha256': digest, 'duration_seconds': duration, 'duration_audit':duration_audit,
              'video': video, 'audio': audio,
              'decode_sample_seconds': samples,
              'audio_max_volume_db': float(peak[1]) if peak else None,
              'clipping_review_needed': bool(peak and float(peak[1]) >= 0),
              'visual_and_lipsync_approved': False,
              'upload_state': 'WAIT_REMOTE_DISK_BUDGET'}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    temp = args.receipt.with_suffix('.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, args.receipt)
    print(json.dumps({k: report[k] for k in ['state', 'bytes', 'sha256', 'audio_max_volume_db']}, indent=2))


if __name__ == '__main__':
    main()
