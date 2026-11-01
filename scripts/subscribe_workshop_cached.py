"""Execute an explicitly approved cached WE list using steam-workshop-bulk.

Public metadata is reused; authenticated checks and writes remain rate limited.
No credentials or remote response bodies are logged.
"""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys


def approved_rows(metadata, ids):
    by_id = {str(x['publishedfileid']): x for x in metadata}
    rows = []
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate IDs')
    for item in ids:
        d = by_id[item]
        if (d.get('result') != 1 or d.get('consumer_app_id') != 431960
                or d.get('banned') or d.get('file_type') == 2):
            raise ValueError('Cached item is not an eligible WE item: ' + item)
        rows.append({'id': item, 'title': d.get('title')})
    return rows


def execute(client, state, save, stop_type):
    for row in state['plan']:
        item = row['id']
        if state['results'].get(item, {}).get('status') in ('success', 'already_subscribed'):
            continue
        try:
            current = client.state(item)
            if current == 'subscribed':
                state['results'][item] = {'status': 'already_subscribed'}
            elif current == 'unsubscribed':
                state['results'][item] = {'status': 'pending_verification'}
                save(state)
                client.subscribe(item)
                if client.state(item) != 'subscribed':
                    raise stop_type('Subscription not remotely verified')
                state['results'][item] = {'status': 'success'}
            else:
                state['results'][item] = {'status': 'skipped_unknown'}
            save(state)
            print(json.dumps({'completed': len(state['results']), 'total': len(state['plan']),
                              'id': item, 'status': state['results'][item]['status']}), flush=True)
        except Exception as error:
            # Preserve pending intent on uncertain writes. No automatic write replay.
            state['status'] = 'STOPPED_REQUIRES_REVIEW'
            state['stopped_item'] = item
            state['stop_reason'] = str(error) if isinstance(error, stop_type) else type(error).__name__
            save(state)
            raise
    state['status'] = 'COMPLETED'
    state.pop('stopped_item', None)
    state.pop('stop_reason', None)
    save(state)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skill-dir', type=Path, required=True)
    p.add_argument('--metadata', type=Path, required=True)
    p.add_argument('--ids', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--account', required=True)
    p.add_argument('--confirm', required=True, help='Account/list token after explicit user approval')
    a = p.parse_args()
    sys.path.insert(0, str(a.skill_dir))
    spec = importlib.util.spec_from_file_location('steam_workshop', a.skill_dir/'steam_workshop.py')
    sw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sw)
    ids = sw.parse_inputs(a.ids.read_text(encoding='utf-8-sig'))
    rows = approved_rows(json.loads(a.metadata.read_text(encoding='utf-8-sig')), ids)
    token = sw.token_for(a.account, ids)
    if a.confirm != token:
        raise ValueError('Account/list confirmation mismatch')
    client = sw.Client(sw.load_config(a.config), interval=3)
    if client.account != a.account:
        raise ValueError('Authenticated account mismatch')
    state = {'appid': 431960, 'account': a.account, 'roots': ids, 'plan': rows,
             'confirmation_token': token, 'approval': 'user explicitly approved all listed items',
             'results': {}, 'status': 'RUNNING'}
    if a.state.exists():
        previous = json.loads(a.state.read_text(encoding='utf-8-sig'))
        if any(previous.get(k) != state[k] for k in ('appid', 'account', 'roots', 'confirmation_token')):
            raise ValueError('Resume account/list mismatch')
        state = previous
        state['status'] = 'RUNNING'
        state.pop('stopped_item', None)
        state.pop('stop_reason', None)
    save = lambda value: sw.atomic_json(a.state, value)
    save(state)
    execute(client, state, save, sw.Stop)
    print(json.dumps(dict(Counter(x['status'] for x in state['results'].values()))), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Batch stopped: ' + type(error).__name__ + '; inspect persisted item status.', file=sys.stderr)
        sys.exit(2)
