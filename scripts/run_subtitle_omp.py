"""Run episode-scoped reviewers through separately authenticated OMP processes.

No App/OAuth credentials or inherited provider environment enter the child.
Runtime state, prompts, sessions and logs belong in the private data root.
"""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import time


def child_environment(key_file, agent_dir):
    key = key_file.read_text().strip()
    if not key:
        raise ValueError('External API key file is empty')
    if key_file.stat().st_mode & 0o077:
        raise ValueError('External API key file must be private')
    env = {k: os.environ[k] for k in
           ['HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'PATH'] if k in os.environ}
    env.update(PI_CONFIG_DIR=str(agent_dir.parent), PI_CODING_AGENT_DIR=str(agent_dir),
               LOTUS_OMP_BUFFERED_RESPONSES='0', JIGONG_RESPONSES_API_KEY=key)
    return env


def command(binary, root, sessions, prompt, resume=False):
    args = [str(binary), '--model', 'jigong-relay/gpt-6.1-sol', '--thinking', 'medium',
            '--offline', '--tools', 'read,bash,edit,write',
            '--no-extensions', '--no-skills', '--no-rules', '--no-context-files',
            '--extension', str(root / 'repo/scripts/subtitle_omp_transport.ts'),
            '--no-title', '--no-lsp', '--no-pty', '--auto-approve',
            '--cwd', str(root), '--session-dir', str(sessions), '--mode', 'json',
            '--max-time', '3h', '--print']
    if resume:
        args.append('--continue')
    return args + [prompt]


def validate_configuration(agent_dir):
    # This task provisions models.yml as JSON (valid YAML). An unspecified
    # custom-provider image budget defaults to five in this OMP build.
    providers = json.loads((agent_dir / 'models.yml').read_text()).get('providers', {})
    if set(providers) != {'jigong-relay'}:
        raise ValueError('Expected only the isolated external provider')
    provider = providers['jigong-relay']
    models = provider.get('models', [])
    if (provider.get('baseUrl') != 'https://api.zhehentiaohe.cn/v1'
            or provider.get('api') != 'openai-responses'
            or provider.get('apiKey') != 'JIGONG_RESPONSES_API_KEY'
            or len(models) != 1 or models[0].get('id') != 'gpt-6.1-sol'
            or 'image' not in models[0].get('input', [])
            or models[0].get('imageCountBudget') != 32):
        raise ValueError('External route/model/image delivery configuration differs from verified setup')


def write_state(path, **values):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(at=time.time(), **values), indent=2) + '\n')
    temp.replace(path)


def checkpoint(root, episodes=None):
    result = []
    for ep in episodes or [f'S03E{number:02d}' for number in range(2, 15)]:
        p = root / f'ocr/subtitle-review/progress-{ep}.json'
        if p.is_file():
            d = json.loads(p.read_text())
            result.append((ep, d.get('complete') is True,
                           len(d.get('text_reviewed_original_cue_ids', []))))
    return result


def acquire_episodes(stack, area, episodes):
    locks = area / 'episode-locks'
    locks.mkdir(exist_ok=True)
    for episode in sorted(episodes):
        if not re.fullmatch(r'S03E(?:0[2-9]|1[0-4])', episode):
            raise ValueError('Invalid episode assignment')
        handle = stack.enter_context((locks / (episode + '.lock')).open('a'))
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--agent-dir', type=Path, required=True)
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--binary', type=Path, default=Path('/home/lotus/.local/bin/omp'))
    parser.add_argument('--worker-id', default='main')
    parser.add_argument('--episodes', nargs='+')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', args.worker_id):
        parser.error('Invalid worker ID')
    base = args.root / 'ocr/subtitle-review/api-handoff'
    area = base if args.worker_id == 'main' else base / 'workers' / args.worker_id
    area.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock = stack.enter_context((area / 'omp-owner.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assigned = args.episodes or [f'S03E{n:02d}' for n in range(11, 15)]
        acquire_episodes(stack, base, assigned)
        validate_configuration(args.agent_dir)
        env = child_environment(args.key_file, args.agent_dir)
        env['JIGONG_OMP_TRANSPORT_LOG'] = str(area / 'omp-transport.jsonl')
        env['JIGONG_OMP_SESSION_DIR'] = str(area / 'omp-sessions')
        env['JIGONG_OMP_WORKER_ID'] = args.worker_id
        env['JIGONG_OMP_EPISODES'] = ','.join(assigned)
        prompt = args.prompt_file.read_text()
        sessions = area / 'omp-sessions'
        sessions.mkdir(exist_ok=True)
        # Resume only this isolated review session, never another App/OMP task.
        resume = any(sessions.rglob('*.jsonl'))
        state = area / 'omp-status.json'
        for attempt in range(100):
            before = checkpoint(args.root, assigned)
            log = area / f'omp-run-{time.time_ns()}.jsonl'
            with log.open('x') as output:
                child = subprocess.Popen(command(args.binary, args.root, sessions, prompt, resume),
                                         env=env, cwd=args.root, stdout=output,
                                         stderr=subprocess.STDOUT)
                write_state(state, state='RUNNING', pid=child.pid, provider='jigong-relay',
                            api='openai-responses', model='gpt-6.1-sol', effort='medium',
                            log=str(log), account_auth_inherited=False, attempt=attempt,
                            worker_id=args.worker_id, assigned_episodes=assigned)
                code = child.wait()
            after = checkpoint(args.root, assigned)
            if code == 0 and args.episodes and len(after) == len(assigned) and all(x[1] for x in after):
                from subtitle_review_supervisor import validate_episode
                from subtitle_review_ass import validate_ass
                if all(validate_episode(args.root, ep) and validate_ass(args.root, ep) for ep in assigned):
                    write_state(state, state='COMPLETE', validated_episodes=assigned,
                                assigned_episodes=assigned, worker_id=args.worker_id, log=str(log))
                    return 0
            if code != 0 or after == before:
                write_state(state, state='NEEDS_ATTENTION', exit_code=code, log=str(log),
                            reason='CLI failed or returned without saved subtitle progress; no blind retry')
                return 1
            completion = area / 'all13-api-review-complete.json'
            if completion.is_file():
                # Completion is accepted only after the original validators run,
                # independently of the model-authored marker or CLI exit status.
                from subtitle_review_supervisor import validate_episode
                from subtitle_review_ass import validate_ass
                if all(validate_episode(args.root, f'S03E{n:02d}') and
                       validate_ass(args.root, f'S03E{n:02d}') for n in range(2, 15)):
                    write_state(state, state='COMPLETE', validated_episodes=13, log=str(log))
                    return 0
                write_state(state, state='NEEDS_ATTENTION', reason='Completion validation failed', log=str(log))
                return 1
            resume = True
            prompt = (args.prompt_file.read_text() + '\n继续同一任务，从实际已保存检查点接续，'
                      '严格只处理分配回目，不写共享progress.json，不新建代理。')
            time.sleep(30)
        write_state(state, state='NEEDS_ATTENTION', reason='Bounded continuation limit reached')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
