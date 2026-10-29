#!/usr/bin/env python3
"""One-shot chain: verified previous upload/release, then anchored next cache cleanup.

Freeze the cache at prepare time; a newer Mask invalidates the pending cleanup.
No scheduled monitor, upload restart, or media/project modification is performed.
"""
import argparse
import os
from pathlib import Path
import subprocess

from episode_lifecycle import episode, load, now, save, sha256
from resolve_cache import CACHE, snapshot, cleanup_local_anchor


def previous_released(root, previous, expected):
    upload = load(root / '.kwjd/uploads' / f'{previous}.json')
    handoff = load(root / '.kwjd/handoffs' / f'{previous}.upload.json')
    for item in (upload, handoff):
        if (item.get('episode') != previous or item.get('verified') is not True
                or item.get('local_master_deleted') is not True or item.get('sha256') != expected):
            raise ValueError('Previous upload and local-release receipts are incomplete')
    if Path(upload['local_path']).exists():
        raise ValueError('Previous master remains local; wait for its verified release')


def prepare(root, ep, previous):
    review = load(root / '.kwjd' / f'{ep.lower().replace("s01", "")}-export-handoff.json')
    if review.get('sampled_av_decode_passed') is not True or sha256(review['path']) != review['sha256']:
        raise ValueError('Next local master needs fresh matching technical evidence')
    approval = load(root / '.kwjd/cache-local-approvals' / f'{ep}.json')
    if approval.get('authorized') is not True or approval.get('master_sha256') != review['sha256']:
        raise ValueError('Explicit matching next-cache authorization is required')
    transfer = load(root / '.kwjd' / f'{previous.lower().replace("s01", "")}-master-transfer.json')
    if CACHE.resolve() != CACHE.absolute() or CACHE.parent.resolve() != Path('D:/ResolveWork').absolute():
        raise ValueError('Unexpected CacheClip path')
    frozen = snapshot(CACHE)
    snapshot_path = root / '.kwjd/cache-chains' / f'{ep}.snapshot.json'
    chain_path = root / '.kwjd/cache-chains' / f'{ep}.json'
    if chain_path.exists():
        raise ValueError('Existing queued action retained; inspect before replacing')
    save(snapshot_path, frozen)
    value = {'episode': ep, 'previous': previous, 'state': 'WAIT_PREVIOUS_UPLOAD_RELEASE',
             'prepared_at': now(), 'previous_upload_pid': int(transfer['pid']),
             'previous_master_sha256': transfer['sha256'], 'master_sha256': review['sha256'],
             'snapshot_path': str(snapshot_path), 'snapshot_sha256': sha256(snapshot_path),
             'cache_bytes': sum(x['bytes'] for x in frozen['entries'])}
    save(chain_path, value)
    return value


def run(root, ep):
    if os.name != 'nt':
        raise ValueError('This one-shot task must run on Windows')
    chain_path = root / '.kwjd/cache-chains' / f'{ep}.json'
    value = load(chain_path)
    if value['state'] == 'CACHE_RELEASED_MASTER_RETAINED':
        return value
    try:
        pid = int(value['previous_upload_pid'])
        if pid <= 0:
            raise ValueError('Invalid upload process')
        print('Waiting for previous upload/release process', pid, flush=True)
        subprocess.run(['pwsh', '-NoProfile', '-Command',
                        f'Wait-Process -Id {pid} -ErrorAction SilentlyContinue'], check=False)
        previous_released(root, value['previous'], value['previous_master_sha256'])
        if sha256(value['snapshot_path']) != value['snapshot_sha256']:
            raise ValueError('Queued cache snapshot changed')
        value.update(state='VERIFYING_NEXT_MASTER_AND_CACHE', checked_at=now())
        save(chain_path, value)
        result = cleanup_local_anchor(root, ep, execute=True,
                                      expected_snapshot=load(value['snapshot_path']))
        value.update(state='CACHE_RELEASED_MASTER_RETAINED', result=result, completed_at=now())
        save(chain_path, value)
        review_path = root / '.kwjd' / f'{ep.lower().replace("s01", "")}-export-handoff.json'
        review = load(review_path)
        review.update(cache_cleanup_executed=True, cache_preserved=False,
                      cache_cleanup_basis='USER_AUTHORIZED_LOCAL_ANCHOR_AFTER_PREVIOUS_UPLOAD')
        save(review_path, review)
    except Exception as error:
        value.update(state='FAILED_CACHE_RETAINED_OR_PARTIAL_CHECK_LOG', error=str(error), failed_at=now())
        save(chain_path, value)
        raise
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('prepare', 'run'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--episode', type=episode, required=True)
    p.add_argument('--previous', type=episode)
    args = p.parse_args()
    root = args.root.resolve()
    if args.command == 'prepare' and not args.previous:
        p.error('--previous is required to prepare')
    result = prepare(root, args.episode, args.previous) if args.command == 'prepare' else run(root, args.episode)
    print(result, flush=True)


if __name__ == '__main__':
    main()
