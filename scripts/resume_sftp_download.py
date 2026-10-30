#!/usr/bin/env python3
"""Resume a hash-named download, recovering only its own stalled SFTP tree."""
import argparse
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from episode_lifecycle import save, now, sha256


def stop_owned(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def watch(process, target, stall_seconds, poll_seconds=2):
    size = target.stat().st_size if target.exists() else 0
    changed = time.monotonic()
    while process.poll() is None:
        current = target.stat().st_size if target.exists() else 0
        if current != size:
            size, changed = current, time.monotonic()
        if time.monotonic() - changed >= stall_seconds:
            stop_owned(process)
            return 'STALLED'
        time.sleep(poll_seconds)
    return 'EXITED_OK' if process.returncode == 0 else 'EXITED_FAILED'


def validate(alias, remote, target, digest):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', alias):
        raise ValueError('Expected a configured SSH alias')
    if not re.fullmatch(r'[A-Za-z0-9_./-]+', remote) or '..' in Path(remote).parts:
        raise ValueError('Unsafe remote path')
    if not re.fullmatch(r'[0-9a-f]{64}', digest) or target.name != digest + '.downloading':
        raise ValueError('Only a SHA-named download staging file is permitted')
    if any(c in str(target) for c in '\r\n"') or target.is_symlink():
        raise ValueError('Unsafe staging path')
    if target.absolute() != target.resolve():
        raise ValueError('Linked staging parent retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-alias', required=True)
    parser.add_argument('--remote', required=True)
    parser.add_argument('--part', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--stall-seconds', type=float, default=180)
    parser.add_argument('--attempts', type=int, default=4)
    args = parser.parse_args()
    target = args.part.absolute()
    validate(args.ssh_alias, args.remote, target, args.sha256)
    if not math.isfinite(args.stall_seconds) or args.stall_seconds <= 0 or not 1 <= args.attempts <= 10:
        parser.error('Positive finite wait and 1..10 attempts required')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Git for Windows supplies an independent OpenSSH client. Its real 8MiB
    # full/resume probe passed when the Windows system client stalled.
    git_bin = Path('C:/Program Files/Git/usr/bin')
    client = git_bin / 'sftp.exe'
    use_git = os.name == 'nt' and client.is_file() and (git_bin / 'ssh.exe').is_file()
    base = [str(client), '-S', str(git_bin / 'ssh.exe')] if use_git else [shutil.which('sftp') or 'sftp']
    receipt = target.with_suffix('.transfer.json')
    for attempt in range(1, args.attempts + 1):
        validate(args.ssh_alias, args.remote, target, args.sha256)
        batch = target.with_name(target.name + f'.{os.getpid()}.{attempt}.sftp')
        log = target.with_name(target.name + f'.{os.getpid()}.{attempt}.log')
        batch.write_text(f'reget "{args.remote}" "{target.as_posix()}"\n', encoding='utf-8')
        command = base + ['-b', str(batch), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                          '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                          '-o', 'IPQoS=none', '-o', 'Compression=no', args.ssh_alias]
        state = dict(at=now(), state='RUNNING', attempt=attempt, target=str(target),
                     sha256=args.sha256, client=base, log=str(log))
        with log.open('ab') as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            state['pid'] = process.pid
            save(receipt, state)
            try:
                outcome = watch(process, target, args.stall_seconds)
            finally:
                stop_owned(process)
                batch.unlink(missing_ok=True)
        state.update(at=now(), state=outcome, bytes=target.stat().st_size if target.exists() else 0)
        save(receipt, state)
        if outcome == 'EXITED_OK':
            if sha256(target) != args.sha256:
                raise ValueError('Full SHA mismatch; retain partial and logs for investigation')
            state.update(state='SHA_VERIFIED', at=now())
            save(receipt, state)
            return
    raise RuntimeError('Download retry budget exhausted; partial and attempt logs retained')


if __name__ == '__main__':
    main()
