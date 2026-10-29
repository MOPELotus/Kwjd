#!/usr/bin/env python3
"""Verify a separately downloaded rework, then explicitly publish its local name.

Never downloads, cleans caches, sets quality_checked, or deletes server files.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from episode_lifecycle import catalog, check_final_media, load, now, save, sha256


def verify(root, state, report_path):
    root = Path(root).resolve()
    report = load(report_path)
    raw_partial = Path(state['partial'])
    partial = raw_partial.resolve(strict=True)
    downloads = (root / '.kwjd/downloads').resolve()
    if partial.parent != downloads or raw_partial.is_symlink():
        raise ValueError('Expected an independent download staging file')
    expected = state['expected_sha256']
    if (report.get('episode') != state['episode'] or report.get('sha256') != expected
            or report.get('source_sha256') != state['source_sha256']
            or report.get('final_decode_checked') is not True or report.get('final_pcm_equivalent') is not True):
        raise ValueError('Rework source/technical report binding differs')
    if partial.stat().st_size != report['bytes'] or sha256(partial) != expected:
        raise ValueError('Incomplete download or SHA mismatch')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                              '-show_format', '-of', 'json', str(partial)]))
    check_final_media(info)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    if (video.get('profile') != 'Main 10' or video.get('color_space') != 'bt709'
            or video.get('color_range') != 'tv'):
        raise ValueError('Expected Main10 Rec709 limited')
    duration = float(info['format']['duration'])
    if abs(duration - report['output_duration']) > .02:
        raise ValueError('Rework duration differs')
    samples = [0, duration / 2, max(0, duration - 4)]
    for start in samples:
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-ss', str(start),
                        '-i', str(partial), '-t', '3', '-map', '0:v:0', '-map', '0:a:0',
                        '-f', 'null', '-'], check=True)
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-xerror', '-nostdin',
                                  '-i', str(partial), '-map', '0:a:0', '-vn',
                                  '-c:a', 'pcm_s32le', '-f', 'hash', '-hash', 'sha256', '-'], text=True)
    if pcm.strip().split('=')[-1] != report['decoded_pcm_sha256']:
        raise ValueError('Decoded mixed audio differs from server evidence')
    return {'episode': state['episode'], 'sha256': expected, 'bytes': partial.stat().st_size,
            'technical_checked': True, 'quality_checked': False, 'checked_at': now(),
            'duration_seconds': duration, 'decode_samples': samples,
            'decoded_pcm_sha256': report['decoded_pcm_sha256'],
            'ready_report_sha256': sha256(report_path),
            'master_sha256': report['master_sha256'], 'source_sha256': report['source_sha256'],
            'remote_final_name': Path(report['final_path']).name,
            'limitations': report.get('limitations', [])}


def publish(root, state, evidence):
    root = Path(root).resolve()
    ep = state['episode']
    target = root / 'final' / catalog(root, ep)['final_name']
    if target.resolve() != Path(state['target']).resolve() or target.is_symlink():
        raise ValueError('Unexpected local final target')
    if sha256(state['partial']) != evidence['sha256']:
        raise ValueError('Staging file changed after checks')
    if target.exists() and sha256(target) != state['source_sha256']:
        raise ValueError('Existing local final is not the explicitly rejected source')
    receipt = root / '.kwjd/receipts' / f'{ep}.json'
    if receipt.exists():
        previous = load(receipt)
        if previous.get('sha256') != state['source_sha256']:
            raise ValueError('Existing receipt belongs to another revision')
        history = root / '.kwjd/receipts/history' / f'{ep}.{previous["sha256"]}.json'
        if history.exists() and load(history) != previous:
            raise ValueError('Historical receipt differs; retain both revisions')
        save(history, previous)
    os.replace(state['partial'], target)
    record = dict(evidence, schema_version=1, received=True, received_at=now(),
                  local_path=str(target), variant='face-reworked')
    save(receipt, record)
    save(root / '.kwjd/receipts' / f'{ep}.face-reworked.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    state = load(args.state)
    evidence = verify(args.root, state, args.report)
    save(args.root / '.kwjd/final-review' / state['episode'] / 'rework-local-technical.json', evidence)
    if args.publish:
        evidence = publish(args.root, state, evidence)
        state.update(state='RECEIVED_NEW_REVISION_TECHNICALLY_VERIFIED',
                     quality_checked=False, completed_at=now())
        save(args.state, state)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
