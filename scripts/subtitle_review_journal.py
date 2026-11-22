"""Rebuild an explicitly partial working SRT from pixel-review decisions.

Unreviewed cues remain verbatim. This tool never publishes, seals or approves.
Decision files are local data and must not be committed with subtitle contents.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kwjd import read_srt


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf8')
    temporary.replace(path)


def stamp(ms):
    seconds, milli = divmod(ms, 1000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f'{hour:02d}:{minute:02d}:{second:02d},{milli:03d}'


def rebuild(root, episode):
    if episode not in [f'S03E{i:02d}' for i in range(2, 15)]:
        raise ValueError('Episode outside review scope')
    review = root / 'ocr/subtitle-review'
    draft = root / 'output/drafts' / (episode + '.zh.srt')
    original = read_srt(draft)
    decisions = {}
    indexes = []
    for path in sorted((review / 'decisions' / episode).glob('*.json')):
        data = json.loads(path.read_text())
        if data['draft_sha256'] != sha(draft) or not data['model_review_session']:
            raise ValueError('Draft identity or actual session missing')
        for index in data['evidence']:
            evidence = Path(index['path']).resolve(strict=True)
            if not evidence.is_relative_to(review.resolve()) or sha(evidence) != index['sha256']:
                raise ValueError('Evidence identity/path differs')
        indexes.append({'path': str(path), 'sha256': sha(path)})
        for row in data['rows']:
            cue = row['original_cue_id']
            if cue in decisions or not 1 <= cue <= len(original):
                raise ValueError('Duplicate/out-of-range original cue')
            if row['original_text'] != original[cue - 1][2] or not row['frames_viewed']:
                raise ValueError('Original mismatch or no viewed frames')
            for segment in row['segments']:
                if segment['end_ms'] <= segment['start_ms'] or not segment['text'].strip():
                    raise ValueError('Invalid explicit replacement')
            decisions[cue] = row
    revised = []
    mapping = []
    for cue, (start, end, text) in enumerate(original, 1):
        segments = decisions[cue]['segments'] if cue in decisions else [dict(start_ms=start, end_ms=end, text=text)]
        for segment in segments:
            revised.append(segment)
            mapping.append({'working_cue_id': len(revised), 'original_cue_id': cue,
                            'pixel_review_recorded': cue in decisions})
    output = root / 'output/reviewing' / (episode + '.zh.srt')
    save(output, '\n\n'.join(f"{i}\n{stamp(r['start_ms'])} --> {stamp(r['end_ms'])}\n{r['text']}"
                              for i, r in enumerate(revised, 1)) + '\n')
    state = dict(episode=episode, complete=False, draft_sha256=sha(draft),
                 working_srt=str(output), working_srt_sha256=sha(output),
                 original_cue_count=len(original), recorded_original_cue_ids=sorted(decisions),
                 decisions=indexes, mapping=mapping,
                 note='Partial working copy; recorded text review does not imply completed boundary/gap audits')
    save(review / 'working' / (episode + '.json'), json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--episode', required=True)
    args = parser.parse_args()
    state = rebuild(args.root.resolve(), args.episode)
    print(json.dumps({k: state[k] for k in ['episode', 'original_cue_count', 'recorded_original_cue_ids', 'working_srt'] }))
