#!/usr/bin/env python3
"""Inventory the explicitly authorized E49 retirement; never delete anything.

New final, original input, SRT, fonts, environments, JSON/logs/PNG evidence and
all Windows CacheClip paths are excluded. Execute only after real Windows
download SHA and media QC evidence, not an invented quality approval.
"""
import argparse
import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil


GIB = 2**30


def inventory(root):
    root = root.resolve()
    post = root / 'post'
    work = root / 'post-home/experiments/face_rework_E49_20261028'
    ready = json.loads((post / 'reports/S01E49.face-rework.json').read_text())
    new = Path(ready['final_path']).resolve()
    new_inode = (new.stat().st_dev, new.stat().st_ino)
    directories = [
        post / 'cache/S01E49/S01E49.segments',
        post / 'cache/master-validation',
        post / 'reports/face_candidates_20261028',
        root / 'post-home/cache-relocated/master-validation',
        root / 'post-home/experiments/native_face_chain_20261028',
        work / 'smoke01', work / 'smoke02',
    ]
    candidates = {post / 'final/S01E49.final-1080p60.mkv'}
    # Only media inside these explicit generated-E49 directories. Keep the
    # native backend acceptance directory and its small proof clip untouched.
    for directory in directories:
        for path in directory.rglob('*'):
            if path.is_symlink():
                continue
            if path.is_file() and path.suffix.lower() in ('.mkv', '.mp4', '.mov', '.ffv1'):
                candidates.add(path)
    members = []
    inodes = collections.defaultdict(list)
    for path in sorted(candidates):
        if not path.exists():
            continue
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) == new_inode:
            raise ValueError('New final was accidentally selected; refuse inventory')
        if path.is_symlink() or not path.is_file():
            raise ValueError('Non-regular retirement candidate')
        # No linked parent below any selected directory may redirect inventory.
        boundary = next((d for d in directories if path.is_relative_to(d)), path.parent)
        current = path.parent
        while current != boundary:
            if current.is_symlink():
                raise ValueError('Inner directory symlink refused')
            current = current.parent
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
        if any(getattr(after, field) != getattr(stat, field) for field in
               ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')):
            raise ValueError('Candidate changed while inventorying')
        item = {'path': str(path), 'sha256': digest, 'bytes': stat.st_size,
                'device': stat.st_dev, 'inode': stat.st_ino, 'links': stat.st_nlink,
                'allocated_bytes': stat.st_blocks * 512}
        members.append(item)
        inodes[(stat.st_dev, stat.st_ino)].append(item)
    credits = collections.defaultdict(int)
    for entries in inodes.values():
        if len(entries) == entries[0]['links']:
            credits[entries[0]['device']] += entries[0]['allocated_bytes']
    home = root.stat().st_dev
    pending = json.loads((post / 'reports/S01E50.pending-handoff.json').read_text())
    seconds = pending['windows_report']['master_duration']
    incoming = pending['expected_bytes']
    video = int(seconds * 18_000_000 / 8)
    pcm = int(seconds * 48000 * 2 * 4)
    # Conservative same-volume phase bounds; existing canonical output is
    # source video for final mux, so both coexist. Do not add disjoint phases.
    phase_ai = 2 * video + 512 * 1024**2
    phase_mux = 2 * video + 2 * pcm + GIB
    free = shutil.disk_usage(root).free
    projected = free + credits[home]
    required = 25 * GIB + incoming + max(phase_ai, phase_mux)
    return {'at': datetime.now().astimezone().isoformat(), 'episode': 'S01E49',
            'operation': 'INVENTORY_ONLY', 'authorization_source': 'Human via Windows peer 2026-10-29: after dual-end new final SHA/media QC release old source and generated non-final episode artifacts; keep new server final, original program/project/SRT/fonts/compact evidence; E51 CacheClip forbidden',
            'execution_gate': 'Actual matching Windows new-file SHA/bytes and media QC proof required; quality=true is not required to retire old source under this new explicit authorization',
            'new_final_preserved': str(new), 'new_final_sha256': ready['sha256'],
            'members': members, 'files': len(members),
            'release_credit_by_device_bytes': dict(credits),
            'e50_projection': {'home_free_now_gib': free/GIB,
                'home_release_credit_gib': credits[home]/GIB,
                'projected_home_free_after_retirement_gib': projected/GIB,
                'incoming_gib': incoming/GIB, 'ai_phase_gib': phase_ai/GIB,
                'final_mux_phase_gib': phase_mux/GIB,
                'reserve_gib': 25, 'minimum_free_before_upload_and_production_gib': required/GIB,
                'projected_shortfall_gib': max(0, required-projected)/GIB,
                'production_budget_sufficient': projected >= required,
                'additional_cache_migration_bytes_not_yet_included': True,
                'scope': 'Reported master duration/PCM48k stereo; exact post-upload master/backend plan remains required'},
            'preserved': ['new full final', 'original input/program', 'projects',
                          'all SRT/seals', 'fonts/environments', 'JSON/logs/PNG evidence',
                          'native backend acceptance clip', 'all other episodes', 'all CacheClip'],
            'symlink_note': 'Native fragment compatibility symlink is not followed or deleted; retire explicitly only with its hash-bound target after gate.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'qingqingwu')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.root)
    with args.report.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'files': result['files'], 'budget': result['e50_projection'],
                      'report': str(args.report)}, ensure_ascii=False, indent=2))
