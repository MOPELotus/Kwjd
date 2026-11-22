"""Copy consecutive backgrounds as one virtual source; never transcode a join.

A chapter crossing a source boundary gets two files and an FCPXML spine for
DaVinci. Different source frame/audio rates remain native in their own files.
"""
import argparse
import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from split_background import probe


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def allocate(durations, sources):
    if sum(durations) > sum(sources) + 1e-6:
        raise ValueError('Backgrounds shorter than programs')
    index, offset, result = 0, 0.0, []
    for duration in durations:
        remaining, parts = duration, []
        while remaining > 1e-6:
            if sources[index] - offset < 1e-6:
                index, offset = index + 1, 0.0
            length = min(remaining, sources[index] - offset)
            parts.append({'source_index': index, 'start_seconds': offset,
                          'target_seconds': length})
            offset += length
            remaining -= length
        result.append(parts)
    return result


def write_timeline(path, episode, parts, target, fps=25):
    root = ET.Element('fcpxml', version='1.9')
    resources = ET.SubElement(root, 'resources')
    ET.SubElement(resources, 'format', id='timelineFormat', name='FFVideoFormat1080p25',
                  frameDuration=f'1/{fps}s', width='1920', height='1080')
    for i, part in enumerate(parts):
        info = part['media']
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        native = round(parse_rate(video['avg_frame_rate']))
        ET.SubElement(resources, 'format', id=f'format{i}',
                      frameDuration=f'1/{native}s', width=str(video['width']), height=str(video['height']))
        asset = ET.SubElement(resources, 'asset', id=f'asset{i}', name=Path(part['output']).name,
                              start='0s', duration=f"{math.floor(part['actual_seconds']*1000000)}/1000000s",
                              hasVideo='1', hasAudio='1', format=f'format{i}',
                              audioSources='1', audioChannels=str(audio['channels']),
                              audioRate=str(audio['sample_rate']))
        ET.SubElement(asset, 'media-rep', kind='original-media', src=Path(part['output']).as_uri())
    event = ET.SubElement(ET.SubElement(root, 'library'), 'event', name=episode+' background')
    project = ET.SubElement(event, 'project', name=episode+' background joined')
    total_frames = round(target * fps)
    sequence = ET.SubElement(project, 'sequence', format='timelineFormat',
                             duration=f'{total_frames}/{fps}s', tcStart='0s',
                             tcFormat='NDF', audioLayout='stereo', audioRate='48k')
    spine = ET.SubElement(sequence, 'spine')
    cursor = 0
    for i, part in enumerate(parts):
        frames = round(part['target_seconds']*fps) if i < len(parts)-1 else total_frames-cursor
        ET.SubElement(spine, 'asset-clip', ref=f'asset{i}', name=Path(part['output']).name,
                      offset=f'{cursor}/{fps}s', start='0s', duration=f'{frames}/{fps}s')
        cursor += frames
    ET.indent(root)
    with path.open('xb') as stream:
        stream.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
        stream.write(ET.tostring(root, encoding='utf-8'))


def parse_rate(value):
    numerator, denominator = value.split('/')
    return float(numerator)/float(denominator)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--videos', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--season', required=True)
    parser.add_argument('--padding-seconds', type=float, default=2)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.padding_seconds < 0:
        parser.error('Negative padding')
    sources = [p.resolve(strict=True) for p in args.source]
    infos = [probe(p) for p in sources]
    source_durations = [float(i['format']['duration']) for i in infos]
    pattern = re.compile(re.escape(args.season)+r'E\d{2,3}(?!\d)')
    videos = sorted(p for p in args.videos.iterdir() if p.suffix.lower()=='.mp4' and pattern.fullmatch(p.stem))
    if not videos:
        raise ValueError('No exact episode filenames')
    durations = [float(probe(p)['format']['duration']) for p in videos]
    allocations = allocate(durations, source_durations)
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError('Preserve existing background directory: '+str(output))
    plan = []
    for video, duration, parts in zip(videos, durations, allocations):
        for i, part in enumerate(parts):
            suffix = f'-part{i+1:02}' if len(parts)>1 else ''
            part['output'] = str(output/f'{video.stem}-background{suffix}.mp4')
            source_index = part['source_index']
            part['copy_seconds'] = min(part['target_seconds']+args.padding_seconds,
                                       source_durations[source_index]-part['start_seconds'])
        plan.append({'episode':video.stem, 'reference':str(video.resolve()),
                     'target_seconds':duration, 'parts':parts})
    print(json.dumps(plan, ensure_ascii=True, indent=2), flush=True)
    if not args.execute:
        return
    output.mkdir(parents=True)
    report = {'mode':'stream-copy', 'order':'sources form one continuous virtual source',
              'sources':[{'path':str(p),'sha256':digest(p),'duration_seconds':d}
                         for p,d in zip(sources,source_durations)], 'chapters':plan}
    (output/'recovery-plan.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for chapter in plan:
        for part in chapter['parts']:
            destination = Path(part['output'])
            partial = destination.with_suffix('.partial.mp4')
            source_index = part['source_index']
            subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-ss',str(part['start_seconds']),
                            '-i',str(sources[source_index]),'-t',str(part['copy_seconds']),
                            '-map','0:V:0','-map','0:a:0','-c','copy','-map_metadata','-1',str(partial)],check=True)
            media = probe(partial)
            actual = float(media['format']['duration'])
            if actual < part['target_seconds']-0.1 or actual > part['copy_seconds']+5:
                raise ValueError('Unexpected copied duration: '+str(partial))
            selected = [s for s in infos[source_index]['streams'] if s['codec_type'] in ('audio','video')]
            for original, copied in zip(selected,media['streams'],strict=True):
                for key in ('codec_name','width','height','sample_rate','channels'):
                    if original.get(key) != copied.get(key):
                        raise ValueError('Stream changed: '+key)
            for seek in (['-ss','0'],['-sseof','-2']):
                subprocess.run(['ffmpeg','-v','error','-xerror','-nostdin',*seek,'-i',str(partial),
                                '-t','2','-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
            part.update(actual_seconds=actual, output_sha256=digest(partial),
                        technical_checked=True, media=media)
            partial.rename(destination)
            (destination.with_suffix('.mp4.receipt.json')).write_text(
                json.dumps(part,ensure_ascii=False,indent=2),encoding='utf-8')
            print('CHECKED '+destination.name,flush=True)
        if len(chapter['parts'])>1:
            timeline = output/(chapter['episode']+'-background-joined.fcpxml')
            write_timeline(timeline,chapter['episode'],chapter['parts'],chapter['target_seconds'])
            chapter['joined_timeline'] = str(timeline)
    (output/'background-split.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('ALL BACKGROUND PARTS CHECKED',flush=True)


if __name__=='__main__':
    main()
