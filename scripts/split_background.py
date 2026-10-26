#!/usr/bin/env python3
"""Split a background sequentially to match discovered episode durations.

Media stays outside Git. Copy mode preserves codecs and may round boundaries;
the local receipt records measured durations for use in the editing timeline.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path)], encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--videos", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--season", default="S01")
    parser.add_argument("--execute", action="store_true", help="Otherwise print plan only")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    pattern = re.compile(re.escape(args.season) + r"E(\d{2,3})(?!\d)", re.I)
    episodes = {}
    for path in args.videos.iterdir():
        match = pattern.search(path.name)
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".mov"} and match:
            number = int(match[1])
            if number in episodes:
                raise ValueError(f"Duplicate episode: {number}")
            episodes[number] = path
    if not episodes:
        raise ValueError("No episode videos found")
    plan = []
    offset = 0.0
    for number, path in sorted(episodes.items()):
        duration = float(probe(path)["format"]["duration"])
        output = args.output / f"E{number:02d}-background.mp4"
        if output.exists() or output.with_suffix(".partial.mp4").exists():
            raise FileExistsError(output)
        plan.append({"episode": f"{args.season}E{number:02d}", "reference": str(path),
                     "start_seconds": offset, "target_seconds": duration,
                     "output": str(output)})
        offset += duration
    source_info = probe(source)
    if offset > float(source_info["format"]["duration"]):
        raise ValueError("Background is shorter than the total episode duration")
    receipt = args.output / "background-split.json"
    if receipt.exists():
        raise FileExistsError(receipt)
    print(json.dumps(plan, ensure_ascii=True, indent=2), flush=True)
    if not args.execute:
        return
    args.output.mkdir(parents=True, exist_ok=True)
    source_codecs = [s["codec_name"] for s in source_info["streams"]
                     if s["codec_type"] in {"video", "audio"}
                     and not s.get("disposition", {}).get("attached_pic")]
    for item in plan:
        output = Path(item["output"])
        partial = output.with_suffix(".partial.mp4")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-ss", f"{item['start_seconds']:.6f}", "-i", str(source),
            "-t", f"{item['target_seconds']:.6f}", "-map", "0:V:0", "-map", "0:a:0",
            "-c", "copy", "-map_metadata", "-1", str(partial)], check=True)
        actual = probe(partial)
        codecs = [s["codec_name"] for s in actual["streams"]]
        item["actual_seconds"] = float(actual["format"]["duration"])
        item["duration_delta_seconds"] = item["actual_seconds"] - item["target_seconds"]
        if codecs != source_codecs or abs(item["duration_delta_seconds"]) > 5:
            raise ValueError(f"Output validation failed; inspect {partial}")
        # Decode the beginning to catch an unusable copied video boundary.
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(partial),
                        "-t", "2", "-map", "0:v:0", "-map", "0:a:0",
                        "-f", "null", "-"], check=True)
        partial.rename(output)
        if args.output.name == 'background' and args.videos.resolve() == (args.output.parent / 'videos').resolve():
            from episode_lifecycle import register_artifact
            register_artifact(args.output.parent, item['episode'], 'windows',
                              f'background/{output.name}')
        print(f"{item['episode']}: {item['actual_seconds']:.3f}s "
              f"(delta {item['duration_delta_seconds']:+.3f}s)", flush=True)
    report = {"source": str(source), "mode": "stream-copy", "clips": plan}
    temporary = receipt.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.rename(receipt)


if __name__ == "__main__":
    main()
