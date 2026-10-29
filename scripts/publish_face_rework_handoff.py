#!/usr/bin/env python3
"""Publish an already reviewed E49 rework separately; never replace the source.

This is a handoff manifest, not the canonical fetch-final/lifecycle receipt.
No inference, encoding, acceptance invention, or Windows receipt creation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def publish(root, experiment, dry_run=False):
    root = root.resolve()
    experiment = experiment.resolve()
    require(experiment.is_relative_to(root / 'post-home' / 'experiments'),
            'Experiment must be inside the project experiments directory')
    approval_path = experiment / 'server_acceptance.json'
    approval = json.loads(approval_path.read_text())
    require(approval['episode'] == 'S01E49' and approval['server_accepted'] is True
            and approval['technical_pass'] is True
            and approval['bounded_visual_review_pass'] is True,
            'Explicit scoped server acceptance required')
    for evidence in approval['evidence']:
        path = Path(evidence['path']).resolve()
        require(path.is_relative_to(experiment), 'Evidence outside experiment')
        require(sha(path) == evidence['sha256'], f'Evidence changed: {path}')
    technical = json.loads((experiment / 'technical_result.json').read_text())
    old_report_path = root / 'post/reports/S01E49.json'
    old_report = json.loads(old_report_path.read_text())
    source = root / 'post/final/S01E49.final-1080p60.mkv'
    output = experiment / 'S01E49.face-reworked-1080p60.mkv'
    require(Path(technical['output']).resolve() == output, 'Unexpected output path')
    require(sha(source) == approval['source_sha256'] == technical['source_sha256']
            == old_report['sha256'], 'Retained source SHA mismatch')
    require(sha(output) == approval['sha256'] == technical['validation']['sha256'],
            'Rework SHA mismatch')
    require(output.stat().st_size == approval['bytes'], 'Rework size mismatch')
    destination = root / 'post/final' / output.name
    report = root / 'post/reports/S01E49.face-rework.json'
    require(destination.parent.stat().st_dev == output.stat().st_dev,
            'Hardlink requires the same filesystem; no implicit media copy')
    require(not destination.is_symlink(), 'Destination symlink refused')
    if destination.exists():
        require(os.path.samefile(destination, output), 'Destination already occupied')
    manifest = {
        'episode': 'S01E49', 'variant': 'face-rework',
        'status': 'SERVER_READY_PENDING_WINDOWS',
        'sha256': approval['sha256'], 'bytes': approval['bytes'],
        'master_sha256': old_report['master_sha256'],
        'master_duration': old_report['master_duration'],
        'source_sha256': approval['source_sha256'],
        'source_path': str(source), 'final_path': str(destination),
        'server_acceptance_path': str(approval_path),
        'server_acceptance_sha256': sha(approval_path),
        'technical_report_path': str(experiment / 'technical_result.json'),
        'final_decode_checked': True, 'final_pcm_equivalent': True,
        'decoded_pcm_sha256': technical['audio_pcm_sha256'],
        'final_video_frame_count': technical['frames'],
        'output_duration': float(technical['validation']['probe']['format']['duration']),
        'video': 'HEVC Main10 1920x1080 60fps BT.709 limited',
        'audio': 'FLAC 24-bit 48000Hz stereo; original mixed PCM unchanged',
        'rife_repeated': False, 'ass_burn_repeated': False,
        'quality_subject': approval['quality_subject'],
        'limitations': approval['limitations'],
        'windows_received': False, 'windows_quality_checked': False,
        'delivery_complete': False, 'standard_fetch_final_supported': False,
        'windows_instructions': [
            'Download this distinct final_path to a separate staging file; ordinary fetch-final still returns the retained old final.',
            'Freshly verify byte count and SHA; run local technical and Guo quality checks. Preserve old receipt as history.',
            'Only after those checks atomically replace the local named final. Record a NEW hash-bound rework receipt; never inherit old quality approval.',
            'Return the new receipt and quality result before any server final/source cleanup. Standard canonical cleanup is not authorized by this manifest.',
        ],
        'inherited_bindings': {key: old_report[key] for key in
                               ('srt_sha256', 'style_sha256', 'subtitle_timing')},
    }
    if report.exists():
        require(json.loads(report.read_text()) == manifest,
                'Handoff report differs; preserve existing receipt, reconcile explicitly')
    if not dry_run:
        if not destination.exists():
            os.link(output, destination)
        if not report.exists():
            # Publish a fully written JSON atomically without overwriting another writer.
            temporary = report.with_name(report.name + f'.{os.getpid()}.tmp')
            try:
                with temporary.open('x') as stream:
                    json.dump(manifest, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, report)
            finally:
                temporary.unlink(missing_ok=True)
    return {'dry_run': dry_run, 'path': str(destination), 'report': str(report),
            'sha256': manifest['sha256'], 'windows_received': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'qingqingwu')
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(publish(args.root, args.experiment, args.dry_run), indent=2))
