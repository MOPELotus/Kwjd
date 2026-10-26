"""Retired source identities are rejected even when filenames are reused."""
import json
from pathlib import Path

RETIRED_MASTER_SHA256 = frozenset({
    'bc33db2cff031025a76c18e2ebfc4f95a6c7fdb0c2d6183ae13f238a257a10ed',
})


def reject_retired(digest, policy=None):
    retired = set(RETIRED_MASTER_SHA256)
    if policy is not None and Path(policy).exists():
        extra = json.loads(Path(policy).read_text())['retired_sha256']
        if not isinstance(extra, list) or any(not isinstance(x, str) or len(x) != 64 for x in extra):
            raise ValueError('Invalid local master retirement policy')
        retired.update(x.lower() for x in extra)
    if digest.lower() in retired:
        raise ValueError('Retired master hash rejected; same filename does not identify the replacement')
