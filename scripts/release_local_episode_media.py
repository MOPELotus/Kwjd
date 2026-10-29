#!/usr/bin/env python3
"""Release generated episode media after verified final return; retain evidence.

Only work/S01Exx, previews/Exx-* and background/Exx-background.mp4 are eligible.
Never touches original videos, projects, subtitles, fonts, final or ResolveWork.
"""
import argparse
from pathlib import Path

from episode_lifecycle import episode, load, now, save, sha256

MEDIA = {'.mp4', '.mkv', '.mov', '.ffv1', '.wav', '.flac'}


def candidates(root, ep):
    root = Path(root).resolve()
    n = int(ep.split('E')[1])
    folders = [root / 'work' / ep]
    folders += sorted((root / 'previews').glob(f'E{n:02d}-*'))
    selected = []
    for folder in folders:
        if not folder.exists():
            continue
        if folder.is_symlink() or folder.resolve() != folder.absolute():
            raise ValueError('Generated-media directory redirects outside its named path')
        for path in folder.rglob('*'):
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Linked generated-media entry; retained')
            if path.is_file() and path.suffix.lower() in MEDIA:
                selected.append(path)
    background = root / 'background' / f'E{n:02d}-background.mp4'
    if background.exists():
        selected.append(background)
    for path in selected:
        if path.resolve() != path.absolute() or not path.is_relative_to(root):
            raise ValueError('Selected path resolves outside the bounded workspace')
    return sorted(set(selected))


def release(root, ep, execute=False):
    root = Path(root).resolve()
    receipt = load(root / '.kwjd/receipts' / f'{ep}.json')
    if (receipt.get('episode') != ep or receipt.get('received') is not True
            or receipt.get('technical_checked') is not True):
        raise ValueError('Verified local final return is required')
    final = Path(receipt['local_path'])
    if final.parent.resolve() != (root / 'final').resolve() or sha256(final) != receipt['sha256']:
        raise ValueError('Preserved final does not match receipt')
    paths = candidates(root, ep)
    entries = [{'path': str(p.relative_to(root)), 'bytes': p.stat().st_size,
                'sha256': sha256(p)} for p in paths]
    report = {'episode': ep, 'final_sha256': receipt['sha256'], 'state': 'PLAN',
              'checked_at': now(), 'entries': entries, 'bytes': sum(x['bytes'] for x in entries),
              'scope': 'Explicit user release of generated non-final media; final and evidence retained'}
    audit = root / '.kwjd/media-release' / f'{ep}.{now().replace(":", "-")}.json'
    save(audit, report)
    if execute:
        allowed = set(candidates(root, ep))
        for entry, path in zip(entries, paths):
            if path not in allowed or path.resolve() != path.absolute() or sha256(path) != entry['sha256']:
                raise ValueError('Generated media changed; stop release')
            path.unlink()
        report.update(state='RELEASED', completed_at=now())
        save(audit, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--episode', type=episode, required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    report = release(args.root, args.episode, args.execute)
    print({k: v for k, v in report.items() if k != 'entries'})


if __name__ == '__main__':
    main()
