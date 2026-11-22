"""Pair explicitly selected scan changes with their actual preceding frames.

This is evidence presentation only. It makes no text or timing decision.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw
from subtitle_review_evidence import sha


def pair(root, index, timestamps, output):
    bounds = (root / 'ocr/subtitle-review').resolve()
    index = index.resolve(strict=True)
    if not index.is_relative_to(bounds) or not output.resolve().is_relative_to(bounds):
        raise ValueError('Evidence must stay within the review data directory')
    data = json.loads(index.read_text())
    rows = []
    for timestamp in timestamps:
        matches = [r for r in data['candidates'] if abs(r['source_pts_seconds']-timestamp) < .000001]
        if len(matches) != 1 or 'before' not in matches[0]:
            raise ValueError('Exact change frame and preceding frame required')
        row = matches[0]
        for frame in [row['before'], row]:
            path = Path(frame['path']).resolve(strict=True)
            if not path.is_relative_to(bounds) or sha(path) != frame['sha256']:
                raise ValueError('Frame path/hash differs')
        if row['frame_index'] != row['before']['frame_index'] + 1:
            raise ValueError('Frames are not adjacent')
        rows.append(row)
    if not rows:
        raise ValueError('Empty explicit selection')
    output.mkdir(parents=True, exist_ok=False)
    pages = []
    for offset in range(0, len(rows), 4):
        group = rows[offset:offset+4]
        sheet = Image.new('RGB', (960,280*len(group)), 'white')
        for j, row in enumerate(group):
            for k, frame in enumerate([row['before'], row]):
                y = j*280+k*140
                image = Image.open(frame['path'])
                image.thumbnail((960,110))
                sheet.paste(image, (0,y+25))
                ImageDraw.Draw(sheet).text((8,y+6), f"{'BEFORE' if k == 0 else 'AFTER'} PTS={frame['source_pts_seconds']:.6f}s frame={frame['frame_index']}", fill='black')
        path = output / f'page-{offset//4:03d}.jpg'
        sheet.save(path, quality=97, subsampling=0)
        pages.append(dict(path=str(path), sha256=sha(path)))
    evidence = dict(scope='ADJACENT_SOURCE_FRAME_PAIRS_NOT_REVIEW', source_sha256=data['source_sha256'],
                    source_index=dict(path=str(index), sha256=sha(index)), rows=rows, pages=pages)
    (output/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--times', nargs='+', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = pair(args.root, args.index, args.times, args.output)
    print(json.dumps(dict(rows=len(result['rows']), pages=result['pages'])))
