"""Run the authorized external cloud operator, waking only on stage changes.

No Codex/App authentication, native agents or duplicate media workers are used.
Private prompts and checkpoints live outside the repository.
"""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

from run_subtitle_omp import child_environment, command, validate_configuration, write_state


def fingerprint(root):
    result = []
    for pattern in ('post/jobs/S03E*.json', 'post/handoffs/S03E*.master-received.json',
                    'post/receipts/S03E*.json', 'post/reports/S03E*.quality.json'):
        for path in sorted(root.glob(pattern)):
            try:
                data = json.loads(path.read_text())
                if '/jobs/' in str(path):
                    result.append((path.name, data.get('state'), data.get('master_sha256')))
                else:
                    result.append((str(path.relative_to(root)), path.stat().st_mtime_ns))
            except (OSError, ValueError):
                continue  # Atomic handoffs will appear in a later tool-only poll.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--key-file', required=True, type=Path)
    parser.add_argument('--agent-dir', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    area = root / 'post/external-operator'
    area.mkdir(parents=True, exist_ok=True)
    with (area / 'owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        validate_configuration(args.agent_dir)
        env = child_environment(args.key_file, args.agent_dir)
        sessions = area / 'sessions'
        sessions.mkdir(exist_ok=True)
        env.update(JIGONG_OMP_TRANSPORT_LOG=str(area / 'transport.jsonl'),
                   JIGONG_OMP_SESSION_DIR=str(sessions), JIGONG_OMP_WORKER_ID='production')
        while True:
            resume = any(sessions.rglob('*.jsonl'))
            log = area / f'run-{time.time_ns()}.jsonl'
            checkpoint = area / 'checkpoint.json'
            before_checkpoint = checkpoint.stat().st_mtime_ns if checkpoint.exists() else None
            prompt = (area / 'task.txt').read_text()
            with log.open('x') as output:
                child = subprocess.Popen(command(Path('/home/lotus/.local/bin/omp'), root,
                    sessions, prompt, resume), env=env, cwd=root,
                    stdout=output, stderr=subprocess.STDOUT)
                write_state(area / 'status.json', state='RUNNING', pid=child.pid,
                            log=str(log), provider='jigong-relay', model='gpt-6.1-sol')
                code = child.wait()
            changed = checkpoint.exists() and checkpoint.stat().st_mtime_ns != before_checkpoint
            saved = json.loads(checkpoint.read_text()) if changed else {}
            state = saved.get('state')
            if code or state not in ('WAITING', 'CONTINUE', 'COMPLETE'):
                write_state(area / 'status.json', state='NEEDS_ATTENTION', exit_code=code,
                            log=str(log), checkpoint_changed=changed,
                            reason='No blind paid retry; inspect saved checkpoint and log')
                return 1
            if state == 'COMPLETE':
                # The model must supply final receipts; completion is independently checked.
                from episode_lifecycle import load
                for n in range(4, 15):
                    ep = f'S03E{n:02d}'
                    receipt = load(root / f'post/receipts/{ep}.json')
                    quality = load(root / f'post/reports/{ep}.quality.json')
                    from jigong_quality import verify_quality
                    verified = verify_quality(root, episode=ep)
                    if (receipt.get('received') is not True
                            or receipt.get('sha256') != quality.get('final_sha256')
                            or receipt.get('quality_evidence_sha256') != verified['quality_sha256']):
                        raise ValueError('Completion lacks actual matching final receipt: ' + ep)
                write_state(area / 'status.json', state='COMPLETE', log=str(log))
                return 0
            write_state(area / 'status.json', state='WAITING', log=str(log), checkpoint=saved)
            if state == 'CONTINUE':
                time.sleep(30)
                continue
            baseline = fingerprint(root)
            deadline = time.monotonic() + 3 * 3600
            while time.monotonic() < deadline:
                time.sleep(60)
                if fingerprint(root) != baseline:
                    break


if __name__ == '__main__':
    raise SystemExit(main())
