#!/usr/bin/env python3
"""Render bounded, consistently encoded picture-filter audition videos.

Uses an existing delivered video: this measures incremental processing, not a
replacement restoration pipeline from a now-retired master. No RIFE is run.
Run in a persistent process with stdout/stderr redirected outside the repository.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from preview_fast_filters import FILTERS


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def run(command, log):
    started = time.monotonic()
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Exit {result.returncode}; see {log}')
    return time.monotonic() - started


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams',
                             '-show_format', '-of', 'json', str(path)],
                            check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(result.stdout)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--input-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--clip', action='append', help='start:duration, max 60 seconds per clip')
    p.add_argument('--variant', action='append', choices=list(FILTERS))
    p.add_argument('--gpu', type=int, default=0)
    p.add_argument('--threads', type=int, default=4)
    args = p.parse_args()
    clips = [tuple(map(float, value.split(':'))) for value in
             (args.clip or ['60:60', '1700:60', '2800:60'])]
    if (not 1 <= args.threads <= 8 or not clips or len(clips) > 5
            or any(start < 0 or not 0 < duration <= 60 for start, duration in clips)
            or sum(duration for _, duration in clips) > 300):
        p.error('Bounded tests only: 1..5 clips, 0..60 seconds each, <=300 total; threads 1..8')
    if len(args.input_sha256) != 64 or any(c not in '0123456789abcdef' for c in args.input_sha256):
        p.error('Supply a lowercase SHA256 binding the agreed source')
    if args.output.exists():
        raise FileExistsError('Use a new audition directory; prior reports and videos are retained')
    if shutil.disk_usage(args.output.parent).free < 30 * 1024**3:
        raise RuntimeError('Need at least 30 GiB available before this bounded sample job')
    if sha256(args.input) != args.input_sha256:
        raise RuntimeError('Agreed source SHA mismatch')
    source_probe = probe(args.input)
    if any(start + duration > float(source_probe['format']['duration']) for start, duration in clips):
        p.error('A requested clip exceeds source duration')
    args.output.mkdir()
    report_path = args.output / 'report.json'
    report = {'status': 'running', 'started_utc': datetime.now(timezone.utc).isoformat(),
              'scope': __doc__, 'source': str(args.input.resolve()),
              'source_sha256': args.input_sha256, 'clips': clips, 'fps': 25,
              'encoder': 'hevc_nvenc main10 p6 hq vbr cq16 b:v0; FLAC s32/24bit',
              'gpu': args.gpu, 'threads': args.threads, 'variants': []}
    save(report_path, report)
    variants = args.variant or list(FILTERS)
    try:
        for name in variants:
            folder = args.output / name
            folder.mkdir()
            entry = {'name': name, 'filters': FILTERS[name], 'status': 'running', 'segments': []}
            report['variants'].append(entry)
            save(report_path, report)
            render_started = time.monotonic()
            parts = []
            for index, (start, duration) in enumerate(clips):
                part = folder / f'part_{index:02d}.mkv'
                command = ['ffmpeg', '-hide_banner', '-nostdin', '-n', '-threads', str(args.threads),
                           '-filter_threads', str(args.threads), '-ss', str(start), '-i', str(args.input),
                           '-t', str(duration), '-map', '0:v:0', '-map', '0:a:0',
                           '-vf', 'fps=25,' + FILTERS[name]
                           + ',scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=p010le',
                           '-c:v', 'hevc_nvenc', '-gpu', str(args.gpu), '-preset', 'p6',
                           '-tune', 'hq', '-profile:v', 'main10', '-rc', 'vbr', '-cq', '16', '-b:v', '0',
                           '-colorspace', 'bt709', '-color_trc', 'bt709', '-color_primaries', 'bt709',
                           '-color_range', 'tv', '-c:a', 'flac', '-sample_fmt', 's32', '-map_metadata', '-1',
                           '-metadata', f'comment=audition {name}; source {args.input_sha256}; {start}:{duration}',
                           str(part)]
                seconds = run(command, folder / f'part_{index:02d}.log')
                parts.append(part)
                entry['segments'].append({'source_start': start, 'duration': duration,
                                           'render_wall_seconds': seconds, 'command': command})
                save(report_path, report)
            # Relative, generated ASCII names avoid concat path quoting ambiguities.
            concat = folder / 'concat.txt'
            concat.write_text(''.join(f"file '{part.name}'\n" for part in parts), encoding='ascii')
            output = folder / f'{name}.mkv'
            join_seconds = run(['ffmpeg', '-hide_banner', '-nostdin', '-n', '-f', 'concat',
                                '-safe', '1', '-i', str(concat), '-map', '0:v:0', '-map', '0:a:0',
                                '-c', 'copy', str(output)], folder / 'concat.log')
            entry['render_and_join_wall_seconds'] = time.monotonic() - render_started
            entry['concat_wall_seconds'] = join_seconds
            entry['input_seconds'] = sum(duration for _, duration in clips)
            entry['processing_fps'] = entry['input_seconds'] * 25 / entry['render_and_join_wall_seconds']
            qc_started = time.monotonic()
            info = probe(output)
            video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
            audio = next(stream for stream in info['streams'] if stream['codec_type'] == 'audio')
            if (video['codec_name'] != 'hevc' or video['profile'] != 'Main 10'
                    or (video['width'], video['height']) != (1920, 1080)
                    or video['r_frame_rate'] != '25/1' or audio['codec_name'] != 'flac'
                    or video.get('color_space') != 'bt709' or video.get('color_range') != 'tv'
                    or audio.get('bits_per_raw_sample') != '24'
                    or audio.get('sample_rate') != '48000' or audio.get('channels') != 2
                    or abs(float(info['format']['duration']) - entry['input_seconds']) > 0.15):
                raise RuntimeError(f'Unexpected output specification: {info}')
            # Strict entire-sample video and audio decode, independently timed.
            run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
                 '-threads', str(args.threads), '-i', str(output), '-map', '0:v:0',
                 '-map', '0:a:0', '-f', 'null', '-'], folder / 'decode-check.log')
            entry.update({'status': 'verified', 'output': str(output.resolve()),
                          'sha256': sha256(output), 'bytes': output.stat().st_size,
                          'probe': info, 'qc_wall_seconds': time.monotonic() - qc_started})
            save(report_path, report)
            print(json.dumps({key: entry[key] for key in
                              ('name', 'status', 'render_and_join_wall_seconds', 'processing_fps', 'output')}), flush=True)
        report['status'] = 'verified'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
        raise
    finally:
        report['updated_utc'] = datetime.now(timezone.utc).isoformat()
        save(report_path, report)


if __name__ == '__main__':
    main()
