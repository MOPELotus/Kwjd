#!/usr/bin/env python3
"""Bounded independent SFTP ranges, preserving a prior contiguous download.

Uses an existing rclone installation, SSH identity and strict known_hosts.
No remote writes. The complete expected SHA is the only assembly publication gate.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from episode_lifecycle import load, now, save, sha256
from resume_sftp_download import stop_owned, validate, watch


@contextmanager
def exclusive(path):
    with path.open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def bounded(path, parent):
    if path.parent != parent or path.is_symlink() or path.absolute() != path.resolve():
        raise ValueError('Linked or redirected chunk path retained')
    return path


def ranges(prefix, total, block):
    if not 0 <= prefix <= total or block <= 0:
        raise ValueError('Invalid prefix/size/chunk length')
    return [{'offset': start, 'bytes': min(block, total-start)}
            for start in range(prefix, total, block)]


def ssh_path(value):
    value = value.strip('"')
    if re.match(r'^/[a-zA-Z]/', value):
        value = value[1] + ':' + value[2:]
    return Path(value).expanduser()


def connection(alias):
    git = Path('C:/Program Files/Git/usr/bin/ssh.exe')
    ssh = str(git) if git.is_file() else shutil.which('ssh')
    config = subprocess.check_output([ssh, '-G', alias], text=True, stderr=subprocess.DEVNULL)
    values = {}
    for line in config.splitlines():
        key, _, value = line.partition(' ')
        values.setdefault(key, []).append(value)
    keys = [ssh_path(x) for x in values.get('identityfile', [])]
    keys = [x for x in keys if x.is_file()]
    known = [ssh_path(x) for x in values.get('userknownhostsfile', [''])[0].split()]
    known = [x for x in known if x.is_file()]
    if not keys or not known or values.get('proxycommand', ['none'])[0] != 'none' or values.get('proxyjump', ['none'])[0] != 'none':
        raise ValueError('Range client needs a verified direct SSH identity and known_hosts')
    options = ['--sftp-host', values['hostname'][0], '--sftp-user', values['user'][0],
               '--sftp-port', values['port'][0], '--sftp-key-file', str(keys[0]),
               '--sftp-known-hosts-file', str(known[0]), '--sftp-disable-hashcheck',
               '--config', 'NUL' if os.name == 'nt' else os.devnull,
               '--timeout', '30s', '--contimeout', '15s',
               '--retries', '1', '--low-level-retries', '1']
    return ssh, options


def fetch_chunk(folder, item, remote, options, attempts=3):
    stem = f"{item['offset']:012d}-{item['bytes']}"
    target = bounded(folder / (stem+'.chunk'), folder)
    receipt = bounded(folder / (stem+'.json'), folder)
    partial = bounded(folder / (stem+'.partial'), folder)
    if receipt.exists() and target.exists():
        prior = load(receipt)
        if prior['offset'] == item['offset'] and prior['bytes'] == target.stat().st_size == item['bytes'] and sha256(target) == prior['sha256']:
            return prior
        raise ValueError('Completed chunk changed; retain evidence')
    if target.exists():
        raise ValueError('Unreceipted chunk retained for investigation')
    for attempt in range(1, attempts+1):
        have = partial.stat().st_size if partial.exists() else 0
        if have > item['bytes']:
            raise ValueError('Oversized partial chunk retained')
        if have < item['bytes']:
            command = ['rclone', 'cat', ':sftp:'+remote, '--offset', str(item['offset']+have),
                       '--count', str(item['bytes']-have)] + options
            with partial.open('ab') as output, (folder/(stem+f'.{attempt}.log')).open('ab') as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                try:
                    outcome = watch(process, partial, 180)
                finally:
                    stop_owned(process)
            if outcome != 'EXITED_OK':
                continue
        if partial.stat().st_size != item['bytes']:
            continue
        proof = dict(item, sha256=sha256(partial), path=str(target), checked_at=now())
        os.replace(partial, target)
        save(receipt, proof)
        return proof
    raise RuntimeError(f"Chunk {item['offset']} retry budget exhausted; partial preserved")


def assemble(part, prefix, proofs, digest, size):
    before = (part.stat().st_size, part.stat().st_mtime_ns) if part.exists() else None
    if (before[0] if before else 0) != prefix:
        raise ValueError('Contiguous prefix changed')
    assembly = bounded(part.with_suffix('.assembling'), part.parent)
    full = hashlib.sha256()
    with assembly.open('wb') as output:
        def append(source, expected_length):
            count = 0
            with source.open('rb') as stream:
                for block in iter(lambda: stream.read(4*1024*1024), b''):
                    output.write(block)
                    full.update(block)
                    count += len(block)
            if count != expected_length:
                raise ValueError('Assembly source length changed')
        if prefix:
            append(part, prefix)
        cursor = prefix
        for proof in sorted(proofs, key=lambda x: x['offset']):
            if proof['offset'] != cursor:
                raise ValueError('Gap or overlap in chunk ordering')
            append(Path(proof['path']), proof['bytes'])
            cursor += proof['bytes']
    if cursor != size or full.hexdigest() != digest:
        raise ValueError('Whole SHA/size mismatch; original prefix and chunks retained')
    after = (part.stat().st_size, part.stat().st_mtime_ns) if part.exists() else None
    if before != after:
        raise ValueError('Original prefix changed during assembly')
    os.replace(assembly, part)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-alias', required=True)
    parser.add_argument('--remote', required=True)
    parser.add_argument('--part', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--chunk-mib', type=int, default=64)
    args = parser.parse_args()
    part = args.part.absolute()
    validate(args.ssh_alias, args.remote, part, args.sha256)
    if not 1 <= args.workers <= 4 or not 1 <= args.chunk_mib <= 128:
        parser.error('Use1..4 connections and1..128MiB chunks')
    if not shutil.which('rclone'):
        raise ValueError('Existing rclone required; no installation attempted')
    part.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(bounded(part.with_suffix('.chunk.lock'), part.parent)):
        ssh, options = connection(args.ssh_alias)
        total = int(subprocess.check_output([ssh, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                            args.ssh_alias, f"stat -c %s -- '{args.remote}'"], text=True))
        folder = bounded(part.with_suffix('.chunks'), part.parent)
        folder.mkdir(exist_ok=True)
        state_path = bounded(folder/'manifest.json', folder)
        if state_path.exists():
            state = load(state_path)
            if state['sha256'] != args.sha256 or state['remote'] != args.remote or state['size'] != total:
                raise ValueError('Source binding changed; cache retained')
            if state['state'] == 'SHA_VERIFIED' and part.exists() and sha256(part) == args.sha256:
                return
            prefix = state['prefix_bytes']
            if (part.stat().st_size if part.exists() else 0) != prefix or (sha256(part) if part.exists() else None) != state['prefix_sha256']:
                raise ValueError('Saved contiguous prefix changed')
            items = state['ranges']
        else:
            prefix = part.stat().st_size if part.exists() else 0
            items = ranges(prefix, total, args.chunk_mib*2**20)
            state = dict(state='DOWNLOADING', at=now(), sha256=args.sha256, remote=args.remote,
                         size=total, prefix_bytes=prefix, prefix_sha256=sha256(part) if part.exists() else None,
                         ranges=items, chunks=[], workers=args.workers, pid=os.getpid())
            save(state_path, state)
        start = time.monotonic()
        state['pid'] = os.getpid()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks = [pool.submit(fetch_chunk, folder, item, args.remote, options) for item in items]
            proofs = []
            try:
                for task in as_completed(tasks):
                    proofs.append(task.result())
                    state.update(state='DOWNLOADING', at=now(), chunks=proofs,
                                 completed_bytes=prefix+sum(x['bytes'] for x in proofs))
                    save(state_path, state)
            except Exception:
                for task in tasks:
                    task.cancel()
                state.update(state='RETRY_REQUIRED', at=now())
                save(state_path, state)
                raise
        state.update(state='ASSEMBLING', at=now())
        save(state_path, state)
        assemble(part, prefix, proofs, args.sha256, total)
        state.update(state='SHA_VERIFIED', at=now(), elapsed_seconds=time.monotonic()-start,
                     completed_bytes=total)
        save(state_path, state)
        # Full hash-verified contiguous file supersedes these precise chunk copies.
        for proof in proofs:
            bounded(Path(proof['path']), folder).unlink()
        print(f'SHA_VERIFIED {total} bytes in {state["elapsed_seconds"]:.2f}s')


if __name__ == '__main__':
    main()
