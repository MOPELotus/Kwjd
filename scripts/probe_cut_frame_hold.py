"""Lossless bounded frame-hold experiment; never publishes a production master.

Proves decoded pixel identity outside explicitly supplied bad frames and keeps
the sample clock/count unchanged. Actual viewing and the fixed neural pipeline
must still be validated separately before any production repair.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import time

from enhance_stage import atomic_json, check_space, sha256


def frame_hashes(path):
    result = subprocess.check_output([
        'ffmpeg', '-nostdin', '-v', 'error', '-threads', '2', '-i', str(path),
        '-map', '0:v:0', '-an', '-c:v', 'rawvideo', '-threads:v', '1',
        '-f', 'framehash', '-hash', 'sha256', '-'
    ], text=True)
    return [line.rsplit(',', 1)[1].strip() for line in result.splitlines()
            if line.strip() and not line.startswith('#')]


def clock(path):
    data = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=time_base,pix_fmt:packet=pts', '-of', 'json', str(path)
    ]))
    stream = data['streams'][0]
    base = Fraction(stream['time_base'])
    return stream['pix_fmt'], sorted(Fraction(row['pts']) * base for row in data['packets'])


def probe(args):
    if not 5 <= args.frames <= 60 or args.start < 0:
        raise ValueError('Probe must contain 5–60 native30 frames')
    relative = args.bad_frame - args.start
    donor = relative - 1
    if not 1 <= relative < args.frames - 1:
        raise ValueError('Bad frame needs retained clean neighbors inside probe')
    master = args.master.resolve(strict=True)
    before = master.stat()
    if sha256(master) != args.master_sha256:
        raise ValueError('Received master identity changed')
    area = args.output.absolute()
    if area.exists():
        raise FileExistsError('Existing evidence retained; choose a new directory')
    check_space(area.parent, 256 * 1024**2, 5)
    area.mkdir()
    info = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=r_frame_rate,pix_fmt,start_time', '-of', 'json', str(master)
    ]))['streams'][0]
    if (Fraction(info['r_frame_rate']) != 30 or info['pix_fmt'] != 'yuv420p10le'
            or abs(float(info.get('start_time', 0))) > .001):
        raise ValueError('Probe requires zero-origin native30 Main10 received master')
    original = area / 'original.lossless.mkv'
    repaired = area / 'candidate.lossless.mkv'
    graph = (f'[0:v]trim=start_frame={args.start}:end_frame={args.start + args.frames},'
             'setpts=PTS-STARTPTS,split=3[original][a][b];'
             f'[a][b]freezeframes=first={relative}:last={relative}:replace={donor}[candidate]')
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '2',
               '-filter_complex_threads', '2', '-i', str(master), '-filter_complex', graph]
    for label, path in [('original', original), ('candidate', repaired)]:
        command += ['-map', f'[{label}]', '-an', '-frames:v', str(args.frames),
                    '-fps_mode', 'passthrough', '-c:v', 'ffv1', '-level', '3',
                    '-pix_fmt', 'yuv420p10le', '-threads:v', '2', '-n', str(path)]
    with (area / 'capture.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True)
    actual_original = frame_hashes(original)
    actual_repaired = frame_hashes(repaired)
    expected = list(actual_original)
    expected[relative] = expected[donor]
    if len(expected) != args.frames or actual_repaired != expected:
        raise ValueError('Decoded pixels changed outside explicit donor replacement')
    original_clock, repaired_clock = clock(original), clock(repaired)
    if original_clock != repaired_clock:
        raise ValueError('Sample pixel format or complete clock changed')
    stamps = original_clock[1]
    if len(stamps) != args.frames or any(abs(t - Fraction(i, 30)) > Fraction(1, 1000)
                                       for i, t in enumerate(stamps)):
        raise ValueError('Sample count or native frame clock differs')
    after = master.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Received master changed during probe')
    atomic_json(area / 'result.json', dict(
        schema_version=1, at=time.time(), scope='LOSSLESS_INPUT_REPAIR_PROBE_NOT_PRODUCTION',
        master_path=str(master), master_sha256=args.master_sha256,
        source_start=args.start, frames=args.frames, bad_frame=args.bad_frame,
        donor_frame=args.bad_frame - 1, original_path=str(original),
        candidate_path=str(repaired), original_sha256=sha256(original),
        candidate_sha256=sha256(repaired), pixel_format=original_clock[0],
        original_frame_hashes=actual_original, candidate_frame_hashes=actual_repaired,
        unchanged_pixels_outside_replacement=True, replacement_equals_clean_previous_frame=True,
        original_and_candidate_clock_identical=True, frame_count_unchanged=True,
        audio_touched=False, audio_present_in_sample=False, neural_validation_performed=False,
        actual_visual_review_complete=False, production_approved=False, command=command))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--master-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', type=int, required=True)
    parser.add_argument('--bad-frame', type=int, required=True)
    parser.add_argument('--frames', type=int, default=20)
    probe(parser.parse_args())
