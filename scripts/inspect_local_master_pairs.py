"""Capture bounded source/master seek samples; never approve a master."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw
from episode_lifecycle import sha256, save

BG = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def capture(source, master, output, seconds):
    master = master.resolve(strict=True)
    before = master.stat()
    digest = sha256(master)
    output.mkdir(parents=True, exist_ok=False)
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                    '-of', 'json', str(source)], **BG))
    source_duration = float(info['format']['duration'])
    rows = []
    for index, stamp in enumerate(seconds):
        if stamp < 0:
            raise ValueError('Negative seek time')
        images = []
        for kind, path in [('source', source), ('master', master)]:
            if kind == 'source' and stamp >= source_duration:
                images.append(None)
                continue
            frame = output / f'{index:03d}-{kind}.jpg'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2',
                            '-ss', str(stamp), '-i', str(path), '-frames:v', '1',
                            '-vf', 'scale=640:360', '-n', str(frame)], check=True, **BG)
            if not frame.exists():
                raise ValueError('Requested frame was not decoded')
            images.append(frame)
        rows.append((stamp, images))
    pages = []
    for start in range(0, len(rows), 5):
        selected = rows[start:start+5]
        page = Image.new('RGB', (1280, len(selected)*385), 'white')
        draw = ImageDraw.Draw(page)
        for row, (stamp, frames) in enumerate(selected):
            for column, (kind, frame) in enumerate(zip(('SOURCE', 'MASTER'), frames)):
                draw.text((column*640+4, row*385+4), f'{kind} seek {stamp}s', fill='black')
                if frame:
                    with Image.open(frame) as picture:
                        page.paste(picture, (column*640, row*385+25))
        target = output / f'page-{start//5:03d}.jpg'
        page.save(target, quality=95)
        pages.append(dict(path=str(target), sha256=sha256(target), actually_viewed=False))
    after = master.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Master changed during capture')
    save(output / 'index.json', dict(master_sha256=digest, sample_seconds=seconds, pages=pages,
         visual_review_complete=False, source_paired_samples=sum(t < source_duration for t in seconds),
         scope='Bounded seek samples only; not exact frame alignment or whole-film acceptance'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source', 'master', 'output'):
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--seconds', nargs='+', type=float, required=True)
    args = parser.parse_args()
    capture(args.source, args.master, args.output, args.seconds)
