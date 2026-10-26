#!/usr/bin/env python3
"""Story-relative delivery names and receipt-gated episode cleanup.

Cleanup touches only registered generated artifacts, never arbitrary paths.
No command in this module starts inference or edits source media/projects.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

EP = re.compile(r'S\d{2}E\d{2,3}\Z')
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def now():
    return datetime.now(timezone.utc).isoformat()


def episode(value):
    if not EP.fullmatch(value):
        raise ValueError('Expected a stable season/episode ID, e.g. S01E49')
    return value


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def chinese_number(value):
    if not 1 <= value <= 999:
        raise ValueError('Story chapter must be 1..999')
    digits = '零一二三四五六七八九'
    if value < 10:
        return digits[value]
    if value < 100:
        tens, units = divmod(value, 10)
        return (digits[tens] if tens > 1 else '') + '十' + (digits[units] if units else '')
    hundreds, rest = divmod(value, 100)
    return digits[hundreds] + '百' + (('零' if rest < 10 else '一' if rest < 20 else '')
                                     + chinese_number(rest) if rest else '')


def parse_number(value):
    if value.isdecimal():
        return int(value)
    digits = {c: n for n, c in enumerate('零一二三四五六七八九')}
    digits['两'] = 2
    result, digit = 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        elif char in '十百':
            result += (digit or 1) * {'十': 10, '百': 100}[char]
            digit = 0
        else:
            raise ValueError('Unrecognized story chapter number')
    return result + digit


def metadata_from_name(name, ep):
    episode(ep)
    if not re.search(re.escape(ep) + r'(?!\d)', name, flags=re.I):
        raise ValueError('Filename belongs to a different episode')
    text = re.split(r'[丨|]', Path(name).stem, maxsplit=1)[-1].strip()
    match = re.fullmatch(r'(.+?)[（(]([零一二三四五六七八九十百两\d]+)[）)]\s*(.+)', text)
    if not match:
        raise ValueError('Missing story-relative chapter/title; supply catalog metadata instead of guessing')
    story, part, title = match.groups()
    part = parse_number(part)
    chapter = chinese_number(part)
    title = INVALID.sub('_', title).strip().rstrip('. ')
    if not title:
        raise ValueError('Empty chapter title')
    return {'episode': ep, 'story': story, 'story_part': part, 'chapter_title': title,
            'final_name': f'第{chapter}回 {title}.mkv', 'source_name': name}


def catalog(root, ep):
    root = Path(root)
    cached = root / '.kwjd' / 'catalog' / f'{ep}.json'
    manifest = root / 'manifests' / f'{ep}.json'
    if manifest.is_file():
        result = metadata_from_name(load(manifest)['InputName'], ep)
    else:
        candidates = sorted(p for p in (root / 'videos').glob('*')
                            if p.is_file() and re.search(re.escape(ep) + r'(?!\d)', p.name, re.I))
        if len(candidates) == 1:
            result = metadata_from_name(candidates[0].name, ep)
        elif cached.is_file():
            result = load(cached)
            if result.get('episode') != ep or Path(result['final_name']).name != result['final_name']:
                raise ValueError('Invalid catalog record')
        else:
            raise ValueError(f'Need unambiguous chapter metadata for {ep}')
    save(cached, result)
    return result


def receipt_path(root, ep):
    return Path(root) / '.kwjd' / 'receipts' / f'{ep}.json'


def check_final_media(info):
    videos = [s for s in info['streams'] if s['codec_type'] == 'video']
    audios = [s for s in info['streams'] if s['codec_type'] == 'audio']
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError('Expected one final video and one mixed audio stream')
    v, a = videos[0], audios[0]
    if (v['codec_name'], v['width'], v['height']) != ('hevc', 1920, 1080):
        raise ValueError('Final must be HEVC 1920x1080')
    if Fraction(v['r_frame_rate']) != 60 or Fraction(v['avg_frame_rate']) != 60:
        raise ValueError('Final must have nominal and average 60fps')
    if a['codec_name'] != 'flac' or int(a.get('bits_per_raw_sample', 0)) < 24 or int(a['sample_rate']) < 48000:
        raise ValueError('Final must contain verified FLAC24/48k or better')


def record_final(root, ep, file, remote_report):
    root, file = Path(root), Path(file).resolve(strict=True)
    final_root = (root / 'final').resolve()
    if not file.is_relative_to(final_root) or file.suffix.lower() != '.mkv':
        raise ValueError('Returned final must be inside the Windows final directory')
    report = load(remote_report)
    digest = sha256(file)
    if report.get('episode') != ep or report.get('sha256') != digest:
        raise ValueError('Remote report and returned final SHA/episode differ')
    if not re.fullmatch(r'[0-9a-f]{64}', report.get('master_sha256', '')):
        raise ValueError('Remote final report must bind the actual master SHA')
    uploaded = root / '.kwjd/uploads' / f'{ep}.json'
    upload = load(uploaded)
    if upload.get('verified') is not True or upload.get('sha256') != report['master_sha256']:
        raise ValueError('Returned final used a different master than the verified current upload')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                                              '-show_streams', '-of', 'json', str(file)]))
    check_final_media(info)
    duration = float(info['format']['duration'])
    if abs(duration - float(report['master_duration'])) > 1:
        raise ValueError('Final duration differs from the verified master')
    for start in (0, duration / 2, max(0, duration - 4)):
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-ss', str(start),
                        '-i', str(file), '-t', '3', '-map', '0:v:0', '-map', '0:a:0',
                        '-f', 'null', '-'], check=True)
    record = {'schema_version': 1, 'episode': ep, 'local_path': str(file), 'sha256': digest,
              'remote_final_name': f'{ep}.final-1080p60.mkv', 'received': True,
              'technical_checked': True, 'quality_checked': False, 'received_at': now(),
              'master_sha256': report['master_sha256'],
              'duration_seconds': duration, 'bytes': file.stat().st_size}
    previous = receipt_path(root, ep)
    if previous.exists():
        old = load(previous)
        if old.get('sha256') != digest:
            raise ValueError('A different final receipt already exists; retire explicitly first')
        if old.get('quality_checked') is True:
            record = old
    save(previous, record)
    return record


def validate_receipt(record, ep, role):
    if record.get('schema_version') != 1 or record.get('episode') != ep:
        raise ValueError('Wrong receipt schema/episode')
    if not all(record.get(k) is True for k in ('received', 'technical_checked', 'quality_checked')):
        raise ValueError('Return, technical validation and actual quality checks must all be complete')
    if record.get('remote_final_name') != f'{ep}.final-1080p60.mkv':
        raise ValueError('Unexpected remote final name')
    if not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', '')):
        raise ValueError('Invalid receipt hash')
    if role == 'windows' and sha256(record['local_path']) != record['sha256']:
        raise ValueError('Preserved Windows final has changed or is missing')


def artifact_path(root, ep, role, relative):
    """Only these workflow-owned locations can ever be registered/deleted."""
    episode(ep)
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or str(rel) in ('.', ''):
        raise ValueError('Artifact must be a bounded relative path')
    base = Path(root) if role == 'windows' else Path(root) / 'post'
    n = int(ep.split('E')[1])
    allowed_files = ({f'masters/{ep}.clean-master.{ext}' for ext in ('mov', 'mkv', 'mp4')}
                     | {f'background/E{n:02d}-background.mp4'} if role == 'windows' else
                     {f'incoming/{ep}.clean-master.{ext}' for ext in ('mov', 'mkv', 'mp4')}
                     | {f'final/{ep}.final-1080p60.mkv'})
    allowed_dirs = {f'work/{ep}'} if role == 'windows' else {f'cache/{ep}'}
    if rel.as_posix() not in allowed_files | allowed_dirs:
        raise ValueError('Not an allowed generated episode artifact; preserve it')
    # post/ itself may be a deliberately configured storage symlink; children may not escape it.
    target = base / rel
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError('Artifact escapes the intended workspace')
    parents = [base.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts))]
    for path in parents + ([target, *list(target.rglob('*'))] if target.is_dir() else [target]):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Symlink/junction artifacts are never deleted')
    return target


def fingerprint(path):
    path = Path(path)
    if path.is_file():
        return {'kind': 'file', 'bytes': path.stat().st_size, 'sha256': sha256(path)}
    if path.is_dir():
        if any(not (p.is_file() or p.is_dir()) for p in path.rglob('*')):
            raise ValueError('Special files cannot be registered for recursive cleanup')
        files = [{'name': f.relative_to(path).as_posix(), 'bytes': f.stat().st_size,
                  'sha256': sha256(f)} for f in sorted(path.rglob('*')) if f.is_file()]
        directories = sorted(f.relative_to(path).as_posix() for f in path.rglob('*') if f.is_dir())
        return {'kind': 'directory', 'files': files, 'directories': directories}
    raise FileNotFoundError(path)


def artifact_manifest(root, ep, role):
    return Path(root) / '.kwjd' / 'artifacts' / f'{ep}.{role}.json'


def register_artifact(root, ep, role, relative):
    path = artifact_path(root, ep, role, relative)
    record = {'relative': Path(relative).as_posix(), 'fingerprint': fingerprint(path)}
    manifest = artifact_manifest(root, ep, role)
    records = load(manifest) if manifest.exists() else {'episode': ep, 'role': role, 'artifacts': []}
    if records['episode'] != ep or records['role'] != role:
        raise ValueError('Wrong artifact manifest')
    existing = next((v for v in records['artifacts'] if v['relative'] == record['relative']), None)
    if existing and existing != record:
        raise ValueError('Registered artifact changed; retire it explicitly before replacing')
    if not existing:
        records['artifacts'].append(record)
    save(manifest, records)
    return record


@contextmanager
def cleanup_lock(root, role):
    if role != 'linux':
        yield
        return
    if os.name == 'nt':
        raise ValueError('Linux cleanup must run on the actual Linux host')
    import fcntl
    lock = Path(root) / 'post/jobs/.worker.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Worker is active; cleanup refused') from error
        yield


def cleanup(root, ep, role, receipt, execute=False):
    root = Path(root)
    record = load(receipt)
    validate_receipt(record, ep, role)
    with cleanup_lock(root, role):
        done_path = root / '.kwjd/cleanup' / f'{ep}.{role}.json'
        if done_path.exists():
            done = load(done_path)
            if done.get('final_sha256') != record['sha256'] or done.get('state') != 'CLEANED':
                raise ValueError('Mismatching cleanup receipt')
            return done
        if role == 'linux':
            final = root / 'post/final' / record['remote_final_name']
            if sha256(final) != record['sha256']:
                raise ValueError('Server final differs from Windows receipt')
            job_path = root / 'post/jobs' / f'{ep}.json'
            if job_path.exists() and load(job_path).get('state') not in {'READY', 'RECEIVED', 'CLEANED'}:
                raise ValueError('Episode job is not in a completed state')
        manifest = artifact_manifest(root, ep, role)
        if not manifest.is_file():
            raise ValueError('No registered artifact manifest; nothing may be deleted')
        entries = load(manifest)
        if entries.get('episode') != ep or entries.get('role') != role:
            raise ValueError('Wrong artifact registry')
        paths = []
        for item in entries['artifacts']:
            path = artifact_path(root, ep, role, item['relative'])
            if path.exists() and fingerprint(path) != item['fingerprint']:
                raise ValueError(f'Artifact changed after registration: {path}')
            if '.clean-master.' in path.name and item['fingerprint'].get('sha256') != record.get('master_sha256'):
                raise ValueError('Master artifact does not match the delivered final master')
            paths.append(path)
        # Refuse to call an episode clean if a recognized generated output was never registered.
        n = int(ep.split('E')[1])
        base = root if role == 'windows' else root / 'post'
        known = ([base / f'masters/{ep}.clean-master.{ext}' for ext in ('mov', 'mkv', 'mp4')]
                 + [base / f'background/E{n:02d}-background.mp4', base / f'work/{ep}'] if role == 'windows' else
                 [base / f'incoming/{ep}.clean-master.{ext}' for ext in ('mov', 'mkv', 'mp4')]
                 + [base / f'cache/{ep}', base / f'final/{ep}.final-1080p60.mkv'])
        if any(p.exists() and p not in paths for p in known):
            raise ValueError('An episode output is unregistered; inspect and register before cleanup')
        report = {'episode': ep, 'role': role, 'final_sha256': record['sha256'],
                  'state': 'PLAN', 'paths': [str(p) for p in paths], 'checked_at': now()}
        if execute:
            # Revalidate each target immediately before native deletion, never invoke a shell.
            for item, path in zip(entries['artifacts'], paths):
                artifact_path(root, ep, role, item['relative'])
                if path.exists():
                    if fingerprint(path) != item['fingerprint']:
                        raise ValueError('Artifact changed immediately before deletion')
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
            report['state'] = 'CLEANED'
            save(done_path, report)
        return report


def gate_next(root, current):
    """Local preparation may overlap cloud work; only unfinished local release blocks it.

    Cloud serialization and filesystem budgets belong to the remote worker. A
    Windows shutdown/final receipt must not stall already accepted cloud jobs.
    """
    root = Path(root)
    previous = set(p.stem for p in (root / '.kwjd/uploads').glob('*.json'))
    for ep in previous - {current}:
        record = load(root / '.kwjd/uploads' / f'{ep}.json')
        done = load(root / '.kwjd/cache-cleanup' / f'{ep}.json')
        if (record.get('verified') is not True or record.get('local_master_deleted') is not True
                or done.get('state') != 'CACHE_CLEANED'
                or done.get('master_sha256') != record.get('sha256')):
            raise ValueError(f'Finish {ep} local upload/cache release before uploading {current}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('final-name', 'record-final', 'quality-checked', 'register', 'cleanup', 'gate-next'))
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--episode', required=True, type=episode)
    p.add_argument('--role', choices=('windows', 'linux'), default='windows')
    p.add_argument('--file', type=Path)
    p.add_argument('--remote-report', type=Path)
    p.add_argument('--receipt', type=Path)
    p.add_argument('--artifact')
    p.add_argument('--note')
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    if args.command == 'final-name':
        print(catalog(args.root, args.episode)['final_name'])
        return
    if args.command == 'record-final':
        if not args.file or not args.remote_report:
            p.error('--file and --remote-report are required')
        result = record_final(args.root, args.episode, args.file, args.remote_report)
    elif args.command == 'quality-checked':
        if not args.note or len(args.note.strip()) < 8:
            p.error('--note must describe actual picture, subtitle, sync and audio checks')
        path = receipt_path(args.root, args.episode)
        result = load(path)
        if result.get('received') is not True or result.get('technical_checked') is not True:
            raise ValueError('Fetch and technically validate final first')
        if sha256(result['local_path']) != result['sha256']:
            raise ValueError('Final hash changed')
        result.update(quality_checked=True, quality_note=args.note, quality_checked_at=now())
        save(path, result)
    elif args.command == 'register':
        if not args.artifact:
            p.error('--artifact is required')
        result = register_artifact(args.root, args.episode, args.role, args.artifact)
    elif args.command == 'cleanup':
        result = cleanup(args.root, args.episode, args.role,
                         args.receipt or receipt_path(args.root, args.episode), args.execute)
    else:
        gate_next(args.root, args.episode)
        result = {'state': 'NEXT_ALLOWED', 'episode': args.episode}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
