#!/usr/bin/env python3
"""Make diagnostic A/B crops from saved same-time baseline and delivered frames.

Inputs are unmodified source_<seconds>.png and final_<seconds>.png. Choose times
shared by both source/output clocks and verify the pose matches before drawing
conclusions. Nearest-neighbour magnification treats both sides identically.
Pixel differences include encoding/colour conversion; they are NOT quality scores.
"""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageStat


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', required=True, type=Path)
    p.add_argument('--sample', required=True, action='append', help='seconds,x,y,width,height')
    p.add_argument('--scale', type=int, default=2)
    p.add_argument('--output-dir', type=Path, help='Separate figures from original saved frames')
    p.add_argument('--before-label', default='Before: DaVinci master')
    p.add_argument('--after-label', default='After: delivered final')
    args = p.parse_args()
    if args.scale not in (1, 2, 3, 4):
        p.error('Use equal integer magnification from 1 to 4')
    results = []
    output_dir = args.output_dir or args.directory
    output_dir.mkdir(parents=True, exist_ok=True)
    for sample in args.sample:
        stamp, x, y, width, height = map(int, sample.split(','))
        if min(x, y) < 0 or min(width, height) <= 0:
            raise ValueError('Crop must be positive and within both native frames')
        paths = [args.directory / f'{side}_{stamp}.png' for side in ('source', 'final')]
        frames = [Image.open(path).convert('RGB') for path in paths]
        if frames[0].size != frames[1].size or x + width > frames[0].width or y + height > frames[0].height:
            raise ValueError('Different frame sizes or out-of-bounds crop')
        crops = [im.crop((x, y, x + width, y + height)) for im in frames]
        padding, header = 16, 70
        w, h = width * args.scale, height * args.scale
        canvas = Image.new('RGB', (2 * w + 3 * padding, h + header + padding), '#20242b')
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default(size=21)
        for index, (label, crop) in enumerate(zip((args.before_label, args.after_label), crops)):
            left = padding + index * (w + padding)
            draw.text((left, 8), label, font=font, fill='white')
            draw.text((left, 36), f'{stamp}s | same crop | {args.scale}x nearest', font=font, fill='#b9c8dc')
            canvas.paste(crop.resize((w, h), Image.Resampling.NEAREST), (left, header))
        output = output_dir / f'comparison_{stamp}.png'
        if output.exists():
            raise FileExistsError(output)
        canvas.save(output)
        difference = ImageStat.Stat(ImageChops.difference(*crops))
        results.append({'seconds': stamp, 'crop_xywh': [x, y, width, height],
                        'scale': args.scale, 'before': str(paths[0]), 'after': str(paths[1]),
                        'input_sha256': [digest(path) for path in paths],
                        'mean_absolute_rgb_difference_0_255': difference.mean,
                        'output': str(output), 'output_sha256': digest(output)})
        print(output)
    report = output_dir / 'comparisons.json'
    if report.exists():
        raise FileExistsError(report)
    report.write_text(json.dumps({'scope': __doc__, 'labels': [args.before_label, args.after_label],
                                 'samples': results}, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
