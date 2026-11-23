#!/usr/bin/env python3
"""Freeze current Resolve cache, then release it after a verified master handoff.

The frozen snapshot is captured when the user queues cleanup, never at completion.
The exported master remains available until the final-delivery lifecycle releases it.
"""
import argparse
from pathlib import Path, PurePosixPath
import re
import time

from episode_lifecycle import load, save, now, episode
from resolve_cache import CACHE, snapshot, cleanup


def prepare(root, ep, receipt, host, remote_root='/home/lotus/qingqingwu'):
    validation = load(root / '.kwjd/master-validation' / f'{ep}.json')
    if validation.get('state') != 'TECHNICALLY_VALIDATED':
        raise ValueError('A technically validated master is required')
    path = root / '.kwjd/cache-handoffs' / f'{ep}.json'
    if path.exists():
        raise ValueError('Existing cache request retained')
    if (not re.fullmatch(r'/[A-Za-z0-9_./-]+', remote_root)
            or '..' in PurePosixPath(remote_root).parts):
        raise ValueError('Expected the exact declared absolute remote receive root')
    prior = root / '.kwjd/cache-cleanup' / f'{ep}.local-anchor.json'
    released = (load(prior) if prior.exists() else {})
    precleaned = (released.get('state') == 'LOCAL_ANCHORED_CACHE_CLEANED'
                  and released.get('master_sha256') == validation['sha256'])
    if remote_root != '/home/lotus/qingqingwu' and not precleaned:
        raise ValueError('Alternate receive roots require matching already-released cache evidence')
    value = dict(episode=ep, state='WAIT_VERIFIED_HANDOFF', prepared_at=now(),
                 master_sha256=validation['sha256'], local_path=validation['path'],
                 bytes=validation['bytes'], receipt=str(receipt.resolve()), ssh_alias=host,
                 remote_root=remote_root, cache_precleaned=precleaned,
                 cache_path=str(CACHE), cache_snapshot=None if precleaned else snapshot(CACHE))
    save(path, value)
    return value


def admit(request, handoff):
    remote_root = request.get('remote_root', '/home/lotus/qingqingwu')
    if (handoff.get('episode') != request['episode']
            or handoff.get('sha256') != request['master_sha256']
            or handoff.get('bytes') != request['bytes']
            or handoff.get('transfer_verified') is not True
            or handoff.get('remote_sampled_decode_verified') is not True
            or handoff.get('path') != remote_root + '/post/incoming/'
               + request['episode'] + '.clean-master.mov'):
        raise ValueError('Master handoff identity/verification differs')
    return dict(episode=request['episode'], verified=True, sha256=request['master_sha256'],
                local_path=request['local_path'], local_master_deleted=False,
                remote_path=(('qingqingwu' if remote_root == '/home/lotus/qingqingwu' else remote_root)
                             + '/post/incoming/' + request['episode'] + '.clean-master.mov'),
                ssh_alias=request['ssh_alias'], cache_path=request['cache_path'],
                cache_snapshot=request['cache_snapshot'], handoff_receipt=request['receipt'],
                verified_at=now())


def run(root, ep):
    path = root / '.kwjd/cache-handoffs' / f'{ep}.json'
    request = load(path)
    if request['state'] == 'CACHE_CLEANED_MASTER_RETAINED':
        return
    receipt = Path(request['receipt'])
    while not receipt.exists():
        time.sleep(15)
    try:
        upload = admit(request, load(receipt))
        upload_path = root / '.kwjd/uploads' / f'{ep}.json'
        if upload_path.exists() and load(upload_path).get('sha256') != upload['sha256']:
            raise ValueError('Existing upload receipt differs')
        save(upload_path, upload)
        # cleanup rehashes both masters and checks the frozen cache twice;
        # it bounds deletion to CacheClip and rejects any new cache changes.
        result = cleanup(root, ep, execute=True)
        request.update(state='CACHE_CLEANED_MASTER_RETAINED', result=result, finished_at=now())
    except Exception as error:
        request.update(state='FAILED_RETAIN_CACHE', error=str(error), finished_at=now())
        save(path, request)
        raise
    save(path, request)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'run'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--episode', type=episode, required=True)
    p.add_argument('--receipt', type=Path)
    p.add_argument('--host')
    p.add_argument('--remote-root', default='/home/lotus/qingqingwu')
    a = p.parse_args()
    if a.command == 'prepare':
        if not a.receipt or not a.host:
            p.error('prepare requires --receipt and --host')
        value = prepare(a.root.resolve(), a.episode, a.receipt, a.host, a.remote_root)
        if value['cache_precleaned']:
            print('CACHE_ALREADY_RELEASED_NEW_CACHE_PROTECTED')
        else:
            print('FROZEN_CACHE_BYTES', sum(x['bytes'] for x in value['cache_snapshot']['entries']))
    else:
        run(a.root.resolve(), a.episode)


if __name__ == '__main__':
    main()
