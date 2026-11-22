"""CPU per-frame subtitle-band change evidence. Never an automatic gap approval.

Decode every source frame in a bounded interval, retain actual showinfo PTS and
per-frame white-ink metrics, and save candidate-change images. Candidate review
alone does not prove detector recall; masks/regions and limitations are explicit.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.subtitle_review_evidence import sha


def scan(root, episode, start, end, output):
    if not re.fullmatch(r'S03E(?:0[2-9]|1[0-4])', episode):
        raise ValueError('Episode outside scope')
    if not 0 <= start < end or end - start > 120:
        raise ValueError('Expected bounded interval <=120 seconds')
    if shutil.disk_usage(root).free < 27 * 1024**3:
        raise ValueError('Need 25 GiB reserve plus 2 GiB evidence allowance')
    source = root / 'input' / (episode + '.mp4')
    source_sha = sha(source)
    receipt = json.loads(source.with_suffix('.mp4.transfer.json').read_text())
    if not receipt.get('transfer_verified') or receipt['sha256'] != source_sha:
        raise ValueError('Source identity differs')
    output.mkdir(parents=True, exist_ok=False)
    command = ['ffmpeg', '-nostdin', '-v', 'info', '-threads', '2', '-copyts',
               '-seek_timestamp', '1', '-ss', str(start), '-t', str(end-start), '-i', str(source),
               '-an', '-filter_threads', '1',
               '-vf', f'trim=start={start}:end={end},crop=1920:220:0:800,showinfo', '-fps_mode', 'passthrough',
               '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    metrics = []
    candidates = []
    previous = None
    previous_frame = None
    anchor = None
    frame_bytes = 1920 * 220 * 3
    with (output / 'decode.log').open('w') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)
        try:
            while True:
                raw = process.stdout.read(frame_bytes)
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise ValueError('Truncated raw frame')
                frame = np.frombuffer(raw, np.uint8).reshape(220, 1920, 3)
                # Full band includes extra line positions. Exclude animated right logo
                # only from change detector; saved frames retain the entire width.
                low = frame.min(axis=2)
                high = frame.max(axis=2)
                white = (low > 180) & ((high.astype(np.int16) - low) < 65)
                mask = white[60:190, 180:1440]
                count = int(mask.sum())
                if previous is None:
                    adjacent = cumulative = 1.0
                else:
                    adjacent = float(np.count_nonzero(mask != previous) / max(count, int(previous.sum()), 1))
                    cumulative = float(np.count_nonzero(mask != anchor) / max(count, int(anchor.sum()), 1))
                # Preserve every detected change, even one-frame candidates. A low
                # threshold intentionally accepts scene/background false positives.
                selected = previous is None or adjacent >= .10 or cumulative >= .15
                n = len(metrics)
                if selected:
                    path = output / f'frame-{n:06d}.jpg'
                    Image.fromarray(frame).save(path, quality=96, subsampling=0)
                    candidate = dict(frame_index=n, path=str(path), sha256=sha(path))
                    if previous_frame is not None:
                        before = output / f'frame-{n:06d}-before.jpg'
                        Image.fromarray(previous_frame).save(before, quality=96, subsampling=0)
                        candidate['before'] = dict(frame_index=n-1, path=str(before), sha256=sha(before))
                    candidates.append(candidate)
                    anchor = mask.copy()
                metrics.append(dict(frame_index=n, white_pixels=count,
                                    white_by_vertical_band=[int(white[k:k+20, :1440].sum()) for k in range(0,220,20)],
                                    adjacent_change=round(adjacent,6), anchor_change=round(cumulative,6),
                                    candidate=selected))
                previous = mask.copy()
                previous_frame = frame
            if process.wait() != 0:
                raise ValueError('FFmpeg decode failed')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()
    pts = [float(x) for x in re.findall(r'\bn:\s*\d+\s+pts:.*?pts_time:([\d.e+-]+)', (output/'decode.log').read_text())]
    if not metrics or len(pts) != len(metrics):
        raise ValueError('Frame count/PTS mismatch')
    for row, timestamp in zip(metrics, pts):
        row['source_pts_seconds'] = timestamp
    for row in candidates:
        row['source_pts_seconds'] = pts[row['frame_index']]
        if 'before' in row:
            row['before']['source_pts_seconds'] = pts[row['before']['frame_index']]
    metrics_path = output / 'frames.json'
    metrics_path.write_text(json.dumps(metrics, separators=(',', ':')) + '\n')
    pages = []
    for offset in range(0, len(candidates), 8):
        group = candidates[offset:offset+8]
        sheet = Image.new('RGB', (960, 140*len(group)), 'white')
        for j, row in enumerate(group):
            image = Image.open(row['path'])
            image.thumbnail((960,110))
            sheet.paste(image, (0,j*140+25))
            ImageDraw.Draw(sheet).text((8,j*140+6), f"frame {row['frame_index']} source PTS={row['source_pts_seconds']:.6f}s", fill='black')
        path = output / f'page-{offset//8:03d}.jpg'
        sheet.save(path, quality=97, subsampling=0)
        pages.append(dict(path=str(path), sha256=sha(path)))
    result = dict(scope='ALL_DECODED_FRAMES_MACHINE_SCAN_NOT_REVIEW', episode=episode,
                  source=str(source), source_sha256=source_sha, command=command,
                  requested_start=start, requested_end=end, first_pts=pts[0], last_pts=pts[-1],
                  decoded_frame_count=len(pts), pts_deltas=sorted(set(round(b-a,6) for a,b in zip(pts,pts[1:]))),
                  detector='low>180 and RGB range<65; change ROI x180:1440 y860:990',
                  limitations=['white-ink detector is not OCR', 'no automatic text/absence judgment',
                               'small/subtle or outside-ROI text changes require additional audit',
                               'scene/background changes yield false positives'],
                  gap_audit_complete=False, short_line_audit_complete=False, two_line_audit_complete=False,
                  frame_metrics=dict(path=str(metrics_path), sha256=sha(metrics_path)),
                  candidates=candidates, pages=pages)
    (output/'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--episode', required=True)
    parser.add_argument('--start', required=True, type=float)
    parser.add_argument('--end', required=True, type=float)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = scan(args.root, args.episode, args.start, args.end, args.output)
    print(json.dumps(dict(frames=result['decoded_frame_count'], candidates=len(result['candidates']), pages=result['pages'])))
