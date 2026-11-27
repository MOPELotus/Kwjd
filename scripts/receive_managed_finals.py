#!/usr/bin/env python3
"""Resume final downloads while Windows is online; never assume quality approval.

Run at user login and keep polling the explicitly configured episode list. Originals,
server finals and existing different local finals are always retained.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from episode_lifecycle import (load, save, now, catalog, record_final, sha256,
                               cleanup, register_artifact, check_final_media)

BACKGROUND_PROCESS = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def remote(config, arguments, input_text=None):
    import shlex
    git = Path('C:/Program Files/Git/usr/bin/ssh.exe')
    client = str(git) if git.exists() else 'ssh'
    return subprocess.run([client, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                           config['host'], shlex.join(arguments)], capture_output=True,
                          text=True, encoding='utf-8', timeout=60, input=input_text,
                          **BACKGROUND_PROCESS)


def quality_identity(quality, receipt):
    from jigong_quality import valid_reviewer
    if (quality.get('schema_version') != 1
            or quality.get('episode') != receipt['episode']
            or quality.get('master_sha256') != receipt['master_sha256']
            or quality.get('final_sha256') != receipt['sha256']
            or quality.get('final_bytes') != receipt['bytes']
            or quality.get('review_complete') is not True
            or not valid_reviewer(quality.get('reviewed_by', {}), receipt['episode'])
            or not quality.get('reviewed_by', {}).get('session_id')):
        raise ValueError('Actual authorized quality review is not bound to this final')
    evidence = []
    for name in ('visual', 'subtitles', 'synchronization', 'audio'):
        section = quality.get(name, {})
        if section.get('checked') is not True or section.get('issues') or not section.get('evidence'):
            raise ValueError('Incomplete actual quality review: ' + name)
        evidence.extend(section['evidence'])
    if (quality['audio'].get('source_pcm_equal') is not True
            or not re.fullmatch('[a-f0-9]{64}', quality['audio'].get('decoded_pcm_sha256', ''))):
        raise ValueError('Missing reviewed original-mix equality')
    for name in ('srt_sha256', 'ass_sha256'):
        if not re.fullmatch('[a-f0-9]{64}', quality['subtitles'].get(name, '')):
            raise ValueError('Missing reviewed subtitle identity')
    return evidence


def publish_receipt(config, ep, value, suffix=''):
    code = ("import pathlib,json,sys,os; r=pathlib.Path(sys.argv[1]); ep=sys.argv[2]; "
            "d=json.load(sys.stdin); p=r/'post/receipts'/(ep+sys.argv[3]+'.json'); "
            "p.parent.mkdir(parents=True,exist_ok=True); "
            "old=json.loads(p.read_text()) if p.exists() else d; "
            "assert old.get('sha256',old.get('final_sha256'))==d.get('sha256',d.get('final_sha256')); "
            "t=p.with_suffix('.json.receiving'); t.write_text(json.dumps(d)); os.replace(t,p)")
    sent = remote(config, ['python3', '-c', code, config['remote_root'], ep, suffix], json.dumps(value))
    if sent.returncode:
        raise RuntimeError('Windows receipt upload pending: ' + sent.stderr)


def finish_delivery(root, config, ep, receipt):
    from pathlib import PurePosixPath
    if receipt.get('windows_cleanup_published'):
        final = Path(receipt['local_path'])
        if not final.exists() or final.stat().st_size != receipt['bytes']:
            raise ValueError('Previously accepted local final is absent or changed')
        return 'ACCEPTED_LOCAL_GENERATED_MEDIA_CLEANED'
    answer = remote(config, ['cat', config['remote_root'] + '/post/reports/' + ep + '.quality.json'])
    if answer.returncode:
        return 'RECEIVED_WAIT_QUALITY'
    quality = json.loads(answer.stdout)
    evidence = quality_identity(quality, receipt)
    report = load(root / '.kwjd/remote-reports' / f'{ep}.json')
    if quality['audio']['decoded_pcm_sha256'] != report['decoded_pcm_sha256']:
        raise ValueError('Quality evidence and received mix differ')
    boundary = PurePosixPath(config['remote_root']) / 'post'
    for item in evidence:
        path = PurePosixPath(item['path'])
        if ('..' in path.parts or not path.is_relative_to(boundary)
                or not re.fullmatch('[a-f0-9]{64}', item.get('sha256', ''))):
            raise ValueError('Invalid quality evidence path/hash')
        check = remote(config, ['sha256sum', str(path)])
        if check.returncode or check.stdout.split()[0] != item['sha256']:
            raise ValueError('Actual quality evidence is missing or changed')
    # Reuse actual cloud Codex review only for the identical local final.
    # Never infer visual/audio acceptance from media metadata alone.
    if sha256(receipt['local_path']) != receipt['sha256']:
        raise ValueError('Local final changed before quality acceptance')
    quality_path = root / '.kwjd/remote-reports' / f'{ep}.quality.json'
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve server bytes: reformatting or Windows CRLF conversion would break
    # the quality-evidence SHA that the cloud validates on the return receipt.
    quality_path.write_bytes(answer.stdout.encode('utf-8'))
    receipt.update(quality_checked=True, quality_checked_at=now(),
                   quality_note='Actual cloud Codex picture/subtitle/sync/audio review; identical local final SHA verified',
                   quality_evidence_sha256=sha256(quality_path), quality_evidence=quality)
    save(root / '.kwjd/receipts' / f'{ep}.json', receipt)
    # Atomic receipt publication only; the cloud owns its guarded cleanup.
    publish_receipt(config, ep, receipt)
    upload = load(root / '.kwjd/uploads' / f'{ep}.json')
    master = Path(upload['local_path'])
    if master.exists():
        if sha256(master) != receipt['master_sha256']:
            raise ValueError('Changed local master retained')
        register_artifact(root, ep, 'windows', master.relative_to(root).as_posix())
    cleaned = cleanup(root, ep, 'windows', root / '.kwjd/receipts' / f'{ep}.json', execute=True)
    publish_receipt(config, ep, cleaned, '.windows-cleanup')
    receipt.update(windows_cleanup_published=True, windows_cleanup_published_at=now())
    save(root / '.kwjd/receipts' / f'{ep}.json', receipt)
    return 'ACCEPTED_LOCAL_GENERATED_MEDIA_CLEANED'


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


def published_quality(config, ep, report):
    """Require actual cloud QC publication before starting any transfer."""
    base = config['remote_root']
    answer = remote(config, ['python3', base + '/repo/scripts/jigong_quality.py',
                             '--root', base, '--episode', ep])
    if answer.returncode:
        return False
    quality = json.loads(answer.stdout)
    identity = dict(episode=ep, master_sha256=report['master_sha256'],
                    sha256=report['sha256'], bytes=report['bytes'])
    quality_identity(quality, identity)
    answer = remote(config, ['cat', base + '/post/handoffs/' + ep + '.cloud-final.json'])
    if answer.returncode:
        return False
    published = json.loads(answer.stdout)
    if (published.get('server_quality_checked') is not True
            or published.get('episode') != ep
            or published.get('master_sha256') != identity['master_sha256']
            or published.get('sha256') != identity['sha256']
            or published.get('bytes') != identity['bytes']
            or published.get('quality_evidence_sha256') != quality.get('quality_sha256')):
        raise ValueError('Published actual QC differs from download identity')
    return True


def receive(root, config, ep):
    from jigong_master_restart import enforce
    receipt_path = root / '.kwjd/receipts' / f'{ep}.json'
    if receipt_path.exists():
        receipt = load(receipt_path)
        enforce(ep, receipt.get('master_sha256', ''))
        if receipt.get('received') and receipt.get('technical_checked'):
            return finish_delivery(root, config, ep, receipt)
    upload_path = root / '.kwjd/uploads' / f'{ep}.json'
    if not upload_path.exists():
        return 'WAIT_MASTER_HANDOFF'
    enforce(ep, load(upload_path).get('sha256', ''))
    base = config['remote_root']
    answer = remote(config, ['cat', base + '/post/reports/' + ep + '.json'])
    if answer.returncode:
        return 'WAIT_CLOUD_FINAL' if answer.returncode == 1 else 'OFFLINE_RETRY'
    report = json.loads(answer.stdout)
    digest = report_identity(report, ep, load(upload_path))
    if not published_quality(config, ep, report):
        return 'WAIT_ACTUAL_QC_PUBLICATION'
    remote_report = root / '.kwjd/remote-reports' / f'{ep}.json'
    save(remote_report, report)
    part = root / '.kwjd/downloads' / (digest + '.downloading')
    target = root / 'final' / catalog(root, ep)['final_name']
    if target.exists() and sha256(target) != digest:
        raise ValueError('Different existing local final retained')
    if not target.exists():
        remaining = report['bytes'] - (part.stat().st_size if part.exists() else 0)
        if shutil.disk_usage(root).free < 5 * 1024**3 + max(0, remaining):
            raise ValueError('Need download space plus 5GiB reserve')
        subprocess.run([sys.executable, str(Path(__file__).with_name('resume_sftp_download.py')),
                        '--ssh-alias', config['host'], '--remote',
                        base + '/post/final/' + ep + '.final-1080p60.mkv',
                        '--part', str(part), '--sha256', digest], check=True, **BACKGROUND_PROCESS)
        if part.stat().st_size != report['bytes']:
            raise ValueError('Downloaded byte count differs')
    candidate = target if target.exists() else part
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                       '-show_streams', '-of', 'json', str(candidate)], text=True, encoding='utf-8',
                       **BACKGROUND_PROCESS))
    check_final_media(info)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    duration = float(info['format']['duration'])
    if (video.get('profile') != 'Main 10' or video.get('pix_fmt') != 'yuv420p10le'
            or abs(duration - float(report['master_duration'])) > 1):
        raise ValueError('Final Main10 profile or actual master duration differs')
    for seek in (0, duration / 2, max(0, duration - 4)):
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-ss', str(seek),
                        '-i', str(candidate), '-t', '3', '-map', '0:v:0', '-map', '0:a:0',
                        '-f', 'null', '-'], check=True, **BACKGROUND_PROCESS)
    # Compare the complete decoded mix with the cloud's original-mix proof.
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-xerror', '-nostdin',
                                  '-i', str(candidate), '-map', '0:a:0', '-vn', '-c:a',
                                  'pcm_s32le', '-f', 'hash', '-hash', 'sha256', '-'], text=True,
                                  **BACKGROUND_PROCESS)
    if pcm.strip().split('=')[-1].lower() != report['decoded_pcm_sha256']:
        raise ValueError('Returned mix differs from cloud lossless evidence')
    if candidate == part:
        target.parent.mkdir(parents=True, exist_ok=True)
        part.replace(target)
    receipt = record_final(root, ep, target, remote_report)
    return finish_delivery(root, config, ep, receipt)


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
