#!/usr/bin/env python3
"""Receive explicitly listed audition videos and verify hashes and full decode.

Manifest: source_sha256, duration_seconds, fps, files[{name,remote_path,sha256}].
This writes only sample receipts; it never acknowledges a production final.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ssh-alias', required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.ssh_alias):
        p.error('Use an existing plain SSH alias')
    plan = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    names = set()
    for item in plan['files']:
        name, remote, sha = item['name'], item['remote_path'], item['sha256']
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.mkv', name) or name in names
                or not re.fullmatch(r'/[A-Za-z0-9_./-]+', remote)
                or '..' in remote.split('/') or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            p.error('Require unique ASCII MKV names, absolute shell-safe remote paths and SHA256')
        names.add(name)
    args.output.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output / 'sample-receipt.json'
    receipt = {'manifest_sha256': digest(args.manifest), 'source_sha256': plan['source_sha256'],
               'scope': __doc__, 'files': [], 'status': 'running'}
    save(receipt_path, receipt)
    try:
        for item in plan['files']:
            final = args.output / item['name']
            row = dict(item, status='receiving')
            receipt['files'].append(row)
            save(receipt_path, receipt)
            started = time.monotonic()
            if not final.exists():
                temporary = args.output / (item['sha256'] + '.downloading')
                # Only hash-bound temporary downloads may be retried. Completed
                # files are never overwritten, including same-name other content.
                subprocess.run(['scp', '-O', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
                                args.ssh_alias + ':' + item['remote_path'], str(temporary)], check=True)
            else:
                temporary = final
            if digest(temporary) != item['sha256']:
                raise RuntimeError(f'SHA mismatch; retained {temporary}')
            row['transfer_and_hash_wall_seconds'] = time.monotonic() - started
            probe = json.loads(subprocess.run(
                ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(temporary)],
                capture_output=True, text=True, encoding='utf-8', check=True).stdout)
            video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
            if (video['codec_name'] != 'hevc' or video.get('profile') != 'Main 10'
                    or (video['width'], video['height']) != (1920, 1080)
                    or video['r_frame_rate'] != f"{plan['fps']}/1"
                    or audio['codec_name'] != 'flac' or audio.get('bits_per_raw_sample') != '24'
                    or audio['sample_rate'] != '48000' or audio['channels'] != 2
                    or abs(float(probe['format']['duration']) - plan['duration_seconds']) > 0.15):
                raise RuntimeError(f'Sample media specification mismatch: {temporary}')
            with (args.output / (item['name'] + '.decode.log')).open('w', encoding='utf-8') as log:
                subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
                                '-threads', '4', '-i', str(temporary), '-map', '0:v:0',
                                '-map', '0:a:0', '-f', 'null', '-'], stdout=log, stderr=log, check=True)
            if temporary != final:
                temporary.rename(final)
            row.update(status='verified', path=str(final.resolve()), bytes=final.stat().st_size,
                       technical_checked=True, probe=probe)
            save(receipt_path, receipt)
            print(json.dumps({'name': item['name'], 'status': 'verified', 'path': str(final.resolve())}), flush=True)
        receipt['status'] = 'verified'
    except Exception as exc:
        receipt.update(status='failed', error=str(exc))
        raise
    finally:
        save(receipt_path, receipt)


if __name__ == '__main__':
    main()
