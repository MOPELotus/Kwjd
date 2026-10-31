"""Audit the finite four-season source library and propose source-bound cut points.

This scanner never cuts media or approves its own proposals. Frozen catalog entries
are published only after the generated boundary sheets have actually been reviewed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import subprocess
import time

import cv2
import numpy as np

import season_cut as base

SCAN_VERSION = 1
EXPECTED = {"S01": 58, "S02": 104, "S03": 46, "S04": 52}
OPENING = {"S01": 30.0, "S02": 30.4, "S03": 36.80, "S04": 36.96}
REFERENCES = {"S02": ("S02E01", 2036.0), "S03": ("S03E20", 3579.0),
              "S04": ("S04E33", 3660.0), "membership": ("S03E46", 3510.0)}


def raw_frames(source, start, duration=None, fps=None):
    args = ["ffmpeg", "-v", "error", "-nostdin", "-threads", "1", "-ss", str(start), "-i", str(source)]
    if duration is not None:
        args += ["-t", str(duration)]
    else:
        args += ["-frames:v", "1"]
    vf = (f"fps={fps}," if fps else "") + "scale=160:90"
    args += ["-vf", vf, "-pix_fmt", "gray", "-threads", "1", "-f", "rawvideo", "-"]
    raw = base.run(args)
    if len(raw) % 14400:
        raise ValueError("Incomplete raw frame")
    return np.frombuffer(raw, np.uint8).reshape(-1, 90, 160)


def roi(frame, kind):
    return frame[20:75, 45:115] if kind != "membership" else frame[30:65, 50:110]


def match_scores(frames, reference, kind):
    return np.array([float(cv2.matchTemplate(roi(f, kind), reference,
                                           cv2.TM_CCOEFF_NORMED)[0, 0]) for f in frames])


def first_run(values, threshold, count):
    length = 0
    for i, value in enumerate(values):
        length = length + 1 if value >= threshold else 0
        if length >= count:
            return i - count + 1
    return None


def discover(roots):
    files = {}
    for season, root in roots.items():
        for source in root.glob("*.mp4"):
            match = re.match(r"^坑王驾到" + season + r"E(\d{2,3})\.", source.name)
            if match:
                eid = season + f"E{int(match[1]):02d}"
                if eid in files:
                    raise ValueError(f"Duplicate source: {eid}")
                files[eid] = source.resolve()
        expected = {season + f"E{i:02d}" for i in range(1, EXPECTED[season] + 1)}
        if expected != {eid for eid in files if eid.startswith(season)}:
            raise ValueError(f"Missing/unexpected episodes for {season}")
    return files


def opening_keyframes(source, season):
    data = json.loads(base.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-read_intervals", "25%+30", "-skip_frame", "nokey", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", source]))
    keys = [float(f["best_effort_timestamp_time"]) for f in data["frames"]
            if "best_effort_timestamp_time" in f]
    candidates = [t for t in keys if t >= OPENING[season] - 1e-6]
    if not candidates:
        raise ValueError("Missing opening keyframe")
    return min(candidates), keys


def tail_candidate(source, season, duration, references):
    start = max(0, duration - 240)
    if season == "S01":
        # Full frame rate: a brief white transition can be missed at five fps.
        frames = raw_frames(source, start, duration=240, fps=25)
        flat = frames.reshape(len(frames), -1)
        near_white = ((flat.mean(axis=1) >= 245) &
                      (np.mean(flat >= 238, axis=1) >= .98) &
                      (flat.min(axis=1) >= 180))
        hits = np.flatnonzero(near_white)
        if len(hits):
            cue = start + float(hits[0]) / 25
            return dict(kind="white_flash", first_forbidden_seconds=cue,
                        evidence_seconds=cue, detector="full_rate_near_white",
                        matches=int(len(hits)), score=None)
    else:
        frames = raw_frames(source, start, duration=240, fps=5)
        scores = match_scores(frames, references[season], season)
        hit = first_run(scores, .80, 3)
        if hit is not None:
            anchor = start + hit / 5
            # Refine the central white bloom immediately before the stable animation.
            fine_start = anchor - 1.6
            fine = raw_frames(source, fine_start, duration=2.2, fps=25)
            white = np.mean(fine[:, 20:75, 45:115] >= 240, axis=(1, 2))
            baseline = float(np.median(white[:8]))
            bloom = np.flatnonzero(white >= max(.045, baseline + .025))
            if len(bloom) and bloom[0] >= 8:
                cue = fine_start + float(bloom[0]) / 25 - .12
                refinement = "white_bloom_minus_120ms"
            else:
                cue = anchor - .9
                refinement = "conservative_animation_lead_900ms"
            return dict(kind="logo_transition", first_forbidden_seconds=cue,
                        evidence_seconds=anchor + .8, detector="season_animation_ncc",
                        score=float(scores.max()), matches=int(np.sum(scores >= .8)),
                        anchor_seconds=anchor, refinement=refinement)
    # Terminal slate is distinct from a white flash or a flying logo.
    member_start = max(0, duration - 20)
    frames = raw_frames(source, member_start, duration=20, fps=10)
    scores = match_scores(frames, references["membership"], "membership")
    hit = first_run(scores, .80, 3)
    if hit is not None:
        cue = member_start + hit / 10
        return dict(kind="credits", first_forbidden_seconds=cue,
                    evidence_seconds=cue + .5, detector="terminal_membership_ncc",
                    score=float(scores.max()), matches=int(np.sum(scores >= .8)))
    return dict(kind="unresolved", detector="no_reliable_cue", first_forbidden_seconds=None)


def scan_one(eid, source, references, output):
    dest = output / "episodes" / eid
    dest.mkdir(parents=True, exist_ok=True)
    result_path = dest / "scan.json"
    if result_path.exists():
        result = base.read(result_path)
        stat = source.stat()
        if (result["scan_version"] == SCAN_VERSION and result["source_bytes"] == stat.st_size
                and result["source_mtime_ns"] == stat.st_mtime_ns):
            return result  # Resume this audit run; final cutter always rehashes the actual source.
        raise ValueError(f"Changed source/scan version needs a new audit directory: {eid}")
    before = source.stat()
    info = base.probe(source)
    duration = float(info["format"]["duration"])
    opening, keys = opening_keyframes(source, eid[:3])
    tail = tail_candidate(source, eid[:3], duration, references)
    source_sha = base.digest(source)
    if (source.stat().st_size, source.stat().st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ValueError(f"Source changed during scan: {eid}")
    times = {"head": opening}
    if tail["first_forbidden_seconds"] is not None:
        cue = tail["first_forbidden_seconds"]
        times.update(end=max(opening, cue - .85), before=max(opening, cue - 3),
                     marker=min(duration - .1, tail["evidence_seconds"]))
    else:
        times.update(end=duration - 1, before=duration - 8, marker=duration - .2)
    for name, second in times.items():
        base.extract_frame(source, second, dest / (name + ".jpg"))
    result = dict(scan_version=SCAN_VERSION, episode=eid, source=str(source),
                  source_name=source.name, source_sha256=source_sha,
                  source_bytes=before.st_size, source_mtime_ns=before.st_mtime_ns,
                  duration_seconds=duration, opening_seconds=opening,
                  opening_keyframes=keys, tail=tail, frame_seconds=times,
                  proposed_video_end_seconds=(tail["first_forbidden_seconds"] - .75
                                             if tail["first_forbidden_seconds"] else None),
                  boundary_reviewed=False)
    base.save_new(result_path, result)
    return result


def sheets(output, results):
    from PIL import Image, ImageDraw
    folder = output / "sheets"
    folder.mkdir(exist_ok=True)
    for season in EXPECTED:
        entries = [r for r in results if r["episode"].startswith(season)]
        for offset in range(0, len(entries), 12):
            group = entries[offset:offset+12]
            sheet = Image.new("RGB", (1280, len(group) * 114), "black")
            draw = ImageDraw.Draw(sheet)
            for row, item in enumerate(group):
                for col, name in enumerate(("head", "before", "end", "marker")):
                    with Image.open(output / "episodes" / item["episode"] / (name + ".jpg")) as im:
                        im.thumbnail((280, 90))
                        sheet.paste(im, (col * 320, row * 114 + 24))
                    caption = f'{item["episode"]} {name} {item["frame_seconds"][name]:.3f}'
                    draw.text((col * 320 + 2, row * 114 + 4), caption, fill="yellow")
            sheet.save(folder / f"{season}-{offset//12+1:02d}.jpg")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--season-dir", action="append", required=True, help="S01=D:/source-directory")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    a = p.parse_args()
    roots = {s: Path(root) for s, root in (x.split("=", 1) for x in a.season_dir)}
    if set(roots) != set(EXPECTED):
        raise ValueError("Provide all four season directories")
    files = discover(roots)
    a.output.mkdir(parents=True, exist_ok=True)
    references = {key: roi(raw_frames(files[eid], second)[0], key).copy()
                  for key, (eid, second) in REFERENCES.items()}
    results = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(scan_one, eid, source, references, a.output): eid
                   for eid, source in sorted(files.items())}
        for f in as_completed(futures):
            result = f.result()
            results.append(result)
            print(f'{len(results)}/260 {result["episode"]} head={result["opening_seconds"]:.3f} '
                  f'tail={result["tail"]["kind"]} {time.monotonic()-started:.1f}s', flush=True)
    results.sort(key=lambda r: r["episode"])
    sheets(a.output, results)
    summary = a.output / "summary.json"
    if not summary.exists():
        base.save_new(summary, {"scan_version": SCAN_VERSION, "entries": results,
                               "reference_recipes": REFERENCES,
                               "elapsed_seconds": time.monotonic() - started})
    print("Survey complete; boundary review is still required before freezing.", flush=True)


if __name__ == "__main__":
    main()
