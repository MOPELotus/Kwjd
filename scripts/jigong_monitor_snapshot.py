#!/usr/bin/env python3
"""Read-only bounded telemetry; may be piped to the cloud's python3 stdin."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess


def read(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def tail(path, limit=192 * 1024):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - limit)
            stream.seek(start)
            data = stream.read(limit)
        if start:
            data = data.partition(b'\n')[2]
        return data.decode('utf-8', errors='replace').splitlines()
    except OSError:
        return []


def segment_stats(post, ep, job):
    result = dict(verified_frames=0, total_frames=None, running_frames=None,
                  percent=None, verified_segments=0, fps=None, eta_seconds=None)
    candidates = list((post / 'cache' / ep).glob('**/context.json'))[:64]
    contexts = []
    for path in candidates:
        context = read(path)
        if (context.get('episode') == ep
                and context.get('master_sha256') == job.get('master_sha256')
                and isinstance(context.get('input_frames'), int)):
            contexts.append((path.stat().st_mtime, path, context))
    if not contexts:
        return result
    _, header, context = max(contexts, key=lambda row: row[0])
    total = context['input_frames']
    size = context.get('segment_source_frames', 0)
    if total <= 0 or not isinstance(size, int) or size <= 0:
        return result
    directory = header.parent
    count = frames = 0
    indexes = set()
    completed = read(directory / 'completed.json')
    if completed and all(completed.get(k) == context.get(k) for k in
                         ('episode', 'master_sha256', 'backend_fingerprint', 'input_frames')):
        frames = total
        count = (total + size - 1) // size
    else:
        for path in directory.glob('*.ready.json'):
            if not re.fullmatch(r'\d{6}\.ready\.json', path.name):
                continue
            receipt = read(path)
            index = int(path.name[:6])
            selected = min(size, total - index * size)
            if (0 < selected <= size and receipt.get('index') == index
                    and receipt.get('source_start') == index * size
                    and receipt.get('source_frames') == selected
                    and all(receipt.get(k) == context.get(k) for k in
                            ('episode', 'master_sha256', 'backend_fingerprint'))):
                frames += selected
                count += 1
                indexes.add(index)
    result.update(verified_frames=frames, total_frames=total,
                  verified_segments=count, percent=round(100 * frames / total, 2))
    running = frames
    next_index = 0
    while next_index in indexes:
        next_index += 1
    next_start = next_index * size
    selected = min(size, total - next_start)
    for line in tail(post / 'logs' / (ep + '.log')):
        try:
            record = json.loads(line[line.index('{'):])
        except (ValueError, TypeError):
            continue
        if record.get('episode') != ep:
            continue
        # The episode log also contains calibration samples from arbitrary film
        # positions. Only the current sequential production segment contributes.
        if (frames < total and record.get('source_start') == next_start
                and record.get('total') == selected
                and isinstance(record.get('processed'), int)
                and 0 <= record['processed'] <= selected):
            running = frames + record['processed']
            elapsed = record.get('elapsed', 0)
            if isinstance(elapsed, (int, float)) and elapsed > 0:
                result['fps'] = round(record['processed'] / elapsed, 2)
    result['running_frames'] = min(total, running)
    if result['fps'] and job.get('state') in ('ENHANCING', 'PROCESSING'):
        result['eta_seconds'] = max(0, (total - result['running_frames']) / result['fps'])
    return result


def collect(root):
    root = Path(root)
    post = root / 'post'
    ep = 'S03E02'
    progress = read(post / 'managed-S03E02/progress.json')
    timer = read(post / 'managed-S03E02/timer-status.json')
    job = read(post / 'jobs' / (ep + '.json'))
    review = read(root / 'ocr/subtitle-review/progress.json')
    sealed = []
    ass = []
    for i in range(2, 15):
        name = f'S03E{i:02}'
        ready = read(root / 'output' / (name + '.zh.srt.ready.json'))
        if ready:
            sealed.append(name)
        # Count actual style sidecars rather than merely the presence of .ass.
        style = list((root / 'output/ass').glob(name + '*.json'))
        if style and list((root / 'output/ass').glob(name + '*.ass')):
            ass.append(name)
    frames = segment_stats(post, ep, job)
    disks = []
    for name in ('incoming', 'cache', 'final'):
        path = post / name
        try:
            usage = shutil.disk_usage(path)
            disks.append(dict(name=name, free_gib=round(usage.free / 1024**3, 1)))
        except OSError:
            pass
    gpu = []
    try:
        command = ['nvidia-smi', '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                   '--format=csv,noheader,nounits']
        gpu = subprocess.check_output(command, text=True, timeout=5).strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        pass
    quota = timer.get('quota_remaining_percent')
    if quota is None:
        quota = progress.get('quota_remaining_percent')
    return dict(checked_at=datetime.now(timezone.utc).isoformat(),
                episode=ep, job=job, cloud_stage=progress.get('stage'),
                production_started=progress.get('production_started', False), frames=frames,
                review=dict(episode=review.get('episode'), stage=review.get('stage'),
                            text=len(review.get('text_reviewed_original_cue_ids', [])),
                            boundaries=len(review.get('boundary_reviewed_original_cue_ids', [])),
                            total=review.get('original_cue_count'), updated_at=review.get('updated_at'),
                            sealed=sealed, ass=ass),
                final_manifest=read(post / 'handoffs' / (ep + '.cloud-final.json')),
                quality=read(post / 'reports' / (ep + '.quality.json')),
                quota_remaining=quota, quota_checked_at=timer.get('checked_at'), disks=disks, gpu=gpu)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.root), ensure_ascii=False))
