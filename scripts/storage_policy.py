"""Validated disk reserve, shared by admission, calibration, segments and mux."""
import json
import math
from pathlib import Path


def minimum_free_gib(config=None):
    if config is not None and 'minimum_free_gib' in config:
        value = config['minimum_free_gib']
    else:
        project = Path(__file__).resolve().parents[1] / 'config/project.json'
        value = json.loads(project.read_text())['minimum_free_gib']
    if type(value) not in (int, float) or not math.isfinite(value) or not 10 <= value <= 100:
        raise ValueError('Disk reserve must be a finite number from10 through100GiB')
    return value
