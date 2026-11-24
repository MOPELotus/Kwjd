"""Capture bounded source-cut/master neighbors for actual visual inspection.

This diagnostic never approves a master, modifies media or starts production.
The supplied clock offset is a search hypothesis, not a timing approval.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess

from enhance_stage import atomic_json, check_space, sha256
from ffmpeg_timestamps import showinfo_frames


def inspect(args):
    from PIL import Image, ImageDraw

    if not 1 <= args.limit <= 24 or not 1 <= args.seconds <= 600:
        raise ValueError('Bounded diagnostic requires 1–24 cuts and at most600 seconds')
    source = args.source.resolve(strict=True)
    master = args.master.resolve(strict=True)
    area = args.output.absolute()
    if area.exists():
        raise FileExistsError('Retain existing diagnostic evidence; choose a new directory')
    check_space(area.parent, 128 * 1024**2, 5)
    area.mkdir()
    before = master.stat()
    if sha256(master) != args.master_sha256:
        raise ValueError('Master differs from supplied received identity')
    info = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate,nb_frames,start_time', '-of', 'json', str(master)
    ]))['streams'][0]
    if Fraction(info['r_frame_rate']) != 30 or abs(float(info.get('start_time', 0))) > .001:
        raise ValueError('This diagnostic requires the received zero-origin CFR30 master')
    full = int(info['nb_frames'])
    commands = []

    def capture(command, log):
        commands.append(command)
        with log.open('w') as out:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=out, check=True)
        return showinfo_frames(log.read_text())

    scenes = capture([
        'ffmpeg', '-nostdin', '-v', 'info', '-threads', '2', '-filter_threads', '2',
        '-copyts', '-i', str(source), '-t', str(args.seconds), '-map', '0:v:0', '-an',
        '-vf', "scale=480:270,select='gt(scene,0.25)',showinfo", '-frames:v', str(args.limit),
        '-fps_mode', 'passthrough', '-n', str(area / 'source-%03d.jpg')
    ], area / 'source-decode.log')
    centers = [round((row['seconds'] + args.offset_ms / 1000) * 30) for row in scenes]
    indices = sorted({i for center in centers for i in range(center - 4, center + 5)
                      if 0 <= i < full})
    expression = '+'.join(f'eq(n\\,{i})' for i in indices)
    frames = capture([
        'ffmpeg', '-nostdin', '-v', 'info', '-threads', '2', '-filter_threads', '2',
        '-copyts', '-i', str(master), '-t', str((max(indices) + 1) / 30 + .1),
        '-map', '0:v:0', '-an', '-vf', f'select={expression},scale=480:270,showinfo',
        '-frames:v', str(len(indices)), '-fps_mode', 'passthrough', '-n',
        str(area / 'master-%03d.jpg')
    ], area / 'master-decode.log')
    if len(frames) != len(indices) or any(abs(row['seconds'] - i / 30) > .001
                                        for row, i in zip(frames, indices)):
        raise ValueError('Actual decoded frame PTS differ from selected native frame indices')
    mapping = {i: area / f'master-{k + 1:03d}.jpg' for k, i in enumerate(indices)}
    pages = []
    for first in range(0, len(scenes), 6):
        rows = scenes[first:first + 6]
        canvas = Image.new('RGB', (2560, len(rows) * 166), 'white')
        draw = ImageDraw.Draw(canvas)
        for row_index, scene in enumerate(rows):
            ordinal = first + row_index
            selected = [i for i in range(centers[ordinal] - 4, centers[ordinal] + 5)
                        if i in mapping]
            entries = [(area / f'source-{ordinal + 1:03d}.jpg',
                        f'SOURCE cut {scene["seconds"]:.6f}')] + [
                            (mapping[i], f'MASTER n={i} t={i / 30:.6f}') for i in selected]
            for column, (path, label) in enumerate(entries):
                with Image.open(path) as picture:
                    canvas.paste(picture.resize((256, 144)), (column * 256, row_index * 166 + 22))
                draw.text((column * 256 + 2, row_index * 166 + 2), label, fill='black')
        target = area / f'page-{first // 6:03d}.jpg'
        canvas.save(target, quality=95)
        pages.append(dict(path=str(target), sha256=sha256(target), actually_viewed=False))
    after = master.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Master changed during evidence capture')
    atomic_json(area / 'index.json', dict(
        schema_version=1, episode=args.episode, source=str(source), master=str(master),
        master_sha256=args.master_sha256, master_bytes=before.st_size,
        offset_hypothesis_ms=args.offset_ms, source_scan_seconds=args.seconds,
        scene_candidates=scenes, master_frame_indices=indices, decoded_master_frames=frames,
        pages=pages, commands=commands, visual_review_complete=False,
        scope='Bounded scene-score samples, actual integer PTS; no all-cut, timing or production approval'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'master', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--episode', required=True)
    parser.add_argument('--master-sha256', required=True)
    parser.add_argument('--offset-ms', type=int, required=True)
    parser.add_argument('--seconds', type=int, default=240)
    parser.add_argument('--limit', type=int, default=6)
    inspect(parser.parse_args())
