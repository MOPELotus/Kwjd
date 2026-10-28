#!/usr/bin/env python3
"""Non-production lightweight filter previews and bounded throughput measurements.

Never changes a delivered video. Images come from preserved pre-restoration frames.
Benchmarks decode an existing video to null; they exclude inference, encoding and
quality validation, and therefore are NOT a complete episode ETA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


FILTERS = {
    'baseline': 'format=yuv444p,format=rgb24',
    'clarity': 'format=yuv444p,cas=strength=0.6:planes=1,eq=contrast=1.03:saturation=1.02,format=rgb24',
    'crisp': 'format=yuv444p,unsharp=13:13:0.18:3:3:0,cas=strength=0.55:planes=1,eq=contrast=1.04:saturation=1.03,format=rgb24',
}


def run(args):
    started = time.monotonic()
    result = subprocess.run(args, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return elapsed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--baseline-dir', type=Path)
    p.add_argument('--video-for-timing', type=Path)
    p.add_argument('--seconds', type=int, default=20)
    p.add_argument('--start', type=float, default=60)
    p.add_argument('--threads', type=int, default=4)
    args = p.parse_args()
    if not 1 <= args.seconds <= 60 or not 1 <= args.threads <= 8 or args.start < 0:
        p.error('Bounded tests only: 1..60 seconds, 1..8 threads, nonnegative start')
    if not args.baseline_dir and not args.video_for_timing:
        p.error('Supply preview images or an existing timing video')
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / 'report.json'
    if report.exists():
        raise FileExistsError(report)
    evidence = {'scope': __doc__, 'filters': FILTERS, 'previews': [], 'benchmarks': []}
    common = ['ffmpeg', '-hide_banner', '-v', 'error', '-nostdin', '-threads', str(args.threads),
              '-filter_threads', str(args.threads)]
    if args.baseline_dir:
        for source in sorted(args.baseline_dir.glob('source_*.png')):
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            for name, filters in FILTERS.items():
                folder = args.output / name
                folder.mkdir(exist_ok=True)
                before, after = folder / source.name, folder / source.name.replace('source_', 'final_', 1)
                if before.exists() or after.exists():
                    raise FileExistsError('Previous preview retained; use a new output directory')
                # Byte-identical baseline and only a generated candidate image.
                before.write_bytes(source.read_bytes())
                seconds = run(common + ['-n', '-i', str(source), '-vf', filters,
                                       '-frames:v', '1', '-update', '1', str(after)])
                evidence['previews'].append({'source': str(source), 'source_sha256': digest,
                                             'variant': name, 'output': str(after), 'wall_seconds': seconds})
    if args.video_for_timing:
        for name, filters in FILTERS.items():
            elapsed = run(common + ['-ss', str(args.start), '-i', str(args.video_for_timing),
                                   '-t', str(args.seconds), '-an', '-sn', '-vf', 'fps=25,' + filters,
                                   '-f', 'null', '-'])
            result = {'variant': name, 'input_seconds': args.seconds,
                      'wall_seconds': elapsed, 'video_seconds_per_wall_second': args.seconds / elapsed}
            evidence['benchmarks'].append(result)
            print(json.dumps(result), flush=True)
        baseline = evidence['benchmarks'][0]['wall_seconds']
        for row in evidence['benchmarks']:
            row['incremental_wall_seconds_vs_baseline'] = row['wall_seconds'] - baseline
        evidence['timing_scope'] = 'HEVC decode, fps=25, colour conversion and CPU filters to null. No RIFE, audio, encoder or disk video output.'
        evidence['benchmark_video'] = str(args.video_for_timing.resolve())
        evidence['threads'] = args.threads
    report.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
