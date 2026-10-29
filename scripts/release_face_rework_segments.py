#!/usr/bin/env python3
"""Release only audited E49 encoding segments after verified final handoff.

LIFECYCLE stage-release authorization applies to intermediates, not retained
source/final videos. Preserve all frame logs, receipts and visual evidence.
"""
import argparse
import fcntl
import json
from pathlib import Path
import re
import subprocess

from audit_face_rework import audit, digest, require
from enhance_stage import atomic_json
from linux_stage_handoff import release_verified_file, resume_verified_release
from publish_face_rework_handoff import publish


def release(root, work, execute=False):
    root, work = root.resolve(), work.resolve()
    require(work.is_relative_to(root / 'post-home/experiments'), 'Unbounded work path')
    unit = 'kwjd-face-rework-e49-20261028.service'
    info = subprocess.check_output(['systemctl', '--user', 'show', unit,
                                    '-p', 'ActiveState', '-p', 'MainPID'], text=True)
    require('ActiveState=inactive' in info and 'MainPID=0' in info,
            'Rework worker must be terminal before release')
    enabled = subprocess.run(['systemctl', '--user', 'is-enabled', unit],
                             capture_output=True, text=True).stdout.strip()
    require(enabled in ('disabled', 'static', 'not-found'),
            'Disable completed worker boot restart before retiring checkpoints')
    locks = []
    try:
        for path in (work / 'worker.lock', work.parents[1] / '.face-compute.lock',
                     root / 'post/jobs/.worker.lock'):
            require(not path.is_symlink(), 'Lock symlink refused')
            stream = path.open('a+')
            locks.append(stream)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Freshly check both full videos and every server acceptance reference.
        published = publish(root, work, dry_run=True)
        require(Path(published['path']).is_file(), 'Distinct final not yet published')
        plan_path = work / 'segment-release-plan.json'
        receipt_path = work / 'segment-release-result.json'
        if plan_path.exists():
            plan = json.loads(plan_path.read_text())
            require(plan['final_sha256'] == published['sha256'], 'Changed downstream final')
        else:
            checked = audit(work, verify_source=True)
            require(checked['all_frames_present'] and checked['frames'] == 211054,
                    'Fresh complete segment/frame-log audit required')
            members = []
            for item in checked['segments']:
                path = work / 'segments' / (item['segment'] + '.mkv')
                members.append({'name': path.name, 'sha256': item['media_sha256'],
                                'bytes': path.stat().st_size})
            plan = {'schema_version': 1, 'episode': 'S01E49',
                    'authorization': 'docs/LIFECYCLE.md: verified downstream stage releases; not final deletion',
                    'final_path': published['path'], 'final_sha256': published['sha256'],
                    'acceptance_sha256': digest(work / 'server_acceptance.json'),
                    'members': members, 'total_bytes': sum(x['bytes'] for x in members),
                    'preserved': ['unique old final', 'new full final', 'audio', 'all receipts',
                                  'frame logs', 'PNG evidence', 'unlisted partials', 'environments']}
            atomic_json(plan_path, plan)
        require(plan['acceptance_sha256'] == digest(work / 'server_acceptance.json'),
                'Acceptance changed after plan')
        names = [x['name'] for x in plan['members']]
        require(len(names) == len(set(names)) == 352
                and set(names) == {f'segment-{i:05d}.mkv' for i in range(352)}
                and all(re.fullmatch(r'segment-\d{5}\.mkv', x) for x in names),
                'Unexpected/duplicate cleanup member')
        audit_receipt = json.loads((work / 'audit-complete-0814/checkpoint-audit.json').read_text())
        expected_hashes = {x['segment'] + '.mkv': x['media_sha256']
                           for x in audit_receipt['segments']}
        require(all(expected_hashes.get(x['name']) == x['sha256'] for x in plan['members']),
                'Plan differs from accepted complete audit')
        results = []
        for item in plan['members']:
            path = work / 'segments' / item['name']
            require(not path.is_symlink(), 'Media symlink refused')
            if path.exists():
                require(path.stat().st_size == item['bytes'] and digest(path) == item['sha256'],
                        'Segment changed; retain all remaining assets')
                if execute:
                    journal = release_verified_file(path, item['sha256'])
                    results.append({'name': item['name'], 'release_journal': journal})
            else:
                if execute:
                    journals = resume_verified_release(path, item['sha256'])
                else:
                    journals = []
                    for journal in path.parent.glob('.' + path.name + '.release-*/receipt.json'):
                        if journal.is_symlink() or journal.parent.is_symlink():
                            continue
                        saved = json.loads(journal.read_text())
                        if (saved.get('original_path') == str(path)
                                and saved.get('sha256') == item['sha256']
                                and saved.get('status') == 'released'
                                and not (journal.parent / 'artifact').exists()):
                            journals.append(str(journal))
                require(bool(journals), 'Missing media without a recoverable release journal')
                results.append({'name': item['name'], 'release_journals': journals})
        if execute:
            atomic_json(receipt_path, {'status': 'ENCODING_SEGMENTS_RELEASED',
                        'plan_sha256': digest(plan_path), 'files': results,
                        'released_bytes': plan['total_bytes'],
                        'windows_received': False, 'source_and_finals_retained': True})
        return {'execute': execute, 'files': len(names), 'bytes': plan['total_bytes'],
                'plan': str(plan_path), 'result': str(receipt_path) if execute else None}
    finally:
        for stream in reversed(locks):
            stream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'qingqingwu')
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    print(json.dumps(release(args.root, args.work, args.execute), indent=2))
