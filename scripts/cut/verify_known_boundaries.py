"""Verify short copied head/tail windows of every frozen source; never full renders."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import season_cut as base
from freeze_known_cuts import validate_catalog


def packets(path):
    return json.loads(base.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_packets", "-show_entries", "packet=pts_time,duration_time", "-of", "json", path]))["packets"]


def check(entry, scan, output, catalog_sha):
    eid = entry["episode"]
    dest = output / eid
    dest.mkdir(parents=True, exist_ok=True)
    receipt_path = dest / "boundary-verification.json"
    if receipt_path.exists():
        old = base.read(receipt_path)
        if old["catalog_sha256"] != catalog_sha or old["source_sha256"] != entry["source_sha256"]:
            raise ValueError("Existing boundary verification belongs to different input/catalog")
        return old
    source = Path(scan["source"])
    stat = source.stat()
    if (stat.st_size != entry["source_bytes"] or stat.st_mtime_ns != scan["source_mtime_ns"]
            or entry["source_sha256"] != scan["source_sha256"]):
        raise ValueError("Source changed after complete SHA survey")
    opening = entry["opening_seconds"]
    end = entry["first_forbidden_seconds"] - .75
    head, tail = dest / "head.mp4", dest / "tail.mp4"
    for path in (head, tail):
        if path.exists():
            raise ValueError("Unfinished prior verification; retain it and use a fresh output directory")
    base.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{opening+.002:.6f}",
              "-i", source, "-t", "3", "-map", "0", "-c", "copy",
              "-avoid_negative_ts", "make_zero", "-n", head])
    head_hash = base.frame_hash(head)
    if head_hash != base.frame_hash(source, opening):
        raise ValueError("Opening keyframe copy mismatch")
    keys = json.loads(base.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-read_intervals", f"{max(0,end-20)}%+25", "-skip_frame", "nokey", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", source]))["frames"]
    choices = [float(f["best_effort_timestamp_time"]) for f in keys
               if "best_effort_timestamp_time" in f and float(f["best_effort_timestamp_time"]) <= end-3]
    if not choices:
        raise ValueError("No tail verification keyframe")
    tail_start = max(choices)
    base.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{tail_start+.002:.6f}",
              "-i", source, "-t", f"{end-tail_start:.6f}", "-map", "0", "-c", "copy",
              "-avoid_negative_ts", "make_zero", "-n", tail])
    times = packets(tail)
    first = min(float(p["pts_time"]) for p in times)
    last = max(float(p["pts_time"])+float(p.get("duration_time", 0)) for p in times)
    actual_end = tail_start + last-first
    if not entry["keep_through_seconds"] <= actual_end <= entry["first_forbidden_seconds"] - .25:
        raise ValueError("Actual tail window crosses safe end or loses required closing")
    for path in (head, tail):
        base.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", path,
                  "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"])
    if (source.stat().st_size, source.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        raise ValueError("Source changed during boundary verification")
    receipt = dict(episode=eid, catalog_sha256=catalog_sha, source_sha256=entry["source_sha256"],
                   source_sha_scope="Complete source SHA from preceding immutable survey; size/mtime rechecked here",
                   scope="Three-second copied head and short copied tail only; not full programme decode/playback",
                   actual_opening_seconds=opening, actual_tail_source_end_seconds=actual_end,
                   head_first_frame_md5=head_hash.decode("ascii"),
                   head_sha256=base.digest(head), tail_sha256=base.digest(tail),
                   first_frame_matches=True, tail_packets_in_bounds=True,
                   boundary_av_decode_passed=True)
    base.save_new(receipt_path, receipt)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--audit-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    a = p.parse_args()
    catalog = base.read(a.catalog)
    validate_catalog(catalog)
    sha = base.digest(a.catalog)
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(check, e,
            base.read(a.audit_root / "episodes" / e["episode"] / "scan.json"), a.output, sha): e["episode"]
            for e in catalog["entries"]}
        for f in as_completed(futures):
            eid = futures[f]
            try:
                results.append(f.result())
                print(f"PASS {eid} {len(results)}/260", flush=True)
            except Exception as exc:
                errors.append(dict(episode=eid, error=str(exc)))
                print(f"FAIL {eid}: {exc}", flush=True)
    base.save_new(a.output / "summary.json", dict(catalog_sha256=sha, passed=len(results),
                 failed=errors, scope="Copied boundary windows only; not full programme decode"))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
