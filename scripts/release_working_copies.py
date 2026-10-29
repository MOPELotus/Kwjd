#!/usr/bin/env python3
"""Retire explicit local trim/background copies after checking full originals.

Keeps original programs, long background, projects, masters/finals, manifests,
sealed subtitles and recovery receipts. Never follows links or scans to delete.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess

from episode_lifecycle import catalog, episode, load, now, save, sha256


def bounded_file(parent, name):
    named_parent = Path(parent).absolute()
    parent = named_parent.resolve(strict=True)
    if named_parent != parent:
        raise ValueError('Working-copy parent redirects through a link')
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('Expected an exact manifest filename, not a path')
    path = parent / name
    if path.is_symlink() or path.resolve() != path.absolute() or path.parent != parent:
        raise ValueError('Linked or out-of-bounds working copy retained')
    return path


def check_original(path, expected_duration=None):
    before = path.stat()
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                                              '-show_streams', '-of', 'json', str(path)]))
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid original duration')
    if expected_duration is not None and abs(duration - float(expected_duration)) > .5:
        raise ValueError('Full original differs from cut-manifest duration')
    types = {x['codec_type'] for x in info['streams']}
    if not {'video', 'audio'} <= types:
        raise ValueError('Original must retain video and audio')
    for start in [0, duration / 2, max(0, duration - 3)]:
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-ss', str(start),
                        '-i', str(path), '-t', '1', '-map', '0:v:0', '-map', '0:a:0',
                        '-f', 'null', '-'], check=True)
    digest = sha256(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Preserved original changed during validation')
    return {'path': str(path), 'sha256': digest, 'bytes': after.st_size,
            'mtime_ns': after.st_mtime_ns, 'duration_seconds': duration,
            'sampled_av_decode_passed': True}


def plan(root, original_root, episodes, backgrounds=False):
    root, original_root = Path(root).resolve(strict=True), Path(original_root).resolve(strict=True)
    entries, sources = [], {}
    split_path = root / 'background/background-split.json'
    split = load(split_path) if backgrounds else None
    for ep in episodes:
        manifest_path = root / 'manifests' / f'{ep}.json'
        manifest = load(manifest_path)
        if manifest['Episode'] != ep:
            raise ValueError('Manifest episode mismatch')
        # Preserve story naming even after the trimmed file is retired.
        catalog(root, ep)
        original = bounded_file(original_root, manifest['InputName'])
        sources[str(original)] = check_original(original, manifest['SourceDurationSeconds'])
        trimmed = bounded_file(root / 'videos', manifest['OutputName'])
        if trimmed.exists():
            if trimmed.samefile(original):
                raise ValueError('Working copy is the only original inode; retained')
            entries.append({'episode': ep, 'kind': 'trimmed_copy', 'path': str(trimmed),
                            'bytes': trimmed.stat().st_size, 'sha256': sha256(trimmed),
                            'original': str(original), 'manifest': manifest,
                            'manifest_sha256': sha256(manifest_path),
                            'rebuild': 'Existing cutter CreateCut: seek ChosenStartKeyframeSeconds+0.002; '
                                       'duration CopyTargetSeconds; map0/copy/avoid_negative_ts make_zero; '
                                       'verify first-frame hash and duration before DaVinci relink'})
        if backgrounds:
            target = bounded_file(root / 'background', f'E{int(ep.split("E")[1]):02d}-background.mp4')
            if target.exists():
                matches = [c for c in split['clips'] if c['episode'] == ep]
                if len(matches) != 1 or Path(matches[0]['output']).resolve() != target:
                    raise ValueError('Background recovery mapping differs')
                background = Path(split['source'])
                if background.parent.resolve() != (root / 'background').resolve():
                    raise ValueError('Unexpected long-background source')
                background = bounded_file(background.parent, background.name)
                if str(background) not in sources:
                    sources[str(background)] = check_original(background)
                if target.samefile(background):
                    raise ValueError('Cannot release original background')
                entries.append({'episode': ep, 'kind': 'background_slice', 'path': str(target),
                                'bytes': target.stat().st_size, 'sha256': sha256(target),
                                'original': str(background), 'split_mapping': matches[0],
                                'split_manifest_sha256': sha256(split_path)})
    return {'state': 'PLAN', 'checked_at': now(), 'episodes': episodes,
            'sources': sources, 'entries': entries, 'bytes': sum(x['bytes'] for x in entries),
            'note': 'User accepts recutting/relinking old DaVinci timelines from intact full originals'}


def execute(report, audit_path):
    # Verify every preserved original before any deletion, not merely its presence.
    for path, proof in report['sources'].items():
        if sha256(path) != proof['sha256']:
            raise ValueError('Preserved original changed; no working copies released')
    report['state'] = 'RELEASING'
    save(audit_path, report)
    try:
        for entry in report['entries']:
            path = Path(entry['path'])
            if path.is_symlink() or path.resolve() != path.absolute() or sha256(path) != entry['sha256']:
                raise ValueError('Working copy changed; retained')
            path.unlink()
            entry['deleted'] = True
            save(audit_path, report)
    except Exception as error:
        report.update(state='PARTIAL_RELEASE_RETAIN_REMAINDER', error=str(error))
        save(audit_path, report)
        raise
    report.update(state='RELEASED', completed_at=now())
    save(audit_path, report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--original-root', type=Path, required=True)
    p.add_argument('--episodes', type=episode, nargs='+', required=True)
    p.add_argument('--include-background-slices', action='store_true')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    report = plan(args.root, args.original_root, list(dict.fromkeys(args.episodes)), args.include_background_slices)
    audit = args.root / '.kwjd/working-copy-release' / (now().replace(':', '-') + '.json')
    save(audit, report)
    if args.execute:
        execute(report, audit)
    print(json.dumps({k: report[k] for k in ['state', 'episodes', 'bytes']}))
    print(audit)


if __name__ == '__main__':
    main()
