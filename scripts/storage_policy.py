"""Validated disk reserve, shared by admission, calibration, segments and mux."""
import json
import math
import os
from pathlib import Path


def minimum_free_gib(config=None):
    if config is not None and 'minimum_free_gib' in config:
        value = config['minimum_free_gib']
    else:
        repo=Path(__file__).resolve().parents[1]
        project=Path(os.environ.get('KWJD_PROJECT',str(repo/'config/project.json')))
        if not project.is_absolute():project=repo/project
        project=project.resolve(strict=True)
        if not project.is_relative_to((repo/'config').resolve()):
            raise ValueError('Project storage policy must remain inside repository config')
        value = json.loads(project.read_text())['minimum_free_gib']
    if type(value) not in (int, float) or not math.isfinite(value) or not 5 <= value <= 100:
        raise ValueError('Disk reserve must be a finite number from5 through100GiB')
    return value
