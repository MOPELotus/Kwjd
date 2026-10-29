#!/usr/bin/env python3
"""Move the declared top-level post/cache to home after E49 retirement.

Preserve logical paths, all compact evidence and both local storage declarations.
No media processing, Windows operation or policy/reserve change is performed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

from enhance_stage import atomic_json, check_space
from episode_lifecycle import artifact_path


def snapshot(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Inner cache symlinks forbidden')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError('Unknown nonpersistent cache endpoint')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        result[str(path.relative_to(directory))] = {'bytes': path.stat().st_size, 'sha256': digest}
    return result


def relocate_to_root(root):
    root=root.resolve();post=(root/'post').resolve();public=post/'cache'
    work=root/'post-home/experiments/face_rework_E49_20261028'
    report=work/'cache-migration-back-to-root.json'
    if not public.is_symlink():
        if report.exists():
            artifact_path(root,'S01E49','linux','cache/S01E49')
            return json.loads(report.read_text())
        raise ValueError('Expected declared cache link before root relocation')
    old=public.resolve();staging=post/'cache.root-staging-20261029';old_link=post/'cache.old-home-link-20261029'
    if staging.exists() or old_link.exists():raise ValueError('Interrupted migration retained for reconciliation')
    locks=[]
    try:
        for path in (work/'worker.lock',work.parents[1]/'.face-compute.lock',post/'jobs/.worker.lock'):
            handle=path.open('a+');locks.append(handle);fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=snapshot(old);check_space(post,sum(x['bytes'] for x in before.values())+256*1024**2,25)
        configs=[(post/'backend/storage.json','zones'),(root/'.kwjd/storage.json','areas')]
        originals=[(p,field,json.loads(p.read_text())) for p,field in configs]
        atomic_json(work/'cache-migration-back-to-root-intent.json',
                    {'old':str(old),'new':str(public),'files':before,
                     'previous_declarations':{str(p):data for p,_,data in originals}})
        shutil.copytree(old,staging)
        if snapshot(old)!=before or snapshot(staging)!=before:raise ValueError('Cache copy differs; preserve both')
        public.rename(old_link);staging.rename(public)
        for p,field,data in originals:
            data[field]['cache']=str(public);atomic_json(p,data)
        artifact_path(root,'S01E49','linux','cache/S01E49')
        if snapshot(old)!=before or snapshot(public)!=before:raise ValueError('Cache changed; old copy retained')
        for relative in before:(old/relative).unlink()
        for directory in sorted((p for p in old.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):directory.rmdir()
        old.rmdir();old_link.unlink()
        result=dict(status='CACHE_RELOCATED_TO_NEWLY_AVAILABLE_ROOT',logical_cache=str(root/'post/cache'),
                    physical_cache=str(public),files_verified=len(before),bytes=sum(x['bytes'] for x in before.values()),
                    both_storage_declarations_updated=True,lifecycle_path_guard_passed=True)
        atomic_json(report,result);return result
    finally:
        for handle in reversed(locks):handle.close()


def relocate(root):
    root = root.resolve()
    post = (root / 'post').resolve()
    work = root / 'post-home/experiments/face_rework_E49_20261028'
    source = post / 'cache'
    destination = root / 'post-home/cache'
    backup = post / 'cache.pre-home-20261029'
    report = work / 'cache-migration.json'
    if source.is_symlink():
        if source.resolve() != destination or not report.exists():
            raise ValueError('Unrecognized existing cache relocation')
        artifact_path(root, 'S01E49', 'linux', 'cache/S01E49')
        return json.loads(report.read_text())
    if not (work / 'generated-retirement-result.json').is_file():
        raise ValueError('E49 generated-media retirement has not completed')
    if destination.exists() or backup.exists():
        raise ValueError('Prior migration directories retained; reconcile before retry')
    locks = []
    try:
        for path in (work / 'worker.lock', work.parents[1] / '.face-compute.lock', post / 'jobs/.worker.lock'):
            handle = path.open('a+')
            locks.append(handle)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        listening = subprocess.check_output(['ss', '-xlH'], text=True)
        for relative in ('master-validation/preview84-mpv.sock',
                         'master-validation/preview84-playback2.sock',
                         'face-full-v2.sock', 'face-smoke-mpv.sock', 'face-full-mpv.sock'):
            endpoint = source / relative
            if endpoint.exists():
                if (not stat.S_ISSOCK(endpoint.lstat().st_mode) or endpoint.stat().st_uid != os.getuid()
                        or str(endpoint) in listening or str(root / 'post/cache' / relative) in listening):
                    raise ValueError('Preview endpoint may still be live; preserve it')
                atomic_json(work / ('obsolete-endpoint-' + endpoint.name + '.json'),
                            {'path': str(endpoint), 'reason': 'Owned stale Unix socket absent from listening sockets'})
                endpoint.unlink()
        before = snapshot(source)
        check_space(destination.parent, sum(x['bytes'] for x in before.values()) + 256 * 1024**2, 25)
        configs = [(post / 'backend/storage.json', 'zones'), (root / '.kwjd/storage.json', 'areas')]
        originals = [(path, field, json.loads(path.read_text())) for path, field in configs]
        atomic_json(work / 'cache-migration-intent.json',
                    {'source': str(source), 'destination': str(destination), 'files': before,
                     'previous_declarations': {str(path): data for path, _, data in originals}})
        shutil.copytree(source, destination)
        if snapshot(source) != before or snapshot(destination) != before:
            raise ValueError('Cache changed or copy differs; both copies preserved')
        source.rename(backup)
        source.symlink_to(destination, target_is_directory=True)
        for path, field, data in originals:
            data[field]['cache'] = str(destination)
            atomic_json(path, data)
        artifact_path(root, 'S01E49', 'linux', 'cache/S01E49')
        if snapshot(backup) != before or snapshot(destination) != before:
            raise ValueError('Post-relocation evidence differs; old copy preserved')
        # Remove only the exact verified source copies; unexpected new files
        # cannot be deleted by a wildcard or recursive blanket operation.
        for relative in before:
            (backup / relative).unlink()
        for directory in sorted((p for p in backup.rglob('*') if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            directory.rmdir()
        backup.rmdir()
        result = {'status': 'CACHE_RELOCATED_AND_OLD_COPIES_RELEASED',
                  'logical_cache': str(root / 'post/cache'), 'physical_cache': str(destination),
                  'files_verified': len(before), 'bytes': sum(x['bytes'] for x in before.values()),
                  'both_storage_declarations_updated': True,
                  'lifecycle_path_guard_passed': True, 'reserve_gib': 25}
        atomic_json(report, result)
        return result
    finally:
        for handle in reversed(locks):
            handle.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'qingqingwu')
    parser.add_argument('--to',choices=['home','root'],default='home')
    args = parser.parse_args()
    print(json.dumps((relocate_to_root if args.to=='root' else relocate)(args.root), indent=2))
