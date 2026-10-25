#!/usr/bin/env python3
"""Kwjd: non-destructive inventory, adoption, subtitle validation and ASS generation.

No AI tools or background services are started by this program.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = json.loads((HERE / "config" / "project.json").read_text(encoding="utf-8"))
SEASON = PROJECT["season"]
BOOTSTRAP = set(PROJECT.get("bootstrap_episodes", []))
TIMING = re.compile(r"^(\d{2,}):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d{2,}):(\d\d):(\d\d)[,.](\d{3})(?:\s+.*)?$")
EPISODE_RE = re.compile(rf"{re.escape(SEASON)}E(\d{{2,3}})(?!\d)", re.IGNORECASE)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def episode(num: int) -> str:
    if not 1 <= num <= 999:
        raise ValueError(f"Invalid episode number: E{num} (expected 1..999)")
    return f"{SEASON}E{num:02d}"


def discover_episodes(role: str, root: Path) -> list[int]:
    """Inventory existing S01 assets; bootstrap makes existing E49-E55 visible without
    limiting discovery to them. A source directory with other shows is not traversed."""
    found = set(BOOTSTRAP)
    folders = ["videos", "manifests", "subtitles", "masters", "final"] if role == "windows" else [
        "input", "output", "post/incoming", "post/jobs", "post/final"]
    for folder in folders:
        base = root / folder
        if base.is_dir():
            for entry in base.iterdir():
                if entry.is_file():
                    match = EPISODE_RE.search(entry.name)
                    if match:
                        n = int(match.group(1))
                        if 1 <= n <= 999:
                            found.add(n)
    return sorted(found)


def root_for(role: str, value: str | None) -> Path:
    return Path(value).expanduser() if value else (Path("D:/qingqingwu") if role == "windows" else Path.home() / "qingqingwu")


def safe_find(base: Path, marker: str, suffixes: tuple[str, ...]) -> Path | None:
    if not base.is_dir():
        return None
    candidates = sorted(p for p in base.iterdir() if p.is_file()
                        and marker.lower() in p.name.lower()
                        and p.suffix.lower() in suffixes
                        and not p.name.endswith((".partial.mp4", ".uploading")))
    return candidates[0] if candidates else None


def info(path: Path | None, verified: bool = False) -> dict:
    if path is None or not path.is_file() or path.stat().st_size == 0:
        return {"status": "missing"}
    return {"status": "verified-by-existing-manifest" if verified else "detected-not-validated",
            "path": str(path), "bytes": path.stat().st_size}


def inventory(role: str, root: Path) -> dict:
    data = {"role": role, "root": str(root), "captured_at": stamp(), "episodes": {}}
    for num in discover_episodes(role, root):
        ep = episode(num)
        if role == "windows":
            cut = safe_find(root / "videos", ep, (".mp4", ".mkv", ".mov"))
            manifest = root / "manifests" / f"{ep}.json"
            valid_manifest = False
            if cut and manifest.is_file():
                try:
                    m = json.loads(manifest.read_text(encoding="utf-8-sig"))
                    valid_manifest = bool(m.get("VideoCodecCopied") and m.get("AudioCodecCopied")
                                          and m.get("Episode") == ep)
                except (ValueError, OSError):
                    pass
            data["episodes"][ep] = {
                "cut": info(cut, verified=valid_manifest),
                "cut_manifest": info(manifest),
                "ocr_srt": info(safe_find(root / "subtitles", ep, (".srt",))),
                "clean_master": info(safe_find(root / "masters", ep + ".clean-master", (".mp4", ".mkv", ".mov"))),
                "final": info(safe_find(root / "final", ep, (".mp4", ".mkv", ".mov"))),
            }
        else:
            original = root / "input" / f"{ep}.mp4"
            srt = root / "output" / f"{ep}.zh.srt"
            data["episodes"][ep] = {
                "ocr_input": info(original),
                "ocr_srt": info(srt),
                "clean_master": info(safe_find(root / "post" / "incoming", ep + ".clean-master", (".mp4", ".mkv", ".mov"))),
                "final": info(safe_find(root / "post" / "final", ep, (".mp4", ".mkv", ".mov"))),
            }
    style = root / "styles" / "template.ass" if role == "windows" else root / "post" / "styles" / "template.ass"
    data["style"] = info(style)
    return data


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def cmd_adopt(args: argparse.Namespace) -> int:
    root = root_for(args.role, args.root)
    if not root.exists():
        raise ValueError(f"Data root does not exist: {root}; clone under existing data root first")
    data = inventory(args.role, root)
    output = root / ".kwjd" / f"adoption-{args.role}.json"
    if output.exists() and not args.refresh:
        print(f"Adoption snapshot exists: {output}\nPass --refresh to update it. Live status is always available.")
        return 0
    atomic_json(output, data)
    print(f"Adopted inventory only (no jobs restarted, no media modified): {output}")
    return print_inventory(data)


def print_inventory(data: dict) -> int:
    print(f"Role={data['role']}  Root={data['root']}")
    for ep, stages in data["episodes"].items():
        s = " ".join(f"{key}:{'OK' if value['status'] != 'missing' else '--'}"
                     for key, value in stages.items())
        print(f"{ep} {s}")
    print(f"ASS style: {data['style']['status']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    data = inventory(args.role, root_for(args.role, args.root))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_inventory(data)
    return 0


def parse_time(parts: tuple[str, ...]) -> int:
    h, m, s, ms = map(int, parts)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def read_srt(path: Path) -> list[tuple[int, int, str]]:
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    segments = re.split(r"\n\s*\n", content.strip())
    cues: list[tuple[int, int, str]] = []
    for item in segments:
        lines = item.split("\n")
        if not lines or not lines[0].strip():
            continue
        if re.fullmatch(r"\d+", lines[0].strip()):
            lines.pop(0)
        if len(lines) < 2:
            raise ValueError(f"Malformed SRT block {len(cues) + 1}: {item[:100]!r}")
        m = TIMING.fullmatch(lines[0].strip())
        if not m:
            raise ValueError(f"Malformed SRT timestamp, block {len(cues) + 1}: {lines[0]!r}")
        start, end = parse_time(m.groups()[:4]), parse_time(m.groups()[4:])
        if start >= end:
            raise ValueError(f"Subtitle duration <= 0 in block {len(cues) + 1}")
        text = "\n".join(lines[1:]).strip()
        if not text:
            raise ValueError(f"Empty subtitle in block {len(cues) + 1}")
        cues.append((start, end, text))
    if not cues:
        raise ValueError(f"Subtitle is empty: {path}")
    for i in range(1, len(cues)):
        if cues[i][0] < cues[i - 1][0]:
            raise ValueError(f"Non-monotonic start time at item {i+1}")
    return cues


def cmd_validate_srt(args: argparse.Namespace) -> int:
    cues = read_srt(Path(args.file))
    overlaps = sum(cues[i][0] < cues[i - 1][1] for i in range(1, len(cues)))
    summary = {"file": args.file, "cues": len(cues), "overlaps": overlaps,
               "last_end_ms": cues[-1][1], "structurally_valid": True,
               "notice": "Structure only; this does not establish accuracy or OCR completeness."}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not overlaps or args.allow_overlaps else 2



def cmd_seal_srt(args: argparse.Namespace) -> int:
    if not args.approve:
        raise ValueError("Explicit --approve required; file existence is not OCR-review completion")
    file = Path(args.file)
    cues = read_srt(file)
    overlaps = sum(cues[i][0] < cues[i - 1][1] for i in range(1, len(cues)))
    if overlaps:
        raise ValueError(f"Refusing to seal SRT with {overlaps} overlapping entries")
    marker = Path(args.marker) if args.marker else file.with_name(file.name + ".ready.json")
    sha = hashlib.sha256(file.read_bytes()).hexdigest()
    if marker.exists():
        prior = json.loads(marker.read_text(encoding="utf-8"))
        if prior.get("sha256") == sha:
            print(f"Already sealed: {marker}")
            return 0
        raise ValueError(f"Existing seal differs: {marker}; inspect first, do not overwrite")
    atomic_json(marker, {"schema_version": 1, "source": file.name, "sha256": sha,
                         "bytes": file.stat().st_size, "cues": len(cues),
                         "reviewed": True, "completed_at": stamp()})
    print(f"Explicitly sealed reviewed OCR result: {marker}")
    return 0


def ass_time(milliseconds: int) -> str:
    cs = (milliseconds + 5) // 10
    hours, rest = divmod(cs, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, centiseconds = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def cmd_make_ass(args: argparse.Namespace) -> int:
    srt, template, output = Path(args.srt), Path(args.template), Path(args.output)
    cues = read_srt(srt)
    if not template.is_file():
        raise FileNotFoundError(f"Please create/approve an Aegisub ASS template first: {template}")
    template_text = template.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if "[V4+ Styles]" not in template_text or "[Script Info]" not in template_text:
        raise ValueError("ASS template must contain [Script Info] and [V4+ Styles]")
    # Keep the template's script metadata and styles; replace only Event records.
    prefix = re.split(r"(?im)^\[Events\]\s*$", template_text, maxsplit=1)[0].rstrip()
    events = ["[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for start, end, value in cues:
        value = value.replace("\\", r"\\").replace("\n", r"\N")
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{value}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(prefix + "\n\n" + "\n".join(events) + "\n", encoding="utf-8")
    print(f"{len(cues)} cues -> {output}; content and timing preserved (centisecond rounding in ASS)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = root_for(args.role, args.root)
    print(f"Role: {args.role}; data root: {root}")
    print("Available tools:", {k: bool(shutil.which(k)) for k in
          ("ffmpeg", "ffprobe", "ssh", "scp", "nvidia-smi", "python3", "pwsh")})
    if root.exists():
        d = shutil.disk_usage(root)
        print(f"Filesystem: {d.free/(1024**3):.1f} GiB free / {d.total/(1024**3):.1f} GiB")
        if d.free < PROJECT["minimum_free_gib"] * 1024**3:
            print("WARNING: Free space below configured reserve. Do not start AI jobs.")
    print("No installations or changes have been made.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name, func in (("adopt", cmd_adopt), ("status", cmd_status), ("doctor", cmd_doctor)):
        a = sub.add_parser(name)
        a.add_argument("--role", choices=("windows", "linux"), required=True)
        a.add_argument("--root", help="Existing data directory; never a new project media directory")
        if name == "adopt":
            a.add_argument("--refresh", action="store_true")
        if name == "status":
            a.add_argument("--json", action="store_true")
        a.set_defaults(func=func)
    v = sub.add_parser("validate-srt")
    v.add_argument("file")
    v.add_argument("--allow-overlaps", action="store_true")
    v.set_defaults(func=cmd_validate_srt)
    seal = sub.add_parser("seal-srt")
    seal.add_argument("--file", required=True)
    seal.add_argument("--marker")
    seal.add_argument("--approve", action="store_true")
    seal.set_defaults(func=cmd_seal_srt)
    a = sub.add_parser("make-ass")
    a.add_argument("--srt", required=True)
    a.add_argument("--template", required=True)
    a.add_argument("--output", required=True)
    a.set_defaults(func=cmd_make_ass)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
