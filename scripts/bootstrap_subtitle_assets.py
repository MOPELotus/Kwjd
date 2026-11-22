"""Restore official MiSans assets and technical font evidence, never approval."""
import argparse
import json
from pathlib import Path
import subprocess
import urllib.request
import zipfile

from enhance_stage import atomic_json, sha256
from font_preflight import preflight
from prepare_subtitle_style import prepare_assets

URL = 'https://hyperos.mi.com/font-download/MiSans.zip'
ARCHIVE_SHA = 'b6aa1fc827035922612df8edf36e5609bca1c5441e25cd57572204569b7b81d9'
LICENSE_URL = 'https://hyperos.mi.com/font/en/download/'


def restore(post):
    post = Path(post).resolve()
    source = post / 'tools/misans-official'
    source.mkdir(parents=True, exist_ok=True)
    archive = source / 'MiSans.zip'
    if not archive.exists():
        partial = archive.with_suffix('.zip.downloading')
        subprocess.run(['curl', '-fL', '--retry', '3', '-C', '-', '-o', str(partial), URL], check=True)
        if sha256(partial) != ARCHIVE_SHA:
            raise ValueError('Official archive changed; retain download for inspection')
        partial.rename(archive)
    if sha256(archive) != ARCHIVE_SHA:
        raise ValueError('Official MiSans archive SHA differs')
    variable = source / 'MiSansVF.ttf'
    with zipfile.ZipFile(archive) as package:
        entries = [n for n in package.namelist() if Path(n).name == 'MiSansVF.ttf']
        if len(entries) != 1:
            raise ValueError('Official variable font entry is not unique')
        content = package.read(entries[0])
        if variable.exists() and variable.read_bytes() != content:
            raise ValueError('Existing variable font differs')
        if not variable.exists():
            variable.write_bytes(content)
    from fontTools.ttLib import TTFont
    import fontTools
    font = TTFont(variable)
    instances = [i for i in font['fvar'].instances
                 if font['name'].getDebugName(i.subfamilyNameID) == 'Demibold']
    if len(instances) != 1 or instances[0].coordinates != {'wght': 450.0}:
        raise ValueError('Expected official Demibold450 named instance')
    static, template = prepare_assets(variable, post / 'styles')
    restored = TTFont(static)
    if 'fvar' in restored or restored['name'].getDebugName(1) != 'MiSans Subtitle':
        raise ValueError('Static packaged font identity differs')
    evidence = post / 'styles/evidence'
    evidence.mkdir(exist_ok=True)
    license_path = source / 'official-download-license.html'
    if not license_path.exists():
        with urllib.request.urlopen(LICENSE_URL, timeout=30) as response:
            license_path.write_bytes(response.read())
    log = evidence / 'font-selection.log'
    result = preflight(template, static.parent, evidence, log)
    result.update(schema_version=1, technical_checked=True, visual_checked=False,
                  source_url=URL, archive_sha256=ARCHIVE_SHA,
                  variable_font_sha256=sha256(variable), fonttools_version=fontTools.__version__,
                  named_instance='Demibold', coordinates=instances[0].coordinates,
                  license_url=LICENSE_URL, license_snapshot_sha256=sha256(license_path),
                  static_font_sha256=sha256(static), template_sha256=sha256(template),
                  font_names={str(i): restored['name'].getDebugName(i) for i in [1,2,4,6,16,17]},
                  render_log=str(log), render_log_sha256=sha256(log),
                  scope='Technical assets only; actual-master visual review pending')
    atomic_json(evidence / 'font-selection.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(restore(args.post), ensure_ascii=False, indent=2))
