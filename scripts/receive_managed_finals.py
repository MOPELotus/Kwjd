#!/usr/bin/env python3
"""Resume final downloads while Windows is online; never assume quality approval.

Run at user login and keep polling the explicitly configured episode list. Originals,
server finals and existing different local finals are always retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from episode_lifecycle import load, save, now, catalog, record_final, sha256


def remote(config, arguments):
    import shlex
    git = Path('C:/Program Files/Git/usr/bin/ssh.exe')
    client = str(git) if git.exists() else 'ssh'
    return subprocess.run([client, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                           config['host'], shlex.join(arguments)], capture_output=True,
                          text=True, timeout=60)


def report_identity(report, ep, upload):
    digest = report.get('sha256', '')
    if (report.get('episode') != ep or not re.fullmatch('[a-f0-9]{64}', digest)
            or report.get('master_sha256') != upload.get('sha256')
            or upload.get('verified') is not True
            or report.get('final_decode_checked') is not True
            or report.get('final_pcm_equivalent') is not True
            or not re.fullmatch('[a-f0-9]{64}', report.get('decoded_pcm_sha256', ''))
            or not isinstance(report.get('bytes'), int) or report['bytes'] <= 0):
        raise ValueError('Final report needs exact master, full decode and mixed-PCM evidence')
    return digest


def receive(root, config, ep):
    receipt_path = root / '.kwjd/receipts' / f'{ep}.json'
    if receipt_path.exists():
        receipt = load(receipt_path)
        if receipt.get('received') and receipt.get('technical_checked'):
            return 'RECEIVED_WAIT_QUALITY' if not receipt.get('quality_checked') else 'ACCEPTED'
    upload_path = root / '.kwjd/uploads' / f'{ep}.json'
    if not upload_path.exists():
        return 'WAIT_MASTER_HANDOFF'
    base = config['remote_root']
    answer = remote(config, ['cat', base + '/post/reports/' + ep + '.json'])
    if answer.returncode:
        return 'WAIT_CLOUD_FINAL' if answer.returncode == 1 else 'OFFLINE_RETRY'
    report = json.loads(answer.stdout)
    digest = report_identity(report, ep, load(upload_path))
    remote_report = root / '.kwjd/remote-reports' / f'{ep}.json'
    save(remote_report, report)
    part = root / '.kwjd/downloads' / (digest + '.downloading')
    target = root / 'final' / catalog(root, ep)['final_name']
    if target.exists() and sha256(target) != digest:
        raise ValueError('Different existing local final retained')
    if not target.exists():
        remaining = report['bytes'] - (part.stat().st_size if part.exists() else 0)
        if shutil.disk_usage(root).free < 25 * 1024**3 + max(0, remaining):
            raise ValueError('Need download space plus 25GiB reserve')
        subprocess.run([sys.executable, str(Path(__file__).with_name('resume_sftp_download.py')),
                        '--ssh-alias', config['host'], '--remote',
                        base + '/post/final/' + ep + '.final-1080p60.mkv',
                        '--part', str(part), '--sha256', digest], check=True)
        if part.stat().st_size != report['bytes']:
            raise ValueError('Downloaded byte count differs')
        target.parent.mkdir(parents=True, exist_ok=True)
        part.replace(target)
    # Compare the complete decoded mix with the cloud's original-mix proof.
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-xerror', '-nostdin',
                                  '-i', str(target), '-map', '0:a:0', '-vn', '-c:a',
                                  'pcm_s32le', '-f', 'hash', '-hash', 'sha256', '-'], text=True)
    if pcm.strip().split('=')[-1].lower() != report['decoded_pcm_sha256']:
        raise ValueError('Returned mix differs from cloud lossless evidence')
    record_final(root, ep, target, remote_report)
    # The true quality review remains an independent gate. This receiver must
    # never set quality_checked or delete either final merely after downloading.
    return 'RECEIVED_WAIT_QUALITY'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--once', action='store_true')
    a = p.parse_args()
    config = load(a.config)
    if (not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', config['host'])
            or not re.fullmatch('/[A-Za-z0-9_/-]+', config['remote_root'])
            or not all(re.fullmatch(r'S0[1-4]E\d{2,3}', ep) for ep in config['episodes'])):
        raise ValueError('Unsafe receiver configuration')
    root = a.root.resolve()
    lock = (root / '.kwjd/managed-receiver.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
    while True:
        states = {}
        for ep in config['episodes']:
            try:
                states[ep] = receive(root, config, ep)
            except Exception as error:
                states[ep] = {'state': 'RETRY_RETAIN_FILES', 'error': str(error)}
        save(root / '.kwjd/managed-receiver.json', dict(checked_at=now(), episodes=states))
        if a.once:
            break
        time.sleep(60)


if __name__ == '__main__':
    main()
