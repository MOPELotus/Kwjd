"""Hash-bound, visually reviewed timing mapping for derived ASS only.

Original sealed SRTs are immutable. Receipt evidence lives in post/reports.
Positive offsets delay subtitles; negative offsets advance them.
"""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import re
import tempfile

from enhance_stage import sha256

_SHA = re.compile(r'^[0-9a-f]{64}$')
_TIME = re.compile(r'^(\d+):([0-5]\d):([0-5]\d)\.(\d{2})$')


def _offset(value):
    if type(value) is not int or not -1000 <= value <= 1000:
        raise ValueError('Subtitle offset_ms must be an integer from -1000 through 1000')
    return value


def _evidence(post, supplied):
    if not isinstance(supplied, str) or not supplied:
        raise ValueError('Missing subtitle timing evidence path')
    root = Path(post).resolve()
    candidate = Path(supplied)
    if not candidate.is_absolute():
        candidate = root / candidate
    else:
        try:
            candidate = root / candidate.relative_to(Path(post).absolute())
        except ValueError:
            pass
    # Lexical normalization must never conceal links or traversal.
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError('Subtitle timing evidence must be inside post/reports') from error
    if not relative.parts or relative.parts[0] != 'reports' or '..' in relative.parts:
        raise ValueError('Subtitle timing evidence must be inside post/reports')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink forbidden within subtitle timing evidence path')
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root / 'reports'):
        raise ValueError('Subtitle timing evidence is not a regular report file')
    return candidate, relative.as_posix()


def read_mapping(post, episode, master_sha256, srt_sha256):
    """Validate a reviewed receipt and evidence, returning a stable audit mapping."""
    if not re.fullmatch(r'S\d{2}E\d{2,3}', episode):
        raise ValueError('Invalid episode identifier')
    for digest in (master_sha256, srt_sha256):
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError('Invalid subtitle timing binding SHA256')
    receipt = Path(post) / 'receipts' / (episode + '.subtitle-timing.json')
    if not receipt.exists() and not receipt.is_symlink():
        return {'offset_ms': 0, 'receipt_sha256': None}
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError('Subtitle timing receipt must be a regular file')
    before = sha256(receipt)
    data = json.loads(receipt.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
            or data['schema_version'] != 1 or data.get('reviewed') is not True
            or data.get('episode') != episode or data.get('master_sha256') != master_sha256
            or data.get('srt_sha256') != srt_sha256):
        raise ValueError('Subtitle timing receipt is unreviewed or bound to different assets')
    offset = _offset(data.get('offset_ms'))
    path, relative = _evidence(post, data.get('evidence_path'))
    evidence_sha = data.get('evidence_sha256')
    if not isinstance(evidence_sha, str) or not _SHA.fullmatch(evidence_sha) or sha256(path) != evidence_sha:
        raise ValueError('Subtitle timing evidence hash mismatch')
    if sha256(receipt) != before:
        raise ValueError('Subtitle timing receipt changed during verification')
    return {'offset_ms': offset, 'receipt_sha256': before, 'schema_version': 1,
        'episode': episode, 'master_sha256': master_sha256, 'srt_sha256': srt_sha256,
        'evidence_path': relative, 'evidence_sha256': evidence_sha}


def _milliseconds(text):
    match = _TIME.fullmatch(text.strip())
    if not match:
        raise ValueError('Malformed ASS Dialogue timestamp: ' + text)
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + centiseconds * 10


def _shift(text, offset):
    value = _milliseconds(text) + offset
    if value < 0:
        raise ValueError('Subtitle offset would create a negative Dialogue timestamp')
    centiseconds = int((Decimal(value) / 10).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    seconds, cs = divmod(centiseconds, 100)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f'{hh}:{mm:02d}:{ss:02d}.{cs:02d}', centiseconds


def apply_offset(ass_path, offset_ms):
    """Atomically adjust only Dialogue start/end, returning hash-bound change audit.

    All dialogue rows must validate before any write. Format columns are honored;
    the comma-containing final text field remains intact, as do styles/comments.
    """
    offset = _offset(offset_ms)
    path = Path(ass_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Derived ASS must be a regular file')
    before = sha256(path)
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    lines = text.splitlines(keepends=True)
    section = ''
    fields = None
    count = 0
    edits = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip().lstrip('\ufeff')
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped.lower()
            continue
        if section != '[events]':
            continue
        if stripped.lower().startswith('format:'):
            fields = [value.strip().lower() for value in stripped.split(':', 1)[1].split(',')]
            if fields.count('start') != 1 or fields.count('end') != 1 or fields[-1] != 'text':
                raise ValueError('Unsupported ASS Events Format')
            continue
        if not stripped.lower().startswith('dialogue:'):
            continue
        if fields is None:
            raise ValueError('ASS Dialogue lacks an Events Format')
        prefix, body = line.split(':', 1)
        ending = '\r\n' if body.endswith('\r\n') else '\n' if body.endswith('\n') else ''
        content = body[:-len(ending)] if ending else body
        parts = content.split(',', len(fields) - 1)
        if len(parts) != len(fields):
            raise ValueError('Malformed ASS Dialogue columns')
        start_index, end_index = fields.index('start'), fields.index('end')
        old_start, old_end = parts[start_index], parts[end_index]
        if _milliseconds(old_end) <= _milliseconds(old_start):
            raise ValueError('Invalid original ASS Dialogue interval')
        start, start_cs = _shift(old_start, offset)
        end, end_cs = _shift(old_end, offset)
        if end_cs <= start_cs:
            raise ValueError('Subtitle offset would create an empty Dialogue interval')
        def preserve_padding(old, new):
            return old[:len(old) - len(old.lstrip())] + new + old[len(old.rstrip()):]
        parts[start_index] = preserve_padding(old_start, start)
        parts[end_index] = preserve_padding(old_end, end)
        lines[number - 1] = prefix + ':' + ','.join(parts) + ending
        count += 1
        if old_start.strip() != start or old_end.strip() != end:
            edits.append({'line': number, 'before': [old_start.strip(), old_end.strip()],
                          'after': [start, end]})
    if count == 0:
        raise ValueError('Derived ASS contains no Dialogue rows')
    result = ''.join(lines).encode('utf-8')
    if sha256(path) != before:
        raise ValueError('Derived ASS changed while preparing timing mapping')
    if result != raw:
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.timing-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(result)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'offset_ms': offset, 'dialogue_count': count, 'changed_count': len(edits),
            'before_sha256': before, 'after_sha256': sha256(path), 'edits': edits}
