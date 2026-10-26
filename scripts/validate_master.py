#!/usr/bin/env python3
"""Validate master structure, sampled decoding, audio peaks and SHA-256.

This records technical checks, not human visual or lip-sync approval.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--file', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--expected-duration', type=float, required=True)
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
    assert abs(duration - args.expected_duration) <= 0.5, 'Wrong duration'
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
    digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
    after = path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'File changed'
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'state': 'TECHNICALLY_VALIDATED', 'path': str(path), 'bytes': after.st_size,
              'sha256': digest, 'duration_seconds': duration, 'video': video, 'audio': audio,
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
