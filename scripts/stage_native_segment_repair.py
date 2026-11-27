"""Stage one native segment replacement; never approve or publish the final.

Render the replacement from its actual master with the fixed native pipeline
first. This tool stream-copies the retained final around that replacement and
proves that every compressed video packet outside it remains identical.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import time

from concat_clock import write_concat, verify_packet_clock, normalize_copy_timestamps
from enhance_stage import atomic_json, sha256


def replacement_bounds(first, count, total, source_rate=25):
    if (type(first) is not int or type(count) is not int or first < 0
            or first % 250 or count != 250 or source_rate != 25):
        raise ValueError('Repair requires one complete aligned250-frame native25 segment')
    start = first * 60 // 25
    frames = count * 60 // 25
    if start + frames >= total:
        raise ValueError('This bounded repair requires a retained suffix')
    return start, frames


def packets(path):
    result = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_packets', '-show_entries', 'packet=pts,flags,data_hash',
        '-show_data_hash', 'sha256', '-of', 'json', str(path)])
    rows = json.loads(result)['packets']
    rows.sort(key=lambda x: int(x['pts']))
    if len({x['pts'] for x in rows}) != len(rows):
        raise ValueError('Duplicate display timestamps')
    if not all(x.get('data_hash', '').startswith('SHA256:') for x in rows):
        raise ValueError('Actual compressed packet hashes required')
    return rows


def unchanged_packets(original, replacement, candidate, first, count):
    if (len(candidate) != len(original) or len(replacement) != count
            or candidate[:first] != original[:first]
            or candidate[first:first + count] != replacement
            or candidate[first + count:] != original[first + count:]):
        raise ValueError('Packet payload changed outside the declared replacement or patch differs')
    return dict(prefix_packets=first, replacement_packets=count,
                suffix_packets=len(original) - first - count,
                unchanged_outside_replacement=True, replacement_payload_exact=True)


def stage(root, episode, patch, receipt, first, count, area, candidate, resume=False, source_final=None, source_report=None, intermediate=False):
    root, patch, area, candidate = map(Path, (root, patch, area, candidate))
    post = root / 'post'
    if (source_final is None) != (source_report is None):
        raise ValueError('A staged parent needs both its actual final and technical report')
    report_path = Path(source_report) if source_report else post / 'reports' / f'{episode}.json'
    report = json.loads(report_path.read_text())
    proof = json.loads(Path(receipt).read_text())
    original = Path(source_final) if source_final else post / 'final' / f'{episode}.final-1080p60.mkv'
    if (original.is_symlink() or original.parent.resolve() != (post/'final').resolve()
            or report_path.is_symlink() or not report_path.resolve().is_relative_to((post/'reports').resolve())):
        raise ValueError('Retained source/report must be regular files in project final/reports areas')
    total = report['final_video_frame_count']
    start, frames = replacement_bounds(first, count, total)
    if (proof.get('technical_checked') is not True
            or proof.get('master_sha256') != report.get('master_sha256')
            or proof.get('source_start') != first or proof.get('input_frames') != count
            or proof.get('output_frames') != frames or sha256(patch) != proof.get('sample_sha256')
            or proof.get('pipeline', {}).get('coverage', {}).get('failed_frames') != 0
            or proof.get('pipeline', {}).get('source_start') != first
            or proof.get('pipeline', {}).get('input_frames') != count
            or proof.get('pipeline', {}).get('output_frames') != frames
            or proof.get('pipeline', {}).get('video_encodes') != 1
            or proof.get('pipeline', {}).get('ass_after_rife') is not True
            or proof.get('pipeline', {}).get('ass_sha256') != report.get('fused_subtitle_binding', {}).get('ass_sha256')
            or proof.get('pipeline', {}).get('native_recipe_sha256') != report.get('native_recipe_sha256')):
        raise ValueError('Replacement lacks its actual master/frame-processing proof')
    if sha256(original) != report['sha256']:
        raise ValueError('Retained original differs from its report')
    if ((area.exists() and not resume) or (candidate.exists() and not resume) or candidate.is_symlink()
            or candidate.parent.resolve() != (post / 'final').resolve()
            or not candidate.name.endswith('.candidate.mkv')):
        raise ValueError('Use fresh staging and a noncanonical final candidate')
    if (shutil.disk_usage(area.parent).free < 3 * original.stat().st_size + 5 * 2**30
            or shutil.disk_usage(candidate.parent).free < original.stat().st_size + 6 * 2**30):
        raise ValueError('Insufficient staging/candidate capacity plus reserve')
    old_rows = packets(original)
    if len(old_rows) != total or 'K' not in old_rows[start]['flags'] or 'K' not in old_rows[start + frames]['flags']:
        raise ValueError('Replacement boundaries must be actual independent keyframes')
    area.mkdir(exist_ok=resume)
    commands = []
    prefix, suffix = area / 'prefix.mkv', area / 'suffix.mkv'
    media = []
    frame_counts = []
    for output, seek, number in [(prefix, 0, start), (suffix, Fraction(start + frames, 60), total - start - frames)]:
        if not number:
            continue
        cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-n']
        if seek:
            # Stream-copy input seeking alone includes the preceding keyframe.
            # Preserve its absolute PTS, explicitly drop preceding packets,
            # then translate timestamps; never count those discarded packets.
            cmd += ['-copyts', '-ss', str(float(seek))]
        cmd += ['-i', str(original), '-map', '0:v:0', '-an', '-c:v', 'copy']
        if seek:
            cmd += ['-bsf:v', f"noise=drop='lt(pts*tb,{float(seek)})',setts=pts=PTS-{float(seek)}/TB:dts=DTS-{float(seek)}/TB",
                    '-avoid_negative_ts', 'disabled']
        else:
            cmd += ['-frames:v', str(number)]
        cmd += [str(output)]
        adopted = output.exists()
        if output.is_symlink():
            raise ValueError('Symlink staging refused')
        if not adopted:
            subprocess.run(cmd, check=True)
        part_rows = packets(output)
        offset = 0 if not seek else start + frames
        if ([x['data_hash'] for x in part_rows] !=
                [x['data_hash'] for x in old_rows[offset:offset + number]]):
            raise ValueError('Extracted or resumed part differs from exact original packet slice')
        verify_packet_clock(output, number)
        media.append(output)
        frame_counts.append(number)
        commands.append(dict(command=cmd, executed=not adopted,
                             resumed_after_full_packet_hash_check=adopted))
    listing, video = area / 'concat.txt', area / 'video.mkv'
    if start:
        media.insert(1, patch);frame_counts.insert(1, frames)
    else:
        media.insert(0, patch);frame_counts.insert(0, frames)
    write_concat(listing, media, frame_counts)
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-f', 'concat', '-safe', '0',
           '-i', str(listing), '-map', '0:v:0', '-an', '-c:v', 'copy', str(video)]
    if not video.exists():
        subprocess.run(cmd, check=True)
    elif not resume:
        raise FileExistsError(video)
    commands.append(cmd)
    normalization = None
    try:
        clock = verify_packet_clock(video, total)
    except ValueError:
        normalized = area / 'normalized.mkv'
        normalization = normalize_copy_timestamps(video, normalized, total)
        video, clock = normalized, normalization['clock']
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(video), '-i', str(original),
           '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-map_metadata', '-1', str(candidate)]
    if not candidate.exists():
        subprocess.run(cmd, check=True)
    elif not resume:
        raise FileExistsError(candidate)
    commands.append(cmd)
    clock = verify_packet_clock(candidate, total)
    payload = unchanged_packets([x['data_hash'] for x in old_rows],
        [x['data_hash'] for x in packets(patch)], [x['data_hash'] for x in packets(candidate)], start, frames)
    pcm = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(candidate),
        '-map', '0:a:0', '-vn', '-c:a', 'pcm_s32le', '-f', 'hash', '-hash', 'sha256', '-'], text=True).strip().split('=')[-1].lower()
    if pcm != report['decoded_pcm_sha256']:
        raise ValueError('Candidate no longer has the verified original mixed PCM')
    # Independent complete video decode, not merely packet counting.
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '4', '-i', str(candidate),
           '-map', '0:v:0', '-an', '-fps_mode', 'passthrough', '-f', 'null', '-', '-progress', 'pipe:1', '-nostats']
    if not intermediate:
        with (area / 'full-decode-progress.txt').open('w') as progress:
            subprocess.run(cmd, stdout=progress, check=True)
        decoded = (area / 'full-decode-progress.txt').read_text()
        counts = [int(line[6:]) for line in decoded.splitlines() if line.startswith('frame=')]
        if not counts or counts[-1] != total or 'progress=end' not in decoded:
            raise ValueError('Complete candidate decode has wrong frame count')
    if sha256(original) != report['sha256'] or sha256(patch) != proof['sample_sha256']:
        raise ValueError('Input changed during repair')
    result = dict(schema_version=1, at=time.time(), episode=episode,
        state=('PACKET_AND_PCM_VERIFIED_INTERMEDIATE_REQUIRES_FINAL_DECODE' if intermediate else 'TECHNICALLY_VERIFIED_CANDIDATE_REQUIRES_ACTUAL_QC'),
        master_sha256=report['master_sha256'], original_final=str(original),
        original_final_sha256=report['sha256'], original_report_path=str(report_path),
        original_report_sha256=sha256(report_path), patch=str(patch), patch_sha256=sha256(patch),
        patch_receipt_path=str(receipt), patch_receipt_sha256=sha256(receipt),
        candidate=str(candidate), sha256=sha256(candidate), bytes=candidate.stat().st_size,
        first_source_frame=first, source_frames=count, first_output_frame=start,
        output_frames=total, payload_proof=payload, packet_clock=clock,
        timestamp_normalization=normalization, decoded_pcm_sha256=pcm,
        full_decode_checked=not intermediate, final_quality_approved=False, canonical_media_changed=False,
        commands=commands, full_video_decode_command=cmd)
    atomic_json(area / 'result.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['root', 'patch', 'receipt', 'area', 'candidate']:
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--episode', required=True)
    p.add_argument('--first', required=True, type=int)
    p.add_argument('--count', default=250, type=int)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--source-final', type=Path)
    p.add_argument('--source-report', type=Path)
    p.add_argument('--intermediate', action='store_true')
    a = p.parse_args()
    print(json.dumps(stage(a.root, a.episode, a.patch, a.receipt, a.first, a.count, a.area, a.candidate, a.resume, a.source_final, a.source_report, a.intermediate)))
