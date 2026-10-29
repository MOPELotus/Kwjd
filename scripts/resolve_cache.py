#!/usr/bin/env python3
"""User-authorized CacheClip cleanup after upload or explicit local anchoring.

The CLI cannot target any folder except D:/ResolveWork/CacheClip.
An upload-start snapshot protects caches changed by a newer Resolve task.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from episode_lifecycle import load, now, save, sha256

CACHE = Path('D:/ResolveWork/CacheClip')


def snapshot(folder):
    folder = Path(folder)
    if not folder.exists():
        return {'exists': False, 'entries': []}
    entries = []
    for path in [folder, *sorted(folder.rglob('*'))]:
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Cache links/junctions are retained, never followed for deletion')
        stat = path.stat()
        entries.append({'path': path.relative_to(folder).as_posix(),
                        'directory': path.is_dir(), 'bytes': stat.st_size if path.is_file() else 0,
                        'mtime_ns': stat.st_mtime_ns})
    return {'exists': True, 'entries': entries}


def check_snapshot(folder, baseline):
    if snapshot(folder) != baseline:
        raise ValueError('CacheClip changed since upload began; a new/active task may own it')


def cleanup(root, ep, execute=False):
    root = Path(root)
    receipt = load(root / '.kwjd/uploads' / f'{ep}.json')
    if receipt.get('episode') != ep or receipt.get('verified') is not True:
        raise ValueError('No verified master upload receipt')
    precleaned_path = root / '.kwjd/cache-cleanup' / f'{ep}.local-anchor.json'
    if precleaned_path.exists():
        previous = load(precleaned_path)
        if (previous.get('state') == 'LOCAL_ANCHORED_CACHE_CLEANED'
                and previous.get('master_sha256') == receipt.get('sha256')):
            return {**previous, 'state': 'CACHE_ALREADY_RELEASED_NEW_CACHE_RETAINED'}
    if receipt.get('cache_path') != str(CACHE):
        raise ValueError('Only the explicitly authorized CacheClip path is allowed')
    if receipt.get('sha256') != sha256(receipt['local_path']):
        raise ValueError('Local master changed since upload')
    remote = receipt['remote_path']
    import re
    if not re.fullmatch(r'qingqingwu/post/incoming/S\d{2}E\d{2,3}[.]clean-master[.](mov|mkv|mp4)', remote):
        raise ValueError('Unsafe remote master path')
    alias = receipt['ssh_alias']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', alias) or alias.startswith('-'):
        raise ValueError('Unsafe SSH alias')
    answer = subprocess.check_output(['ssh.exe', '-o', 'BatchMode=yes', alias,
                                      f"sha256sum '{remote}'"], text=True)
    if answer.split()[0].lower() != receipt['sha256']:
        raise ValueError('Remote master no longer matches uploaded master')
    if CACHE.resolve() != CACHE.absolute() or CACHE.parent.resolve() != Path('D:/ResolveWork').absolute():
        raise ValueError('CacheClip resolves outside the exact authorized directory')
    check_snapshot(CACHE, receipt['cache_snapshot'])
    report = {'episode': ep, 'cache_path': str(CACHE), 'master_sha256': receipt['sha256'],
              'state': 'CACHE_PLAN', 'checked_at': now(),
              'bytes': sum(v['bytes'] for v in receipt['cache_snapshot']['entries'])}
    if execute:
        time.sleep(2)
        check_snapshot(CACHE, receipt['cache_snapshot'])
        # Native filesystem operations only; the CacheClip folder itself is retained.
        if CACHE.exists():
            for item in list(CACHE.iterdir()):
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        report['state'] = 'CACHE_CLEANED'
        save(root / '.kwjd/cache-cleanup' / f'{ep}.json', report)
    return report


def delete_uploaded_master(root, ep):
    """Delete only the exact exported file whose remote copy was just rehashed."""
    import re
    root = Path(root)
    path = root / '.kwjd/uploads' / f'{ep}.json'
    receipt = load(path)
    if receipt.get('verified') is not True or receipt.get('episode') != ep:
        raise ValueError('A verified upload receipt is required')
    target = Path(receipt['local_path'])
    if (target.parent.resolve() != (root / 'masters').resolve()
            or not re.fullmatch(re.escape(ep) + r'[.]clean-master[.](mov|mkv|mp4)', target.name)
            or target.is_symlink()):
        raise ValueError('Only the exact generated episode master may be deleted')
    remote = receipt['remote_path']
    alias = receipt['ssh_alias']
    if remote != f'qingqingwu/post/incoming/{target.name}' or not re.fullmatch(r'[A-Za-z0-9_.-]+', alias) or alias.startswith('-'):
        raise ValueError('Unsafe remote verification target')
    answer = subprocess.check_output(['ssh.exe', '-o', 'BatchMode=yes', alias,
                                      f"sha256sum '{remote}'"], text=True)
    if answer.split()[0].lower() != receipt['sha256']:
        raise ValueError('Remote copy hash differs; local master retained')
    if target.exists():
        if sha256(target) != receipt['sha256']:
            raise ValueError('Local master changed; retained')
        target.unlink()
    elif not receipt.get('local_master_deleted'):
        raise ValueError('Local master is unexpectedly absent')
    receipt.update(local_master_deleted=True, local_master_deleted_at=now())
    save(path, receipt)
    return {'state': 'UPLOADED_LOCAL_MASTER_DELETED', 'episode': ep, 'sha256': receipt['sha256']}


def cleanup_local_anchor(root, ep, execute=False, expected_snapshot=None):
    """Release only CacheClip while retaining the verified, authorized local master."""
    import re
    root = Path(root).resolve()
    record = load(root / '.kwjd/cache-local-approvals' / f'{ep}.json')
    if record.get('episode') != ep or record.get('authorized') is not True:
        raise ValueError('Explicit local-cache cleanup authorization is required')
    review = load(root / '.kwjd' / f'{ep.lower().replace("s01", "")}-export-handoff.json')
    target = Path(review['path'])
    if (target.is_symlink() or target.parent.resolve() != (root / 'masters').resolve()
            or not re.fullmatch(re.escape(ep) + r'[.]clean-master[.](mov|mkv|mp4)', target.name)):
        raise ValueError('Expected the exact local episode master')
    expected = record.get('master_sha256')
    if review.get('sampled_av_decode_passed') is not True or review.get('sha256') != expected:
        raise ValueError('Missing matching master technical evidence')
    if not review.get('duration_gate_passed'):
        exception = review.get('duration_exception', {})
        if exception.get('approved') is not True or exception.get('master_sha256') != expected:
            raise ValueError('Duration difference has not been accepted for this master')
    before = target.stat()
    if sha256(target) != expected:
        raise ValueError('Local master changed; cache retained')
    prior_path = root / '.kwjd/cache-cleanup' / f'{ep}.local-anchor.json'
    if prior_path.exists():
        prior = load(prior_path)
        if prior.get('state') == 'LOCAL_ANCHORED_CACHE_CLEANED' and prior.get('master_sha256') == expected:
            return {**prior, 'state': 'CACHE_ALREADY_RELEASED_NEW_CACHE_RETAINED'}
    if CACHE.resolve() != CACHE.absolute() or CACHE.parent.resolve() != Path('D:/ResolveWork').absolute():
        raise ValueError('CacheClip resolves outside the exact authorized directory')
    baseline = snapshot(CACHE)
    if expected_snapshot is not None and baseline != expected_snapshot:
        raise ValueError('CacheClip changed since the queued cleanup request; retain newer cache')
    report = {'episode': ep, 'state': 'LOCAL_ANCHORED_CACHE_PLAN', 'checked_at': now(),
              'master_sha256': expected, 'master_retained': True, 'cache_path': str(CACHE),
              'bytes': sum(v['bytes'] for v in baseline['entries'])}
    if execute:
        save(root / '.kwjd/cache-snapshots' / f'{ep}.local-anchor.json', baseline)
        time.sleep(2)
        after = target.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Master changed during cleanup preparation')
        check_snapshot(CACHE, baseline)
        if CACHE.exists():
            for item in list(CACHE.iterdir()):
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        report['state'] = 'LOCAL_ANCHORED_CACHE_CLEANED'
        save(root / '.kwjd/cache-cleanup' / f'{ep}.local-anchor.json', report)
        save(root / '.kwjd/cache-cleanup' / f'{ep}.json',
             {**report, 'state': 'CACHE_CLEANED', 'basis': 'EXPLICIT_LOCAL_ANCHOR_AUTHORIZATION'})
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('snapshot', 'cleanup', 'cleanup-local', 'delete-uploaded-master'))
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--episode', required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    from episode_lifecycle import episode
    episode(args.episode)
    if args.command == 'snapshot':
        result = {'episode': args.episode, 'cache_path': str(CACHE),
                  'cache_snapshot': snapshot(CACHE), 'captured_at': now()}
        save(args.root / '.kwjd/cache-snapshots' / f'{args.episode}.json', result)
    elif args.command == 'delete-uploaded-master':
        if not args.execute:
            p.error('--execute is required to delete the verified uploaded master')
        result = delete_uploaded_master(args.root, args.episode)
    elif args.command == 'cleanup-local':
        result = cleanup_local_anchor(args.root, args.episode, args.execute)
    else:
        result = cleanup(args.root, args.episode, args.execute)
    print(json.dumps({k: v for k, v in result.items() if k != 'cache_snapshot'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
