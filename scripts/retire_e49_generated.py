#!/usr/bin/env python3
"""Retire inventoried E49 generated media only after real Windows receipt.

Never removes the new server final or touches input/SRT/fonts/env/CacheClip.
New Windows technical receipt is sufficient under the explicit human override;
quality_checked is preserved, never invented. Default is a full dry run.
"""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess

from enhance_stage import atomic_json
from inventory_e49_retirement import allowed_media
from linux_stage_handoff import release_verified_file, resume_verified_release


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_receipt(receipt, ready, ready_sha):
    require(receipt.get('received') is True and receipt.get('technical_checked') is True,
            'Actual Windows received/technical checks are required')
    for key in ('episode', 'sha256', 'bytes', 'source_sha256', 'master_sha256', 'decoded_pcm_sha256'):
        require(receipt.get(key) == ready[key], f'Windows receipt {key} differs')
    require(receipt.get('ready_report_sha256') == ready_sha,
            'Windows receipt belongs to a different READY report')
    require(receipt.get('remote_final_name') == Path(ready['final_path']).name,
            'Windows downloaded a different final variant')


def run(root, receipt_path, execute=False):
    root = root.resolve()
    work = root / 'post-home/experiments/face_rework_E49_20261028'
    post = root / 'post'
    ready_path = post / 'reports/S01E49.face-rework.json'
    ready = json.loads(ready_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    receipt_sha = sha(receipt_path)
    validate_receipt(receipt, ready, sha(ready_path))
    info = subprocess.check_output(['systemctl', '--user', 'show',
        'kwjd-face-rework-e49-20261028.service', '-p', 'ActiveState', '-p', 'MainPID'], text=True)
    require('ActiveState=inactive' in info and 'MainPID=0' in info, 'Rework is still active')
    locks = []
    try:
        for path in (work / 'worker.lock', work.parents[1] / '.face-compute.lock',
                     post / 'jobs/.worker.lock'):
            require(not path.is_symlink(), 'Lock symlink refused')
            stream = path.open('a+')
            locks.append(stream)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        final = Path(ready['final_path'])
        require(sha(final) == ready['sha256'] and final.stat().st_size == ready['bytes'],
                'Retained new server final differs from Windows receipt')
        final_inode = (final.stat().st_dev, final.stat().st_ino)
        plan_path = post / 'reports/S01E49.retirement-inventory-20261029.json'
        preflight = json.loads((post / 'reports/S01E49.retirement-preflight-20261029.json').read_text())
        require(sha(plan_path) == preflight['inventory_sha256'], 'Inventory changed after preflight')
        plan = json.loads(plan_path.read_text())
        require(plan['new_final_sha256'] == ready['sha256'], 'Inventory final differs')
        members = plan['members']
        require(len({x['path'] for x in members}) == len(members), 'Duplicate retirement path')
        present = []
        recovered = []
        # Validate every still-present target before releasing any of this pass.
        for item in members:
            path = Path(item['path'])
            require(path.is_absolute() and '..' not in path.parts and allowed_media(root, path),
                    'Unbounded or forbidden retirement path')
            require(not path.is_symlink(), 'Media symlink refused')
            if path.exists():
                stat = path.stat()
                require((stat.st_dev, stat.st_ino) != final_inode, 'New final inode must be retained')
                require(stat.st_size == item['bytes'] and sha(path) == item['sha256'],
                        'Retirement candidate changed; preserve remaining media')
                present.append(item)
            else:
                # Missing paths require exact per-file completed journals. Do
                # not change recovery journals during a read-only dry run.
                journals = []
                for journal in path.parent.glob('.' + path.name + '.release-*/receipt.json'):
                    if journal.is_symlink() or journal.parent.is_symlink():
                        continue
                    saved = json.loads(journal.read_text())
                    if (saved.get('original_path') == str(path)
                            and saved.get('sha256') == item['sha256']
                            and saved.get('status') in ('verified_isolated', 'released')):
                        require(not (journal.parent / 'artifact').exists(),
                                'Interrupted isolated artifact needs explicit recovery')
                        journals.append(str(journal))
                require(journals, 'Missing candidate without release proof')
                recovered.append({'path': str(path), 'journals': journals})
        require(sha(receipt_path) == receipt_sha, 'Windows receipt changed during preflight')
        if execute:
            atomic_json(work / 'windows-new-final-technical-receipt.json', receipt)
            intent = work / 'generated-retirement-intent.json'
            atomic_json(intent, {'at': datetime.now().astimezone().isoformat(),
                'plan_sha256': sha(plan_path), 'windows_receipt_sha256': receipt_sha,
                'new_final_sha256': ready['sha256'], 'quality_checked': receipt.get('quality_checked', False)})
            results = list(recovered)
            for item in present:
                path = Path(item['path'])
                journal = release_verified_file(path, item['sha256'])
                results.append({'path': str(path), 'journal': journal})
            result = {'at': datetime.now().astimezone().isoformat(),
                'status': 'AUTHORIZED_E49_GENERATED_MEDIA_RETIRED',
                'plan_sha256': sha(plan_path), 'windows_receipt_sha256': receipt_sha,
                'new_server_final_retained': str(final), 'new_final_sha256': ready['sha256'],
                'new_final_quality_checked': receipt.get('quality_checked', False), 'files': results,
                'original_input_srt_fonts_environments_and_e51_cacheclip_untouched': True}
            atomic_json(work / 'generated-retirement-result.json', result)
            alias = post / 'cache/master-validation/native_45_60s.ffv1.mkv'
            retired_fragment = root / 'post-home/cache-relocated/master-validation/native_45_60s.ffv1.mkv'
            if alias.is_symlink():
                require(alias.readlink() == retired_fragment and not retired_fragment.exists()
                        and any(x['path'] == str(retired_fragment) for x in members),
                        'Unexpected native fragment alias; preserve it')
                atomic_json(work / 'native-fragment-alias-retired.json',
                            {'alias': str(alias), 'former_target': str(retired_fragment),
                             'retirement_result_sha256': sha(work / 'generated-retirement-result.json')})
                alias.unlink()
            state_path = work / 'status.json'
            state = json.loads(state_path.read_text())
            require(state['sha256'] == ready['sha256'], 'Display state belongs to another final')
            state.update(stage='WINDOWS_TECHNICAL_RECEIVED_QUALITY_PENDING',
                         updated_at=datetime.now().astimezone().isoformat(),
                         windows_received=True, windows_quality_checked=receipt.get('quality_checked', False),
                         source_retained=False,
                         source_retirement_receipt=str(work / 'generated-retirement-result.json'),
                         windows_receipt_sha256=receipt_sha)
            atomic_json(state_path, state)
        return {'execute': execute, 'present_files': len(present), 'already_released': len(recovered),
                'new_final_retained': str(final), 'windows_technical_receipt_verified': True}
    finally:
        for stream in reversed(locks):
            stream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'qingqingwu')
    parser.add_argument('--windows-receipt', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.windows_receipt, args.execute), indent=2))
