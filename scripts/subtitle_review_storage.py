"""Budget subtitle evidence on its actual destination filesystem."""
from pathlib import Path
import shutil


def require_space(destination, allowance_gib=2):
    ancestor=Path(destination).resolve()
    while not ancestor.exists():ancestor=ancestor.parent
    free=shutil.disk_usage(ancestor).free
    if free<(5+allowance_gib)*1024**3:
        raise RuntimeError(f'Need5GiB reserve plus{allowance_gib}GiB evidence allowance on {ancestor}')
    return free
