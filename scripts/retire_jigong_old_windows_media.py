"""Retire the explicitly superseded Jigong CFR30 media, keeping current caches.

Only known old master/final hashes and pre-decision intro trial files are eligible.
Never traverses source videos, backgrounds, Resolve projects or CacheClip.
"""
import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from jigong_master_restart import OLD_JIGONG_MASTERS

EXTENSIONS = {'.mp4', '.mkv', '.mov', '.wav', '.flac', '.pcm', '.yuv', '.hevc', '.h265', '.ts'}
OLD_FINALS = {'d2061f72974362f543161760217517062c4a80ca6b8bfe1b86a9ed69cb83d4bf',
              '75fca3f68f09bb5048b86ab832fb856277fc4889248107af34f5c5611f21cf3d'}
CUTOFF = datetime.fromisoformat('2026-11-25T00:00:00+08:00').timestamp()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = args.root.absolute()
    if root.resolve() != root:
        raise ValueError('Root must not be a link')
    archive = root / '.kwjd/retired/jigong-cfr30-20261125'
    archive.mkdir(parents=True, exist_ok=True)
    report = archive / 'windows-media-retirement.json'
    if report.exists():
        raise ValueError('Retirement report exists; review it rather than repeat')
    targets = {}
    for ep, sha in OLD_JIGONG_MASTERS.items():
        p = root / 'masters' / (ep + '.clean-master.mov')
        if p.is_file():
            targets[p] = {sha}
    for folder in ('final', 'publish'):
        for p in (root / folder).glob('第一回*.mkv'):
            targets[p] = OLD_FINALS
    for folder in ['jigong-intro-final'] + ['jigong-intro-v' + str(n) for n in range(1, 7)]:
        for p in (root / 'previews' / folder).rglob('*'):
            if p.is_file() and p.suffix.lower() in EXTENSIONS and p.stat().st_mtime < CUTOFF:
                targets[p] = None
    pilot = root / '.kwjd/jigong-s03/ocr-pilot-30s.mp4'
    if pilot.is_file() and pilot.stat().st_mtime < CUTOFF:
        targets[pilot] = None
    items, retained = [], []
    for path, expected in targets.items():
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError('Unsafe generated-media path: ' + str(path))
        before = path.stat()
        sha = digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Media changed during retirement planning')
        item = dict(path=str(path), sha256=sha, bytes=after.st_size, mtime_ns=after.st_mtime_ns)
        (items if expected is None or sha in expected else retained).append(item)
    value = dict(state='PLANNED', authorization_date='2026-11-25',
                 basis='User explicitly retired old Jigong masters, finals and trial video; preserve originals/projects/subtitles/current cache',
                 files=items, retained_unknown_or_replacement=retained,
                 bytes=sum(x['bytes'] for x in items), archived_state_files=[])
    report.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.execute:
        for item in items:
            path = Path(item['path'])
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (item['bytes'], item['mtime_ns']):
                raise ValueError('Media changed after planning; retain it')
            path.unlink()
            item['deleted'] = True
            report.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        # Move obsolete active identities out of the replacement episode namespace.
        areas = ('master-validation', 'uploads', 'receipts', 'cache-local-approvals',
                 'master-duration-approvals', 'cache-cleanup', 'cache-handoffs',
                 'cache-snapshots', 'remote-reports', 'publishing')
        candidates = []
        for area in areas:
            candidates += [p for p in (root / '.kwjd' / area).glob('*')
                           if p.is_file() and re.match(r'S03E0[2-8](?:[.]|$)', p.name)]
        candidates += [p for p in (root / '.kwjd/jigong-s03').glob('S03E0*')
                       if p.is_file() and re.match(r'S03E0[2-8][.](?:master-upload|background-jobs)', p.name)]
        candidates += [p for p in (root / '.kwjd').glob('s03e0*-export-handoff.json')
                       if re.fullmatch(r's03e0[2-8]-export-handoff.json', p.name)]
        for path in candidates:
            if path.resolve() != path or path.stat().st_mtime >= CUTOFF:
                continue
            dest = archive / 'state' / path.relative_to(root / '.kwjd')
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise ValueError('Archive destination already exists')
            path.rename(dest)
            value['archived_state_files'].append(dict(source=str(path), archive=str(dest)))
        value['state'] = 'RETIRED'
        report.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in value.items() if k not in ('files', 'archived_state_files')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
