#!/usr/bin/env python3
"""Linux-only queue worker. AI inference is deliberately an external tested backend.

The worker NEVER launches the legacy OCR job. Run it only after independently
validating post/backend/enhance.sh on a short clip. One episode at a time.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kwjd import read_srt, PROJECT, EPISODE_RE
from enhance_stage import ensure_enhanced, WaitDisk, check_space, sha256
from master_policy import reject_retired
from font_preflight import preflight as font_preflight
from episode_lifecycle import register_artifact
from linux_stage_handoff import (preserve_source, validate_bundle, release_master_copy,
                                 release_enhancement_inputs, decoded_pcm_hash, decoded_video_frames)

ROOT = Path.home() / "qingqingwu"
POST = ROOT / "post"
BACKEND = POST / "backend" / "enhance.sh"
STAGE = "all"
WORKER_LOCK_HELD = False


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def check_rel(rel):
    path = Path(rel)
    if path.is_absolute() or '..' in path.parts:
        # The srt path is intentionally relative to post/; whitelist exactly.
        if not (str(rel).startswith("../output/S01E") and str(rel).endswith(".zh.srt")):
            raise ValueError("Unsafe path in job")
    return path


def command(args, log_file):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"\n[{now()}] command: {args!r}\n")
        f.flush()
        return subprocess.run(args, stdout=f, stderr=subprocess.STDOUT, check=False).returncode


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", str(path)], text=True, capture_output=True, check=True)
    return json.loads(r.stdout)


def duration(data):
    return float(data["format"]["duration"])


def video_stream(data):
    return next(s for s in data["streams"] if s["codec_type"] == "video")


def ready(path):
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    size = path.stat().st_size
    time.sleep(2)
    return path.is_file() and path.stat().st_size == size


def update(job_path, job, status, reason=""):
    job["state"] = status
    job["updated_at"] = now()
    job["detail"] = reason
    write_json(job_path, job)
    print(f"{job['episode']} => {status}: {reason}", flush=True)


def verified_master_hash(master, job):
    marker = master.with_name(master.name + ".verified.json")
    verification = json.loads(marker.read_text()) if marker.is_file() else {}
    expected = job.get("master_sha256") or (verification.get("sha256") if verification.get("transfer_verified") is True else None)
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return None
    actual = sha256(master)
    policy = POST / "backend" / "master-policy.json"
    reject_retired(actual, policy)
    if actual != expected.lower():
        raise ValueError("Master differs from confirmed transfer hash")
    return actual


def validate_final_binding(report, final, episode, master_hash, receipt, srt_hash, style_hash, fonts=None):
    expected = {"episode": episode, "master_sha256": master_hash,
                "enhanced_sha256": receipt["sha256"], "srt_sha256": srt_hash,
                "style_sha256": style_hash, "sha256": sha256(final)}
    if fonts is not None:expected['fonts'] = fonts
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("Existing final does not match current master/enhancement/SRT/style hashes; retained")


def approved_fonts(approval):
    fonts=approval.get('fonts')
    if not isinstance(fonts,dict) or not fonts:
        raise ValueError('Style approval must bind all packaged font hashes')
    directory=POST/'styles/fonts'
    packaged={p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ('.ttf','.otf','.ttc')}
    if packaged != set(fonts):
        raise ValueError('Packaged font set differs from approved fonts')
    for name,digest in fonts.items():
        font=directory/name
        if Path(name).name != name or font.is_symlink() or sha256(font) != digest:
            raise ValueError('Packaged font differs from style approval: '+name)
    return fonts


def approved_inputs(job_path, job, srt, style, master_hash=None):
    if not ready(srt):
        update(job_path, job, 'WAIT_SUBTITLE', 'Reviewed SRT missing or still being written');return None
    marker = srt.with_name(srt.name+'.ready.json')
    seal = json.loads(marker.read_text(encoding='utf-8-sig')) if marker.is_file() else {}
    srt_hash = sha256(srt)
    if seal.get('reviewed') is not True or seal.get('sha256') != srt_hash:
        update(job_path, job, 'WAIT_SUBTITLE', 'Waiting for matching reviewed SRT seal');return None
    if not style.is_file():
        update(job_path, job, 'WAIT_STYLE', 'Approved template missing');return None
    approval_path = style.with_name(style.name+'.ready.json')
    approval = json.loads(approval_path.read_text()) if approval_path.is_file() else {}
    style_hash = sha256(style)
    if approval.get('approved') is not True or approval.get('sha256') != style_hash:
        update(job_path, job, 'WAIT_STYLE_APPROVAL', 'Matching template approval missing/obsolete');return None
    if master_hash is not None and approval.get('master_sha256') != master_hash:
        update(job_path,job,'WAIT_STYLE_APPROVAL','Style approval belongs to another master');return None
    try:fonts=approved_fonts(approval)
    except (ValueError,FileNotFoundError) as exc:
        update(job_path,job,'WAIT_STYLE_APPROVAL',str(exc));return None
    read_srt(srt)
    return srt_hash, style_hash, marker, approval_path, fonts


def register_completed_assets(ep, master_hash, final, directory):
    """Freeze compact evidence outside the eventual cache cleanup target."""
    archive=POST/'reports/stages'/ep/master_hash
    archive.mkdir(parents=True,exist_ok=True)
    for source in directory.rglob('*'):
        if source.is_file() and (source.suffix == '.json' or source.name == ep+'.ass'):
            target=archive/source.relative_to(directory)
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                if sha256(target) != sha256(source):
                    raise ValueError('Archived evidence differs; retain both and inspect')
            else:
                with source.open('rb') as inp,target.open('xb') as out:shutil.copyfileobj(inp,out)
    register_artifact(ROOT,ep,'linux','cache/'+ep)
    register_artifact(ROOT,ep,'linux','final/'+final.name)


def backend_technically_ready(master, master_hash):
    """Machine-local measured readiness; this is not a human sample approval."""
    path=POST/'backend/production-ready.json'
    if not path.is_file():return False
    record=json.loads(path.read_text())
    if (record.get('master_sha256') != master_hash or record.get('technical_checked') is not True
            or record.get('performance_acceptable') is not True
            or not isinstance(record.get('measured_source_fps'),(int,float))
            or record['measured_source_fps'] <= 0):
        return False
    plan=json.loads(subprocess.check_output([str(BACKEND),'--plan',str(master)],text=True))
    return record.get('backend_fingerprint') == plan['backend_fingerprint']


def do_job(job_path):
    job = json.loads(job_path.read_text(encoding="utf-8-sig"))
    ep = job["episode"]
    if not EPISODE_RE.fullmatch(ep):
        raise ValueError("Episode identifier must match configured season and E-number")
    if job.get("schema_version") != 1:
        raise ValueError("Unsupported job schema")
    master_rel = check_rel(job["master"])
    style_rel = check_rel(job["style"])
    output_rel = check_rel(job["output"])
    if any(p.parts[0] not in ("incoming", "styles", "final") for p in (master_rel, style_rel, output_rel)):
        raise ValueError("Job paths must be in the post workspace")
    master, style, final = POST / master_rel, POST / style_rel, POST / output_rel
    srt = ROOT / "output" / f"{ep}.zh.srt"  # Existing OCR output is read-only.
    ass = POST / "cache" / ep / f"{ep}.ass"
    enhanced = POST / "cache" / ep / f"{ep}.enhanced.mkv"
    source_bundle = enhanced.with_name(ep + '.source.ready.json')
    log = POST / "logs" / f"{ep}.log"
    report_path = POST/'reports'/f'{ep}.json'
    pending_report = report_path.with_name(ep+'.pending.json')
    final_partial = final.with_name(final.stem+'.partial'+final.suffix)
    if (final.is_file() or pending_report.is_file()) and STAGE == 'all':
        expected = verified_master_hash(master, job) if master.exists() else job.get('master_sha256')
        inputs = approved_inputs(job_path, job, srt, style,expected)
        if inputs is None:return
        current_hash, style_hash, _, _, font_hashes = inputs
        if not expected:raise ValueError('No confirmed master identity for existing final')
        reject_retired(expected,POST/'backend/master-policy.json')
        report = json.loads((report_path if report_path.is_file() else pending_report).read_text())
        source_record = json.loads(source_bundle.read_text())
        if (report.get('source_bundle_sha256') != sha256(source_bundle)
                or source_record.get('master_sha256') != expected):
            raise ValueError('Existing final lacks current source handoff binding')
        receipt = source_record['enhanced_checkpoint']
        candidate = final if final.is_file() else final_partial
        validate_final_binding(report,candidate,ep,expected,receipt,current_hash,style_hash,font_hashes)
        if report.get('final_decode_checked') is not True or report.get('final_pcm_equivalent') is not True:
            raise ValueError('Incomplete final acceptance report; assets retained')
        if not final.exists():
            os.link(final_partial,final);final_partial.unlink()
        if not report_path.exists():write_json(report_path,report)
        release_enhancement_inputs(POST,ep,enhanced,source_bundle.with_name(ep+'.mixed.flac'),
                                  final,report,worker_lock_held=WORKER_LOCK_HELD)
        register_completed_assets(ep,expected,final,enhanced.parent)
        update(job_path,job,'READY','Verified final retained; previous-stage inputs released')
        return
    released = not master.exists() and source_bundle.is_file()
    if not released and not ready(master):
        update(job_path, job, "WAIT_MASTER", "Master not uploaded/stable")
        return
    master_hash = job.get('master_sha256') if released else verified_master_hash(master, job)
    if master_hash is None:
        update(job_path, job, "WAIT_MASTER_VERIFICATION", "Waiting for confirmed transfer SHA256, not file presence")
        return
    reject_retired(master_hash, POST/'backend/master-policy.json')
    job['master_sha256'] = master_hash
    if not released:register_artifact(ROOT,ep,'linux',master_rel.as_posix())
    if (not BACKEND.is_file() or not os.access(BACKEND, os.X_OK)) and not enhanced.exists():
        update(job_path, job, "WAIT_BACKEND", "Validated enhancement backend not installed")
        return
    if not enhanced.exists() and not backend_technically_ready(master,master_hash):
        update(job_path,job,'WAIT_BACKEND_TECHNICAL',
               'Actual-master technical/performance validation pending; no AI job launched')
        return
    update(job_path, job, "ENHANCING", "Independent of ASS, fonts and OCR SRT")
    try:
        if released:
            source_record, mixed_audio = validate_bundle(source_bundle, enhanced, master_hash)
            release_audit = source_bundle.with_name(ep+'.master-released.json')
            if (not release_audit.is_file()
                    or json.loads(release_audit.read_text()).get('master_sha256') != master_hash):
                raise ValueError('Missing transferred master without matching release audit')
            receipt = source_record['enhanced_checkpoint']
        else:
            receipt = ensure_enhanced(master, enhanced, BACKEND, ep, log, PROJECT["minimum_free_gib"])
            source_record, mixed_audio = preserve_source(master, enhanced, receipt, source_bundle, ep, log)
    except WaitDisk as exc:
        update(job_path, job, "WAIT_DISK", str(exc))
        return
    if receipt.get("master_sha256") != master_hash:
        raise ValueError("Enhanced checkpoint does not bind the confirmed master")
    # Explicit Windows completion confirmation is separate from upload/file presence.
    upload_receipt = POST/'receipts'/f'{ep}.upload.json'
    upload = json.loads(upload_receipt.read_text()) if upload_receipt.is_file() else {}
    may_release = (upload.get('episode') == ep and upload.get('sha256') == master_hash
                   and upload.get('verified') is True and upload.get('local_master_deleted') is True)
    if released or may_release:
        release_master_copy(POST, master, source_bundle, enhanced, master_hash,
                            worker_lock_held=WORKER_LOCK_HELD)
    job["enhanced_sha256"] = receipt["sha256"]
    update(job_path, job, "ENHANCED_READY", "Hash-bound1080p60 checkpoint retained")
    if STAGE == "enhance":
        return
    inputs = approved_inputs(job_path,job,srt,style,master_hash)
    if inputs is None:return
    current_hash, style_hash, marker, style_seal, font_hashes = inputs
    # Encoded output has a bounded50Mbps video budget plus lossless audio margin.
    seconds = duration(source_record['source_probe'])
    need = int(seconds * 60_000_000 / 8 + 1024**3)
    try:
        check_space(final.parent, need, PROJECT["minimum_free_gib"])
    except WaitDisk as exc:
        update(job_path, job, "WAIT_DISK", str(exc))
        return
    update(job_path, job, "PROCESSING", "AI enhancement, then ASS burn-in")
    ass.parent.mkdir(parents=True, exist_ok=True)
    from kwjd import cmd_make_ass
    from argparse import Namespace
    cmd_make_ass(Namespace(srt=str(srt), template=str(style), output=str(ass)))
    font_evidence = font_preflight(style,POST/'styles/fonts',ass.parent,log)
    src = source_record['source_probe']
    audio_streams = [item for item in src["streams"] if item["codec_type"] == "audio"]
    if not audio_streams:
        raise ValueError("Bilibili Hi-Res final requires a mixed audio track in the master")
    audio_cfg = PROJECT["audio"]
    # Preserve the native mixed PCM clock, including64k/88.2k sources.
    original_rate = int(audio_streams[0].get("sample_rate", 0) or 0)
    final_rate = original_rate
    if final_rate < audio_cfg['minimum_sample_rate']:
        raise ValueError('Native audio rate below required minimum; no implicit resampling')
    final.parent.mkdir(parents=True, exist_ok=True)
    temp = final.with_name(final.stem + ".partial" + final.suffix)
    if temp.exists():
        raise ValueError("Unverified final partial exists; retained, do not overwrite")
    fonts = POST / "styles" / "fonts"
    # filenames are built from the episode ID; escape FFmpeg filter metacharacters.
    ass_str = str(ass).replace("\\", "/").replace(":", r"\:")
    font_str = str(fonts).replace("\\", "/").replace(":", r"\:")
    vf = f"ass=filename='{ass_str}':fontsdir='{font_str}'"
    runtime=json.loads((POST/'backend/runtime.json').read_text())
    encoder_gpu=runtime.get('encoder_gpu')
    if type(encoder_gpu) is not int or encoder_gpu < 0:
        raise ValueError('Explicit physical NVENC GPU required')
    gpu_name=subprocess.check_output(['nvidia-smi','--id='+str(encoder_gpu),
        '--query-gpu=name','--format=csv,noheader'],text=True).strip()
    if gpu_name != runtime.get('gpu_name'):
        raise ValueError('NVENC physical device identity differs from tested runtime')
    code = command(["ffmpeg", "-hide_banner", "-n", "-nostdin", "-v", "warning",
                    "-i", str(enhanced), "-itsoffset", str(source_record['audio_offset_seconds']), "-i", str(mixed_audio),
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-vf", vf, "-c:v", "hevc_nvenc", "-gpu",str(encoder_gpu),"-preset", "p6",
                    "-rc", "vbr", "-cq", "18", "-b:v", "0", "-maxrate", "50M", "-bufsize", "100M", "-pix_fmt", "p010le",
                    "-c:a", "flac", "-sample_fmt", "s32",
                    "-bits_per_raw_sample", "24", "-ar", str(final_rate),
                    str(temp)], log)
    if code:
        raise RuntimeError(f"ASS burn-in/encode failed ({code}); see {log}")
    out = probe(temp)
    v = video_stream(out)
    if v["codec_name"] != "hevc" or v["width"] != 1920 or v["height"] != 1080:
        raise ValueError("Final does not match codec/resolution requirements")
    from fractions import Fraction
    if (Fraction(v['avg_frame_rate']) != 60 or Fraction(v['r_frame_rate']) != 60
            or v.get('pix_fmt') not in ('yuv420p10le','p010le')):
        raise ValueError('Final must be actual1080p60 HEVC10bit')
    if abs(duration(src) - duration(out)) > 1.0:
        raise ValueError("Final duration is inconsistent with master")
    audio_out = [item for item in out["streams"] if item["codec_type"] == "audio"]
    if len(audio_out) != 1:
        raise ValueError("Final must contain the single mixed audio track")
    if audio_out[0].get("codec_name") != "flac":
        raise ValueError("Bilibili Hi-Res preset requires lossless FLAC audio")
    if int(audio_out[0].get("sample_rate", 0)) < 48000:
        raise ValueError("Audio sampling rate below 48 kHz")
    if int(audio_out[0].get("bits_per_raw_sample", 0)) < 24:
        raise ValueError("FLAC stream is not verified 24-bit")
    if decoded_pcm_hash(temp) != source_record['decoded_pcm_sha256']:
        raise ValueError('Final decoded PCM differs from original mixed master; previous inputs retained')
    if decoded_video_frames(temp,log) != source_record['enhanced_frame_count']:
        raise ValueError('Final video lost/added frames; previous inputs retained')
    digest = hashlib.sha256()
    with temp.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    # Approval may be revoked or replaced while a long encode is running.
    current_seal = json.loads(marker.read_text(encoding="utf-8-sig"))
    current_approval = json.loads(style_seal.read_text())
    if (sha256(mixed_audio) != source_record['audio_sha256'] or sha256(enhanced) != receipt["sha256"]
            or sha256(srt) != current_hash or sha256(style) != style_hash
            or current_seal.get("reviewed") is not True or current_seal.get("sha256") != current_hash
            or current_approval.get("approved") is not True or current_approval.get("sha256") != style_hash
            or current_approval.get('master_sha256') != master_hash
            or approved_fonts(current_approval) != font_hashes):
        raise ValueError("Source asset or approval changed during final encoding; partial retained")
    report = {
        "episode": ep, "completed_at": now(), "sha256": digest.hexdigest(),
        "master_sha256": receipt["master_sha256"],
        "enhanced_sha256": receipt["sha256"], "srt_sha256": current_hash,
        "style_sha256": style_hash,
        "fonts": font_hashes,
        "font_selection": font_evidence,
        "decoded_pcm_sha256": source_record['decoded_pcm_sha256'],
        "preserved_audio_sha256": source_record['audio_sha256'],
        "source_bundle_sha256": sha256(source_bundle),
        "final_decode_checked": True, "final_pcm_equivalent": True,
        "final_video_frame_count": source_record['enhanced_frame_count'],
        "master_duration": duration(src), "output_duration": duration(out),
        "final_path": str(final), "ai_backend": str(BACKEND),
        "audio_preset": "bilibili_hires", "audio_codec": "flac",
        "audio_bits_per_sample": 24, "audio_sample_rate": final_rate,
        "audio_source_sample_rate": original_rate,
        "audio_note": "High-spec lossless output cannot recover source details; Bilibili Hi-Res visibility depends on platform submission eligibility.",
        "note": "Technical checks only; user must approve video edge quality and audio.",
    }
    # Persist acceptance before publication, allowing recovery across link/report crashes.
    write_json(pending_report,report)
    os.link(temp, final)  # Refuse to replace a final that appeared during encoding.
    temp.unlink()
    write_json(report_path, report)
    release_enhancement_inputs(POST,ep,enhanced,mixed_audio,final,report,
                              worker_lock_held=WORKER_LOCK_HELD)
    register_completed_assets(ep,master_hash,final,enhanced.parent)
    update(job_path, job, "READY", "Final passed structural checks; download with SHA-256 validation")
    # Keep the verified intermediate while styles/delivery are pending; no automatic deletion.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Try one queued/waiting job then exit")
    parser.add_argument("--loop", action="store_true", help="Long-running queue poll")
    parser.add_argument("--poll", type=int, default=90)
    parser.add_argument("--episode", help="Process only this episode identifier")
    parser.add_argument("--stage", choices=("enhance", "all"), default="all")
    args = parser.parse_args()
    global STAGE, WORKER_LOCK_HELD
    STAGE = args.stage
    if args.episode and not EPISODE_RE.fullmatch(args.episode):
        parser.error("Invalid episode identifier")
    if not args.once and not args.loop:
        parser.error("Choose --once or --loop")
    (POST / "jobs").mkdir(parents=True, exist_ok=True)
    lockfile = POST / "jobs" / ".worker.lock"
    with lockfile.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another Kwjd worker owns this queue")
        WORKER_LOCK_HELD = True
        while True:
            jobs = sorted(path for path in (POST / "jobs").glob(f"{PROJECT['season']}E*.json")
                          if EPISODE_RE.fullmatch(path.stem) and (not args.episode or path.stem == args.episode))
            for file in jobs:
                try:
                    job = json.loads(file.read_text(encoding="utf-8-sig"))
                    if job.get("state") in ("QUEUED", "ENHANCING", "PROCESSING", "WAIT_MASTER", "WAIT_MASTER_VERIFICATION", "WAIT_SUBTITLE",
                                            "WAIT_STYLE", "WAIT_STYLE_APPROVAL", "WAIT_BACKEND", "WAIT_BACKEND_TECHNICAL", "WAIT_DISK", "ENHANCED_READY"):
                        if STAGE == "enhance" and job.get("state") == "ENHANCED_READY":
                            continue
                        do_job(file)
                        if args.once:
                            return
                except Exception as e:
                    job = json.loads(file.read_text(encoding="utf-8-sig"))
                    update(file, job, "FAILED", str(e))
                    print(f"ERROR {file.name}: {e}", file=sys.stderr)
                    if args.once:
                        raise
            if args.once:
                print("No pending jobs")
                return
            time.sleep(max(30, args.poll))


if __name__ == "__main__":
    main()
