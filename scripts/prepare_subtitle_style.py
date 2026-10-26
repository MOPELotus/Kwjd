#!/usr/bin/env python3
"""Create a reusable MiSans ASS style and real-master preview frames.

Requires fontTools. Font binaries and subtitle/media contents stay outside Git.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kwjd import cmd_make_ass, read_srt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--font', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--master', type=Path, required=True)
    p.add_argument('--episode', default='S01E49')
    args = p.parse_args()
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    styles = args.root / 'styles'
    fonts = styles / 'fonts'
    fonts.mkdir(parents=True, exist_ok=True)
    static = fonts / 'MiSans-Subtitle-Demibold.ttf'
    if not static.exists():
        font = TTFont(args.font)
        instance = next(i for i in font['fvar'].instances
                        if font['name'].getDebugName(i.subfamilyNameID) == 'Demibold')
        font = instantiateVariableFont(font, instance.coordinates, inplace=False)
        # Unique family prevents a platform font or variable default substituting.
        names = {1: 'MiSans Subtitle', 2: 'Regular', 3: 'MiSansSubtitle-Demibold',
                 4: 'MiSans Subtitle Demibold', 6: 'MiSansSubtitle-Demibold',
                 16: 'MiSans Subtitle', 17: 'Regular'}
        for name_id, value in names.items():
            font['name'].removeNames(nameID=name_id)
            font['name'].setName(value, name_id, 3, 1, 0x409)
        font.save(static)
    template = styles / 'template.ass'
    if template.exists():
        raise FileExistsError(f'Refusing to overwrite existing style: {template}')
    template.write_text('''[Script Info]
Title: MiSans readable 1080p subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,MiSans Subtitle,52,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,2.8,0.8,2,90,90,54,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
''', encoding='utf-8')
    ass = args.root / 'subtitles' / f'{args.episode}.styled.ass'
    srt = args.root / 'subtitles' / f'{args.episode}.zh.srt'
    cmd_make_ass(argparse.Namespace(srt=str(srt), template=str(template), output=str(ass)))
    cues = read_srt(srt)
    selected = [cues[len(cues)//6], max(cues, key=lambda c: len(c[2])),
                max(cues, key=lambda c: ('\n' in c[2], len(c[2]))), cues[-30]]
    preview = styles / 'preview'
    preview.mkdir(exist_ok=True)
    receipt = []
    # Run from the data root to avoid drive-colon escaping in libass options.
    for index, (start, end, text) in enumerate(selected, 1):
        time = (start + end) / 2000
        output = preview / f'{args.episode}-style-{index}.png'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'info', '-nostdin', '-n',
                        '-ss', str(time), '-copyts', '-i', str(args.master),
                        '-vf', f'ass=subtitles/{ass.name}:fontsdir=styles/fonts',
                        '-frames:v', '1', '-update', '1', str(output)],
                       cwd=args.root, check=True)
        receipt.append({'time_seconds': time, 'image': str(output), 'text': text})
    (preview / 'samples.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Created {template}; font {static}; styled ASS {ass}', flush=True)


if __name__ == '__main__':
    main()
