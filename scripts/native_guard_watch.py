#!/usr/bin/env python3
"""Experimental scoped native producer pause, NOT production enabled.

Reuses the existing pidfd/owned-stop state machine. No pipeline/config edits,
model calls, media changes, other-worker discovery or GPU hardware changes.
E50's actual trial found the loaded T10 would not cool to77C promptly; the
guardian was stopped and its exact owned process explicitly resumed. Retained
for auditing that negative experiment, not as a validated throughput solution.
"""
import argparse
import csv
import fcntl
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time

from enhance_stage import atomic_json
from final_guard_watch import direct_children, worker_identity
import thermal_guard as thermal


def identify(post, episode, pid, worker_pid, worker_starttime):
    worker = worker_identity(worker_pid, worker_starttime, episode)
    job = thermal.load(post / 'jobs' / f'{episode}.json')
    production = thermal.load(post / 'backend/production-ready.json')
    master = job.get('master_sha256'); fp = production.get('backend_fingerprint')
    if (job.get('episode') != episode or job.get('state') != 'ENHANCING'
            or job.get('picture_pipeline') != 'native_light_gfpgan_rife_ass_one_encode'
            or not isinstance(master, str) or not re.fullmatch('[0-9a-f]{64}', master)
            or production.get('master_sha256') != master
            or not isinstance(fp, str) or not re.fullmatch('[0-9a-f]{64}', fp)
            or job.get('master') != f'incoming/{episode}.clean-master.mov'):
        raise ValueError('Native job/production identity differs')
    config = post / 'backend/runtime.native-active.json'
    raw = config.read_bytes(); key = hashlib.sha256(raw).hexdigest()
    archive = post / 'backend/native-profiles' / (key + '.json')
    if config.is_symlink() or archive.is_symlink() or archive.read_bytes() != raw:
        raise ValueError('Native runtime lacks immutable matching profile')
    cfg = thermal.load(config)
    if cfg.get('restoration_backend') != 'cuda_gfpgan_fused':
        raise ValueError('Not the native fused backend')
    context = post / 'cache' / episode / (episode + '.segments') / (master[:16] + '-' + fp[:16]) / 'context.json'
    data = thermal.load(context)
    if any(data.get(k) != v for k, v in dict(episode=episode, master_sha256=master, backend_fingerprint=fp).items()):
        raise ValueError('Native segment context differs')
    script = str(Path(__file__).resolve().with_name('segmented_backend.py'))
    expected = [str(post / 'face-venv/bin/python'), script,
                str(post / 'incoming' / f'{episode}.clean-master.mov'),
                str(post / 'cache' / episode / f'{episode}.enhanced.partial.mkv'),
                episode, '--config', str(config)]
    backend = thermal.process(pid)
    if backend['ppid'] != worker_pid or backend['argv'] != expected:
        raise ValueError('Not the exact native production child; plan/other jobs refused')
    return dict(episode=episode, backend_pid=pid, worker_pid=worker_pid,
                backend_starttime=backend['starttime'], worker_starttime=worker['starttime'],
                backend_cmdline=backend['argv'], worker_cmdline=worker['argv'],
                master_sha256=master, backend_fingerprint=fp), backend


def proactive_temperatures(reader):
    values = reader()  # Real physical device operating/slowdown/shutdown limits.
    for index, limit in [('0', 95), ('1', 85)]:
        row = values[index]
        row['hardware_pause'] = row['pause']
        row['native_abort_temperature'] = limit
        row['pause'] = min(row['pause'], limit - 2)
        row['resume'] = row['pause'] - 6
    return values


def fast_temperature_reader(hardware_reader, query=None):
    """Cache measured hardware limits; one bounded two-device query per tick."""
    limits = proactive_temperatures(hardware_reader)
    pins = {}
    if query is None:
        query = lambda: subprocess.check_output([
            'nvidia-smi', '--query-gpu=index,uuid,name,temperature.gpu',
            '--format=csv,noheader,nounits'], text=True, timeout=2)
    def read():
        result = {}
        for row in csv.reader(query().splitlines(), skipinitialspace=True):
            if len(row) != 4: raise ValueError('Malformed physical GPU telemetry')
            index, uuid, name, current = [x.strip() for x in row]
            if index not in ('0', '1') or index in result or not current.isdecimal():
                raise ValueError('Unknown/duplicate GPU index or invalid temperature')
            if name != {'0': 'NVIDIA RTX A4000', '1': 'Tesla T10'}[index] or not uuid.startswith('GPU-'):
                raise ValueError('Physical device name/UUID differs')
            if index in pins and pins[index] != uuid:
                raise ValueError('Physical device identity changed during guarding')
            if not 0 < int(current) < limits[index]['shutdown']:
                raise ValueError('Invalid physical temperature reading')
            result[index] = dict(limits[index], current=int(current), uuid=uuid, name=name)
        if set(result) != {'0', '1'}: raise ValueError('Missing physical GPU telemetry')
        for index, row in result.items(): pins[index] = row['uuid']
        return result
    read()  # Establish real hardware identity before any signal authority.
    return read


def watch(post, episode, worker_pid, worker_starttime):
    initial = worker_identity(worker_pid, worker_starttime, episode)
    state_path = post / 'reports' / f'{episode}.native-guard-watch.json'
    def publish(state, **details):
        atomic_json(state_path, dict(at=thermal.utc(), episode=episode, state=state,
                    worker_pid=worker_pid, worker_starttime=worker_starttime,
                    watch_pid=os.getpid(), final_quality_accepted=False, **details))
    original_identify, original_temperatures = thermal.identify, thermal.temperatures
    reader = fast_temperature_reader(original_temperatures)
    thermal.identify = lambda p, e, pid, worker: identify(p, e, pid, worker, worker_starttime)
    thermal.temperatures = reader
    try:
        while True:
            worker_identity(worker_pid, worker_starttime, episode, initial['argv'])
            job = thermal.load(post / 'jobs' / f'{episode}.json')
            if job.get('state') in ('READY', 'FAILED'):
                publish('ENDED', reason='Worker terminal; no quality claim'); return
            matches = []
            if job.get('state') == 'ENHANCING':
                for child in direct_children(worker_pid):
                    try:
                        identity, backend = identify(post, episode, child, worker_pid, worker_starttime)
                    except (OSError, ValueError, KeyError):
                        continue
                    matches.append((child, identity))
            if len(matches) == 1:
                child, identity = matches[0]
                with (post / 'reports/.thermal_guard.lock').open('a+') as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    prior_path = post / 'reports/thermal_pause.json'
                    if prior_path.exists():
                        prior = thermal.load(prior_path)
                        if prior.get('owned_stop') is True:
                            raise ValueError('Existing owned stop needs explicit reviewed recovery; never auto-adopt')
                        raw = prior_path.read_bytes()
                        history = post / 'reports/thermal_history'
                        history.mkdir(exist_ok=True)
                        archived = history / (hashlib.sha256(raw).hexdigest() + '.json')
                        if not archived.exists():
                            with archived.open('xb') as out: out.write(raw)
                    publish('ATTACHING', target_pid=child, binding=identity,
                            reason='Candidate only; thermal_pause.json proves actual guardian attachment')
                    thermal.run(post, episode, child, worker_pid, adopt_stop=False, poll_seconds=1)
            else:
                publish('WAITING', reason='Waiting for one exact native producer', matching_children=len(matches))
            time.sleep(2)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        publish('ATTENTION', reason=str(error))
        raise
    finally:
        thermal.identify, thermal.temperatures = original_identify, original_temperatures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post', type=Path, required=True)
    parser.add_argument('--episode', choices=['S01E50', 'S01E51'], required=True)
    parser.add_argument('--worker-pid', type=int, required=True)
    parser.add_argument('--worker-starttime', type=int, required=True)
    args = parser.parse_args()
    if min(args.worker_pid, args.worker_starttime) <= 0: parser.error('Exact live worker identity required')
    post = args.post.expanduser().absolute()
    with (post / 'reports' / ('.native_guard_watch.' + args.episode + '.lock')).open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        watch(post, args.episode, args.worker_pid, args.worker_starttime)
