"""Append a separately encoded intro without re-encoding the accepted episode.

Writes an independent publishing artifact and evidence; never replaces the
accepted body or inherits its delivery receipt for a different file.
"""
import argparse
import hashlib
import json
import os
import subprocess
import shutil
from pathlib import Path

BG = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def run(args):
    try:
        return subprocess.check_output([str(x) for x in args], stderr=subprocess.PIPE, **BG)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(error.stderr.decode(errors='replace')[-3000:]) from error


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))


def packets(path):
    info = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                          '-show_packets', '-show_entries', 'packet=pts', '-of', 'json', path]))
    return info['packets']


def picture_hashes(path):
    # Compare coded picture NALs; transport may repeat parameter sets at a join.
    raw = run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:v:0', '-an', '-c:v', 'copy',
               '-bsf:v', 'filter_units=remove_types=32|33|34|35|39|40',
               '-f', 'framehash', '-hash', 'sha256', '-']).decode()
    return [line.rsplit(',', 1)[-1].strip() for line in raw.splitlines()
            if line and not line.startswith('#')]


def pcm(path, skip_samples=0):
    args = ['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0', '-vn']
    if skip_samples:
        args += ['-af', f'atrim=start_sample={skip_samples}']
    return run(args + ['-c:a', 'pcm_s24le', '-f', 'hash', '-hash', 'sha256', '-']).decode().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('intro', 'body', 'output', 'report', 'mkvmerge'):
        parser.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args()
    intro, body = args.intro.resolve(strict=True), args.body.resolve(strict=True)
    output = args.output.resolve()
    if output in (intro, body) or output.exists() or args.report.exists():
        raise ValueError('Publishing output/report must be new paths')
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.stem + '.pending.mkv')
    if pending.exists():
        raise ValueError('Retain/review the existing pending output before retry')
    transport = output.parent / (output.stem + '.transport')
    if transport.exists():
        raise ValueError('Review the previous transport workspace before retry')
    # Pending append + video transport + repaired output may coexist.
    needed = 3 * (intro.stat().st_size + body.stat().st_size) + 5 * 1024**3
    if shutil.disk_usage(output.parent).free < needed:
        raise ValueError('Publishing peak budget plus 5GiB reserve is unavailable')
    inputs = [probe(x) for x in (intro, body)]
    for info in inputs:
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        if (video['codec_name'], video.get('profile'), video['width'], video['height'], video['r_frame_rate']) != ('hevc', 'Main 10', 1920, 1080, '60/1'):
            raise ValueError('Expected HEVC Main10 1080p60')
        if (audio['codec_name'], int(audio['sample_rate']), audio['channels'], int(audio['bits_per_raw_sample'])) != ('flac', 48000, 2, 24):
            raise ValueError('Expected FLAC24 48k stereo')
    intro_seconds = float(inputs[0]['format']['duration'])
    if abs(intro_seconds * 60 - round(intro_seconds * 60)) > 0.0001:
        raise ValueError('Intro must end on an exact 60fps frame')
    before = {str(x): (x.stat().st_size, x.stat().st_mtime_ns, digest(x)) for x in (intro, body)}
    result = subprocess.run([str(args.mkvmerge), '-o', str(pending), str(intro), '+', str(body)],
                            capture_output=True, **BG)
    log = output.with_suffix('.mux.log')
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode not in (0, 1):
        raise RuntimeError('mkvmerge failed; see ' + str(log))
    # Different encoders may use different HEVC parameter sets.  Annex-B
    # transport carries each source's VPS/SPS/PPS in-band at its own keyframes.
    # Keep timestamps and reuse the losslessly appended audio from mkvmerge.
    transport.mkdir(exist_ok=False)
    for name, source in [('intro', intro), ('body', body)]:
        run(['ffmpeg', '-v', 'error', '-i', source, '-map', '0:v:0', '-an', '-c:v', 'copy',
             '-bsf:v', 'hevc_mp4toannexb', '-muxdelay', '0', '-f', 'mpegts', transport / (name + '.ts')])
    listing = transport / 'video.ffconcat'
    listing.write_text(f"ffconcat version 1.0\nfile intro.ts\nduration {intro_seconds:.9f}\nfile body.ts\n", encoding='utf-8')
    repaired = transport / 'joined.mkv'
    run(['ffmpeg', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', listing, '-i', pending,
         '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', repaired])
    os.replace(repaired, pending)
    original, joined = packets(body), packets(pending)
    added = round(intro_seconds * 60)
    if len(joined) != len(original) + added:
        raise ValueError('Unexpected video packet count')
    offset = round(intro_seconds * 1000)
    for old, new in zip(original, joined[added:]):
        if int(new['pts']) - int(old['pts']) != offset:
            raise ValueError('Body video packet clock changed')
    if picture_hashes(body) != picture_hashes(pending)[added:]:
        raise ValueError('Body coded picture payload changed')
    body_pcm = pcm(body)
    if pcm(pending, round(intro_seconds * 48000)) != body_pcm:
        raise ValueError('Body mixed PCM changed')
    total = float(probe(pending)['format']['duration'])
    expected = intro_seconds + float(inputs[1]['format']['duration'])
    if abs(total - expected) > 0.05:
        raise ValueError('Unexpected joined duration')
    for start in (0, intro_seconds - 1, intro_seconds, total / 2, total - 3):
        run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '2', '-ss', str(max(0, start)),
             '-i', pending, '-t', '3', '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    for path in (intro, body):
        if (path.stat().st_size, path.stat().st_mtime_ns, digest(path)) != before[str(path)]:
            raise ValueError('Input changed during publishing')
    evidence = {'state': 'PUBLISHING_COPY_VERIFIED', 'intro_seconds': intro_seconds,
                'body_path': str(body), 'body_sha256': before[str(body)][2],
                'intro_path': str(intro), 'intro_sha256': before[str(intro)][2],
                'path': str(output), 'sha256': digest(pending), 'duration_seconds': total,
                'body_video_packets': len(original), 'intro_video_packets': added,
                'body_coded_picture_payloads_identical': True, 'body_pts_offset_ms': offset,
                'body_pcm_identical': True, 'body_pcm_hash': body_pcm,
                'join_and_first_middle_end_decode_passed': True,
                'original_delivery_receipt_retained_separately': True}
    os.replace(pending, output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
