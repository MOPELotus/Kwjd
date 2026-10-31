"""Source-bound, reviewed stream-copy cuts for S01--S04 (no upload/production)."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import uuid

REPO = Path(__file__).resolve().parents[2]


def run(args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.PIPE)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".writing")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)  # Atomic complete JSON publication, never replace prior evidence.
    finally:
        temp.unlink(missing_ok=True)


def probe(source):
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                           "-of", "json", source]))


def profile_for(source):
    match = re.match(r"^坑王驾到(S0[1-4])E(\d{2,3})\.", Path(source).name)
    if not match:
        raise ValueError("Source must have its original S01--S04 episode filename")
    profile_path = REPO / "config" / "seasons" / (match[1] + ".json")
    profile = read(profile_path)
    return match[1] + "E" + match[2], profile, digest(profile_path)


def extract_frame(source, seconds, output):
    # Individual accurate seeks, not fps/tile sampling whose labels can be offset.
    run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{seconds:.6f}", "-i", source,
         "-frames:v", "1", "-vf", "scale=640:-2", "-n", output])


def inspect(source, output, times=None):
    source = Path(source).resolve(strict=True)
    episode, profile, profile_sha = profile_for(source)
    info = probe(source)
    duration = float(info["format"]["duration"])
    policy = profile["cut_review"]
    if times is None:
        a, b = policy["opening_search_seconds"]
        times = [a + n * 0.5 for n in range(int((b - a) * 2) + 1)]
        times += [max(0, duration - 240) + n * 15 for n in range(16)]
        times += [max(0, duration - 0.25)]
    times = sorted(set(float(t) for t in times))
    if not times or any(not math.isfinite(t) or not 0 <= t < duration for t in times):
        raise ValueError("Frame times must be finite and inside the source")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    stat = source.stat()
    source_sha = digest(source)
    frames = []
    for index, second in enumerate(times):
        name = f"{index:03d}-{second:.3f}.jpg"
        extract_frame(source, second, output / name)
        frames.append({"requested_source_seconds": second, "file": name,
                       "sha256": digest(output / name)})
    if (source.stat().st_size, source.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise ValueError("Source changed during inspection")
    audit = {"schema_version": 1, "episode": episode, "source": str(source),
             "source_sha256": source_sha, "source_bytes": stat.st_size,
             "profile_sha256": profile_sha, "duration_seconds": duration,
             "sampling": "individually sought frames; requested time, within one source frame",
             "frames": frames}
    save_new(output / "audit.json", audit)
    # Contact sheet is review convenience; JPEGs and their requested times remain authoritative.
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (1280, math.ceil(len(frames) / 4) * 204), "black")
    draw = ImageDraw.Draw(sheet)
    for i, frame in enumerate(frames):
        with Image.open(output / frame["file"]) as im:
            im.thumbnail((320, 180))
            x, y = i % 4 * 320, i // 4 * 204
            sheet.paste(im, (x, y + 24))
            draw.text((x + 4, y + 4), f'{frame["requested_source_seconds"]:.3f}s', fill="yellow")
    sheet.save(output / "contact.jpg")
    return audit


def validate_bounds(first_live, keep_through, first_forbidden, duration, safety):
    values = [first_live, keep_through, first_forbidden, duration, safety]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite boundary")
    # Reserve extra video reorder allowance; the actual packet bound is checked after copy.
    end = first_forbidden - safety - 0.5
    if not (0 <= first_live < keep_through < end < first_forbidden <= duration):
        raise ValueError("Boundaries cannot preserve required closing shot before forbidden material")
    return end


def make_plan(audit_path, first_live, keep_through, first_forbidden, kind, note, output):
    audit_path = Path(audit_path).resolve()
    audit = read(audit_path)
    _, profile, profile_sha = profile_for(audit["source"])
    if profile_sha != audit["profile_sha256"]:
        raise ValueError("Stale profile; inspect again")
    if not note.strip():
        raise ValueError("Record the actual visual review, including missing entrance/wide shot")
    policy = profile["cut_review"]
    validate_bounds(first_live, keep_through, first_forbidden,
                    audit["duration_seconds"], policy["tail_safety_seconds"])
    if kind not in policy["tail_kinds"]:
        raise ValueError("Unsupported tail kind")
    if kind == "clean_eof" and abs(first_forbidden - audit["duration_seconds"]) > 0.05:
        raise ValueError("clean_eof must refer to the real source end")
    # A coarse survey alone cannot approve an exact boundary: inspect nearby frames first.
    for value in [first_live, keep_through, first_forbidden]:
        if min(abs(f["requested_source_seconds"] - value) for f in audit["frames"]) > 1.0:
            raise ValueError("Inspect frames within 1 second of every reviewed boundary")
    for frame in audit["frames"]:
        if digest(audit_path.parent / frame["file"]) != frame["sha256"]:
            raise ValueError("Review frame changed")
    plan = {"schema_version": 1, "episode": audit["episode"], "source": audit["source"],
            "source_sha256": audit["source_sha256"], "profile_sha256": profile_sha,
            "audit": str(audit_path), "audit_sha256": digest(audit_path),
            "first_live_seconds": first_live, "keep_through_seconds": keep_through,
            "first_forbidden_seconds": first_forbidden, "tail_kind": kind,
            "visual_review_note": note, "duration_seconds": audit["duration_seconds"]}
    save_new(output, plan)
    return plan


def frame_hash(source, seconds=0):
    data = run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{seconds:.6f}",
                "-i", source, "-map", "0:v:0", "-frames:v", "1", "-f", "framemd5", "-"])
    return next(line.split(b",")[-1].strip() for line in data.splitlines()
                if line and not line.startswith(b"#"))


def cut(plan_path, output):
    plan = read(plan_path)
    source = Path(plan["source"]).resolve(strict=True)
    output = Path(output).resolve()
    episode, profile, profile_sha = profile_for(source)
    if episode != plan["episode"] or profile_sha != plan["profile_sha256"]:
        raise ValueError("Episode/profile mismatch")
    if digest(source) != plan["source_sha256"] or digest(plan["audit"]) != plan["audit_sha256"]:
        raise ValueError("Source or audit changed; review again")
    policy = profile["cut_review"]
    duration = float(probe(source)["format"]["duration"])
    if abs(duration - plan["duration_seconds"]) > 0.05:
        raise ValueError("Source duration changed")
    end = validate_bounds(plan["first_live_seconds"], plan["keep_through_seconds"],
                          plan["first_forbidden_seconds"], duration, policy["tail_safety_seconds"])
    if plan["tail_kind"] not in policy["tail_kinds"] or not plan["visual_review_note"].strip():
        raise ValueError("Invalid review")
    if (output.exists() or output.with_suffix(output.suffix + ".cut.json").exists()
            or output == source or output.suffix.lower() not in (".mp4", ".mkv")):
        raise ValueError("Output must be a new MP4/MKV path")
    start = plan["first_live_seconds"]
    frames = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-read_intervals", f"{max(0,start-5)}%+20", "-skip_frame", "nokey",
        "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", source]))
    keys = [float(f["best_effort_timestamp_time"]) for f in frames["frames"]
            if "best_effort_timestamp_time" in f and float(f["best_effort_timestamp_time"]) >= start]
    if not keys or min(keys) - start > policy["max_opening_keyframe_skip_seconds"]:
        raise ValueError("No nearby inward opening keyframe; review source GOP")
    start = min(keys)
    if start >= plan["keep_through_seconds"]:
        raise ValueError("Opening seek reaches closing content")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".cut.lock")
    temp = output.with_name(output.stem + "." + uuid.uuid4().hex + ".partial" + output.suffix)
    with lock.open("x") as lock_stream:
        try:
            run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start+0.002:.6f}",
                 "-i", source, "-t", f"{end-start:.6f}", "-map", "0", "-c", "copy",
                 "-avoid_negative_ts", "make_zero", "-n", temp])
            if frame_hash(source, start) != frame_hash(temp):
                raise ValueError("First decoded frame differs from selected source keyframe")
            before, after = probe(source), probe(temp)
            fields = ("codec_type", "codec_name", "width", "height", "sample_rate", "channels")
            if [[s.get(k) for k in fields] for s in before["streams"]] != [
                    [s.get(k) for k in fields] for s in after["streams"]]:
                raise ValueError("Stream-copy codec/track/dimension check failed")
            packets = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_packets", "-show_entries", "packet=pts_time,duration_time", "-of", "json", temp]))["packets"]
            pts = [float(p["pts_time"]) for p in packets]
            last = max(float(p["pts_time"]) + float(p.get("duration_time", 0)) for p in packets)
            source_end = start + last - min(pts)
            if not plan["keep_through_seconds"] <= source_end <= plan["first_forbidden_seconds"] - policy["tail_safety_seconds"]:
                raise ValueError("Copied video does not preserve closing shot / leaks past safe tail")
            out_duration = float(after["format"]["duration"])
            for second in (0, max(0, out_duration - 2)):
                run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-ss", str(second),
                     "-i", temp, "-t", "2", "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"])
            receipt = {"schema_version": 1, "episode": episode, "source_sha256": plan["source_sha256"],
                "plan_sha256": digest(plan_path), "output": str(output), "output_sha256": digest(temp),
                "stream_copy": True, "actual_start_seconds": start,
                "video_source_end_seconds": source_end, "duration_seconds": out_duration,
                "technical_checked": True, "output_visual_review_pending": True,
                "decode_scope": "first and last two seconds; not full playback"}
            # Hard-link publication cannot overwrite an existing file, including on Unix.
            os.link(temp, output)
            temp.unlink()
            save_new(output.with_suffix(output.suffix + ".cut.json"), receipt)
            return receipt
        finally:
            # Failed media is retained for diagnosis, never mistaken for a published cut.
            lock_stream.close()
            lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--times", type=float, nargs="+")
    p = sub.add_parser("plan")
    p.add_argument("--audit", required=True)
    for flag in ("first-live", "keep-through", "first-forbidden"):
        p.add_argument("--" + flag, type=float, required=True)
    p.add_argument("--tail-kind", choices=["logo_transition", "preview", "credits", "clean_eof"], required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("cut")
    p.add_argument("--plan", required=True)
    p.add_argument("--output", required=True)
    a = parser.parse_args()
    if a.command == "inspect":
        result = inspect(a.source, a.output, a.times)
    elif a.command == "plan":
        result = make_plan(a.audit, a.first_live, a.keep_through, a.first_forbidden,
                           a.tail_kind, a.note, a.output)
    else:
        result = cut(a.plan, a.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
