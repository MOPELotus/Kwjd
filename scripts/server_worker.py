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
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kwjd import read_srt, PROJECT, EPISODE_RE

ROOT = Path.home() / "qingqingwu"
POST = ROOT / "post"
BACKEND = POST / "backend" / "enhance.sh"


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
    log = POST / "logs" / f"{ep}.log"
    if final.is_file() and final.stat().st_size > 1024:
        update(job_path, job, "READY", "Existing final retained; inspect validation report")
        return
    if not ready(master):
        update(job_path, job, "WAIT_MASTER", "Master not uploaded/stable")
        return
    if not ready(srt):
        update(job_path, job, "WAIT_SUBTITLE", "Existing OCR SRT still missing or being written")
        return
    # OCR/Codex explicitly seals a reviewed SRT. A stable-sized file alone is not completion.
    marker = srt.with_name(srt.name + ".ready.json")
    if not marker.is_file():
        update(job_path, job, "WAIT_SUBTITLE", "Waiting for explicit reviewed SRT seal")
        return
    seal = json.loads(marker.read_text(encoding="utf-8-sig"))
    current_hash = hashlib.sha256(srt.read_bytes()).hexdigest()
    if not seal.get("reviewed") or seal.get("sha256") != current_hash:
        update(job_path, job, "WAIT_SUBTITLE", "SRT seal absent/obsolete; OCR review may still be running")
        return
    if not style.is_file():
        update(job_path, job, "WAIT_STYLE", "Aegisub template missing")
        return
    if not BACKEND.is_file() or not os.access(BACKEND, os.X_OK):
        update(job_path, job, "WAIT_BACKEND", "Codex must implement and validate post/backend/enhance.sh")
        return
    read_srt(srt)  # Refuse malformed transcripts, never rewrite OCR itself.
    free = shutil.disk_usage(POST).free
    reserve = PROJECT["minimum_free_gib"] * 1024 ** 3
    need = max(10 * 1024**3, master.stat().st_size * 4)
    if free - need < reserve:
        update(job_path, job, "WAIT_DISK",
               f"Require {(need+reserve)/1024**3:.1f} GiB free; have {free/1024**3:.1f} GiB")
        return
    update(job_path, job, "PROCESSING", "AI enhancement, then ASS burn-in")
    ass.parent.mkdir(parents=True, exist_ok=True)
    from kwjd import cmd_make_ass
    from argparse import Namespace
    cmd_make_ass(Namespace(srt=str(srt), template=str(style), output=str(ass)))
    src = probe(master)
    audio_streams = [item for item in src["streams"] if item["codec_type"] == "audio"]
    if not audio_streams:
        raise ValueError("Bilibili Hi-Res final requires a mixed audio track in the master")
    audio_cfg = PROJECT["audio"]
    # Never fabricate new frequency detail: stay at 48 kHz unless source >= 96 kHz.
    original_rate = int(audio_streams[0].get("sample_rate", 0) or 0)
    final_rate = (original_rate if original_rate >= audio_cfg["preserve_native_rate_if_at_least"]
                  else audio_cfg["minimum_sample_rate"])
    # Contract: enhance.sh INPUT OUTPUT EPISODE; output must be 1080p60 and duration-preserving.
    if not ready(enhanced):
        code = command([str(BACKEND), str(master), str(enhanced), ep], log)
        if code:
            raise RuntimeError(f"AI backend failed ({code}); see {log}")
    enh = probe(enhanced)
    vs = video_stream(enh)
    fps = vs["avg_frame_rate"].split("/")
    actual_fps = float(fps[0]) / float(fps[1])
    if vs.get("width") != 1920 or vs.get("height") != 1080 or abs(actual_fps - 60) > 0.05:
        raise ValueError("AI backend output is not 1920x1080 at 60 FPS")
    if abs(duration(src) - duration(enh)) > 0.5:
        raise ValueError("AI backend changed the running time by more than 0.5 seconds")
    final.parent.mkdir(parents=True, exist_ok=True)
    temp = final.with_name(final.stem + ".partial" + final.suffix)
    fonts = POST / "styles" / "fonts"
    # filenames are built from the episode ID; escape FFmpeg filter metacharacters.
    ass_str = str(ass).replace("\\", "/").replace(":", r"\:")
    font_str = str(fonts).replace("\\", "/").replace(":", r"\:")
    vf = f"ass=filename='{ass_str}':fontsdir='{font_str}'"
    code = command(["ffmpeg", "-hide_banner", "-y", "-nostdin", "-v", "warning",
                    "-i", str(enhanced), "-i", str(master),
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-vf", vf, "-c:v", "hevc_nvenc", "-preset", "p6",
                    "-rc", "vbr", "-cq", "18", "-b:v", "0", "-pix_fmt", "yuv420p10le",
                    "-c:a", "flac", "-sample_fmt", "s32",
                    "-bits_per_raw_sample", "24", "-ar", str(final_rate),
                    "-shortest", str(temp)], log)
    if code:
        raise RuntimeError(f"ASS burn-in/encode failed ({code}); see {log}")
    out = probe(temp)
    v = video_stream(out)
    if v["codec_name"] != "hevc" or v["width"] != 1920 or v["height"] != 1080:
        raise ValueError("Final does not match codec/resolution requirements")
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
    digest = hashlib.sha256()
    with temp.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    os.replace(temp, final)
    report = {
        "episode": ep, "completed_at": now(), "sha256": digest.hexdigest(),
        "master_duration": duration(src), "output_duration": duration(out),
        "final_path": str(final), "ai_backend": str(BACKEND),
        "audio_preset": "bilibili_hires", "audio_codec": "flac",
        "audio_bits_per_sample": 24, "audio_sample_rate": final_rate,
        "audio_source_sample_rate": original_rate,
        "audio_note": "High-spec lossless output cannot recover source details; Bilibili Hi-Res visibility depends on platform submission eligibility.",
        "note": "Technical checks only; user must approve video edge quality and audio.",
    }
    write_json(POST / "reports" / f"{ep}.json", report)
    update(job_path, job, "READY", "Final passed structural checks; download with SHA-256 validation")
    if not PROJECT.get("keep_enhanced", False):
        enhanced.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Try one queued/waiting job then exit")
    parser.add_argument("--loop", action="store_true", help="Long-running queue poll")
    parser.add_argument("--poll", type=int, default=90)
    args = parser.parse_args()
    if not args.once and not args.loop:
        parser.error("Choose --once or --loop")
    (POST / "jobs").mkdir(parents=True, exist_ok=True)
    lockfile = POST / "jobs" / ".worker.lock"
    with lockfile.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another Kwjd worker owns this queue")
        while True:
            jobs = sorted(path for path in (POST / "jobs").glob(f"{PROJECT['season']}E*.json")
                          if EPISODE_RE.fullmatch(path.stem))
            for file in jobs:
                try:
                    job = json.loads(file.read_text(encoding="utf-8-sig"))
                    if job.get("state") in ("QUEUED", "WAIT_MASTER", "WAIT_SUBTITLE",
                                            "WAIT_STYLE", "WAIT_BACKEND", "WAIT_DISK"):
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
