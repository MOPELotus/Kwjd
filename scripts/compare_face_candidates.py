#!/usr/bin/env python3
"""Compare preserved E49 original-master still restorations and video strips.

No extra sharpening or interpolation. Pixel differences measure change, not
quality; sampled strips cannot establish whole-video temporal stability.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat


VARIANTS = ('gfpgan14', 'codeformer_w05', 'codeformer_w08')
LABELS = ('GFPGAN 1.4', 'CodeFormer 0.5', 'CodeFormer 0.8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def labeled_rows(rows, labels):
    width = rows[0].width
    canvas = Image.new('RGB', (width, sum(im.height + 34 for im in rows)), '#20242b')
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=22)
    top = 0
    for im, label in zip(rows, labels):
        draw.text((8, top + 4), label, fill='white', font=font)
        canvas.paste(im, (0, top + 34))
        top += im.height + 34
    return canvas


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True, type=Path, help='Completed audition root')
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve prior comparisons; select a new directory')
    args.output.mkdir(parents=True)
    report = {'scope': __doc__, 'stills': [], 'motion_strips': []}
    for stamp, x, y, w, h in [(60, 880, 140, 260, 330), (1700, 830, 340, 200, 230),
                              (2800, 880, 190, 260, 330)]:
        folders = [args.root / 'comparisons/original-master' / v for v in VARIANTS]
        sources = [folder / f'source_{stamp}.png' for folder in folders]
        assert len({digest(path) for path in sources}) == 1, 'Different source stills'
        paths = [sources[0]] + [folder / f'final_{stamp}.png' for folder in folders]
        frames = [Image.open(path).convert('RGB') for path in paths]
        assert len({frame.size for frame in frames}) == 1
        assert x + w <= frames[0].width and y + h <= frames[0].height
        crops = [im.crop((x, y, x+w, y+h)) for im in frames]
        canvas = Image.new('RGB', (4*w*2, 2*h+54), '#20242b')
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default(size=19)
        for i, (crop, label) in enumerate(zip(crops, ('Original master',) + LABELS)):
            draw.text((i*w*2+6, 5), label, fill='white', font=font)
            draw.text((i*w*2+6, 28), f'{stamp}s / 2x nearest', fill='#b9c8dc', font=font)
            canvas.paste(crop.resize((2*w, 2*h), Image.Resampling.NEAREST), (i*2*w, 54))
        output = args.output / f'still_{stamp}.png'
        canvas.save(output)
        metrics = []
        for i, j in itertools.combinations(range(1, 4), 2):
            diff = ImageChops.difference(crops[i], crops[j])
            pixels = list(diff.get_flattened_data())
            metrics.append({'pair': [LABELS[i-1], LABELS[j-1]],
                            'mean_absolute_rgb_0_255': ImageStat.Stat(diff).mean,
                            'pixels_max_channel_difference_gt2_fraction':
                            sum(max(pixel)>2 for pixel in pixels)/len(pixels)})
        report['stills'].append({'seconds': stamp, 'crop_xywh': [x,y,w,h],
                                 'source_sha256': digest(sources[0]),
                                 'inputs': [{'path': str(path), 'sha256': digest(path)} for path in paths],
                                 'pairs': metrics, 'output': str(output)})
        print(output)
    for stamp in (1, 61, 121):
        paths = [args.root / 'motion-review' / v / f'sequence_{stamp}.png' for v in VARIANTS]
        frames = [Image.open(path).convert('RGB') for path in paths]
        assert all(im.size == frames[0].size for im in frames)
        half = frames[0].height // 2
        baselines = [im.crop((0, 0, im.width, half)) for im in frames]
        baseline_differences = []
        for im in baselines[1:]:
            diff = ImageChops.difference(baselines[0], im)
            mean = ImageStat.Stat(diff).mean
            maximum = max(high for low, high in diff.getextrema())
            # Independently decoded RGB strips have tiny rounding differences.
            # Reject meaningful clock/pose differences instead of requiring
            # byte identity; retain measured bounds in the evidence report.
            assert max(mean) < 0.01 and maximum <= 4, 'Baseline strips differ materially'
            baseline_differences.append({'mean_absolute_rgb_0_255': mean, 'max_channel_difference': maximum})
        rows = [baselines[0]] + [im.crop((0, half, im.width, im.height)) for im in frames]
        output = args.output / f'motion_{stamp}.png'
        labeled_rows(rows, ('Common video baseline',) + LABELS).save(output)
        report['motion_strips'].append({'sample_seconds': stamp, 'fps_sampled': 5,
                                        'window_seconds': 1, 'output': str(output),
                                        'baseline_conversion_differences': baseline_differences,
                                        'input_sha256': [digest(path) for path in paths]})
        print(output)
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
