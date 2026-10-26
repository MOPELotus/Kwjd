#!/usr/bin/env python3
"""Record actual style approval, binding template and all shipped fonts to hashes.

Run only after actual playback review/approval, never on template presence alone.
Media, proprietary fonts and the generated receipt remain outside Git.
"""
import argparse
from pathlib import Path
from episode_lifecycle import now, save, sha256


def seal(folder, note, master_hash):
    import re
    if len(note.strip()) < 8 or not re.fullmatch(r'[0-9a-f]{64}', master_hash):
        raise ValueError('Need actual approval basis and the reviewed master SHA256')
    template = folder / 'template.ass'
    fonts = sorted(p for p in (folder / 'fonts').iterdir()
                   if p.is_file() and p.suffix.lower() in {'.ttf', '.otf', '.ttc'})
    if not fonts:
        raise ValueError('No packaged fonts; verify real libass font selection first')
    record = {'schema_version': 1, 'approved': True, 'sha256': sha256(template),
              'fonts': {p.name: sha256(p) for p in fonts}, 'approved_at': now(),
              'basis': note, 'master_sha256': master_hash}
    save(folder / 'template.ass.ready.json', record)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--styles', type=Path, required=True)
    p.add_argument('--master-sha256', required=True)
    p.add_argument('--note', required=True)
    p.add_argument('--approve', action='store_true')
    args = p.parse_args()
    if not args.approve:
        p.error('--approve requires actual prior approval')
    record = seal(args.styles, args.note, args.master_sha256)
    print(f"Approved style SHA256 {record['sha256']}; {len(record['fonts'])} fonts bound")


if __name__ == '__main__':
    main()
