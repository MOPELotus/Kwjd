"""Jigong replacement masters must follow the active project policy."""
import json
import re
from pathlib import Path

OLD_JIGONG_MASTERS = {
    'S03E02': '0aaa63c922cd92e211c3515003169aed8c466251b66f4b233e539cb62cb0baa6',
    'S03E03': 'e0f9ac6e34f76cfdf8c47601a8b973020094785467e2989dff971ee9c14c36b4',
    'S03E04': '3fa3134d3f743104a52d6413bbded1cb7fc3046b033b7423149ad6b5c3e4f7ad',
    'S03E05': 'd4c258283eeeb237378ee34d5a3628cf67375cca1a32c1f6519520770d136e15',
    'S03E06': '9c5cc784f4618bfc3de163de0fc2481c9c46610afe75b5aa30c17c0656944e7b',
    'S03E07': 'e5adfef01439aae3c2de7893088769947f1217609dd043d24c09c83cf3393a3b',
    'S03E08': 'bab66189703fc3985c0285843fd9be97885cd702be9651d095f3a51656c3612a',
}


def enforce(episode, digest, rate=None):
    if not re.fullmatch(r'S03E(?:0[2-9]|1[0-4])', episode):
        return
    if digest in OLD_JIGONG_MASTERS.values():
        raise ValueError('Superseded Jigong CFR30 master; fresh native25 Mask/export required')
    policy = Path(__file__).resolve().parents[1] / 'config/projects/jigong-s03.json'
    export = json.loads(policy.read_text(encoding='utf-8'))['master_export']
    if rate is not None and rate not in {str(x) + '/1' for x in export['accepted_master_fps']}:
        raise ValueError('Jigong replacement master must use native25; do not transcode the old CFR30 export')
