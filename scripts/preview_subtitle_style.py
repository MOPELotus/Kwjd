#!/usr/bin/env python3
"""Burn the approved ASS onto short excerpts of a real master for player review."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--master', required=True, type=Path)
    p.add_argument('--episode', default='S01E49')
    args = p.parse_args()
    folder = args.root / 'styles' / 'preview'
    samples = json.loads((folder / 'samples.json').read_text(encoding='utf-8'))
    selected = list(dict.fromkeys(round(s['time_seconds'] - 4, 3) for s in samples))
    clips = []
    for index, start in enumerate(selected, 1):
        output = folder / f'{args.episode}-sample-{index}.mkv'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'info', '-nostdin', '-n',
                        '-ss', str(max(0, start)), '-copyts', '-i', str(args.master),
                        '-t', '12', '-map', '0:v:0', '-map', '0:a:0',
                        '-vf', f'ass=subtitles/{args.episode}.styled.ass:fontsdir=styles/fonts,setpts=PTS-STARTPTS',
                        '-af', 'asetpts=PTS-STARTPTS', '-c:v', 'h264_nvenc', '-preset', 'p6',
                        '-cq', '16', '-b:v', '0', '-pix_fmt', 'yuv420p',
                        '-c:a', 'pcm_s24le', str(output)], cwd=args.root, check=True)
        clips.append(output)
    listing = folder / 'concat.txt'
    listing.write_text(''.join(f"file '{clip.name}'\n" for clip in clips), encoding='utf-8')
    output = folder / f'{args.episode}-MiSans-preview.mkv'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-n',
                    '-f', 'concat', '-safe', '0', '-i', str(listing),
                    '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', str(output)], check=True)
    print(output, flush=True)


if __name__ == '__main__':
    main()
