"""Readable, tool-only subtitle dashboard; never sends a model request."""
import argparse
from collections import deque
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def stamp(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, ZONE).strftime('%m-%d %H:%M:%S')


def sealed_count(root):
    """Cheap artifact identity check, independent of stale progress booleans.

    This dashboard does not replace the full source/image/style validators.
    """
    completed = 0
    for number in range(2, 15):
        ep = f'S03E{number:02d}'
        srt = root / f'output/{ep}.zh.srt'
        seal_path = srt.with_suffix('.srt.ready.json')
        ass = root / f'output/ass/{ep}.ass'
        seal = read(seal_path)
        style = read(ass.with_suffix('.ass.ready.json'))
        report = read(root / f'ocr/subtitle-review/reports/{ep}.json')
        try:
            srt_sha = hashlib.sha256(srt.read_bytes()).hexdigest()
            ass_sha = hashlib.sha256(ass.read_bytes()).hexdigest()
            seal_sha = hashlib.sha256(seal_path.read_bytes()).hexdigest()
        except OSError:
            continue
        if (report.get('complete') is True and seal.get('reviewed') is True
                and srt_sha == seal.get('sha256') == style.get('srt_sha256') == report.get('final_srt_sha256')
                and style.get('state') == 'ASS_STYLE_READY_REFERENCE_CLOCK'
                and style.get('srt_seal_sha256') == seal_sha and style.get('ass_sha256') == ass_sha):
            completed += 1
    return completed


def describe(event):
    """Only allowlisted metadata: no commands, tool payloads or image bytes."""
    kind = event.get('type')
    if kind == 'tool_execution_start':
        tool = event.get('toolName')
        args = event.get('args') or {}
        if tool == 'read':
            name = Path(str(args.get('path', ''))).name
            if re.search(r'\.(png|jpe?g|webp)$', name, re.I):
                return '看图核对：' + name[:100]
            return '读取字幕时间轴或审校记录'
        if tool == 'bash':
            command = str(args.get('command', ''))
            if 'focused_native.py' in command:
                return '生成下一批原片字幕截图（取证不等于已审）'
            if 'seal-srt' in command or 'finalize_focused_binding' in command:
                return '核验本回完整性并准备封存'
            if 'subtitle_review_ass' in command:
                return '生成并校验ASS字幕'
            if 'commit(' in command or 'omp_focused_commit.py' in command:
                return '保存本批审校决定和检查点'
            return '核对时间轴、证据及审校结果'
        if tool in ('write', 'edit'):
            return '保存审校记录或辅助脚本'
    if kind == 'tool_execution_end' and event.get('isError'):
        return '工具操作报错，执行者正在检查；未据此计为完成'
    return None


def worker_states(root):
    base = root / 'ocr/subtitle-review'
    executor = read(root / '.kwjd/subtitle-executor.json')
    result = []
    for item in executor.get('workers', []):
        status = read(Path(item['status_path']))
        episodes = item['episodes']
        candidates = [read(base / f'progress-{ep}.json') for ep in episodes]
        progress = next((p for p in candidates if p.get('complete') is not True), candidates[-1])
        result.append((item['id'], status, progress))
    return result or [('main', read(base/'api-handoff/omp-status.json'), read(base/'progress.json'))]


class Events:
    def __init__(self):
        self.path = None
        self.offset = 0
        self.pending = b''
        self.recent = deque(maxlen=8)

    def update(self, path):
        added = []
        if not path:
            return added
        path = Path(path)
        try:
            if path != self.path or path.stat().st_size < self.offset:
                self.path, self.offset, self.pending = path, 0, b''
            with path.open('rb') as stream:
                stream.seek(self.offset)
                data = stream.read()
                self.offset = stream.tell()
        except OSError:
            return added
        lines = (self.pending + data).split(b'\n')
        self.pending = lines.pop()
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            text = describe(event)
            if text:
                # OMP events sometimes omit timestamps; log time is observation
                # time in that case, never a claimed model execution timestamp.
                epoch = event.get('timestamp')
                if not isinstance(epoch, (int, float)):
                    epoch = time.time()
                elif epoch > 1e12:
                    epoch /= 1000
                entry = f'{stamp(epoch)} {text}'
                self.recent.append(entry)
                added.append(entry)
        return added


def render(root, recent):
    base = root / 'ocr/subtitle-review'
    progress = read(base / 'progress.json')
    owner = read(base / 'api-handoff/omp-status.json')
    episode = progress.get('episode', '')
    number = int(episode[4:]) - 1 if re.fullmatch(r'S03E\d{2}', episode) else '?'
    count = len(progress.get('text_reviewed_original_cue_ids', []))
    total = progress.get('total_original_cues', 0)
    completed = sealed_count(root)
    state = owner.get('state')
    label = {'RUNNING': '运行中', 'NEEDS_ATTENTION': '等待排查', 'COMPLETE': '全部完成'}.get(state, '尚未启动')
    updated = progress.get('updated_at')
    age = f'{max(0, int(time.time()-updated))}秒前' if isinstance(updated, (int, float)) else '未知'
    ratio = f'{count / total:.1%}' if total else '未知'
    latest = next((line for line in reversed(recent) if ' 检查点：' not in line), '')
    activity = latest.split(' ', 2)[-1] if latest else '等待新的工具记录'
    workers = worker_states(root)
    lanes = []
    if len(workers) > 1:
        if any(status.get('state')=='NEEDS_ATTENTION' for _,status,_ in workers):
            label = '部分线程等待排查'
        elif completed==13 and all(status.get('state')=='COMPLETE' for _,status,_ in workers):
            label = '全部完成'
        else:
            label = '并行运行中' if any(status.get('state')=='RUNNING' for _,status,_ in workers) else '等待启动'
        for worker, status, current in workers:
            ep = current.get('episode', '?')
            done = len(current.get('text_reviewed_original_cue_ids', []))
            size = current.get('total_original_cues', 0)
            fraction = f'{done/size:.1%}' if size else '等待初始化'
            state = {'RUNNING':'运行中','COMPLETE':'分配回目已完成','NEEDS_ATTENTION':'等待排查'}.get(status.get('state'),'等待启动')
            lanes.append(f'  {worker}｜{ep}｜{done}/{size}条 · {fraction}｜{state}')
    return '\n'.join([
        '济公传 · OMP字幕实时状态', f'刷新：{stamp()}（北京时间）',
        f'执行：{label}｜外部 Responses API｜gpt-6.1-sol / medium',
        f'当前：第{number}回（{episode or "未知"}）',
        f'本回已保存：{count}/{total}条 · {ratio}',
        f'已封存SRT及ASS：{completed}/13（文件SHA匹配）',
        *(['', f'并行审校：{len(workers)}路', *lanes, ''] if lanes else []),
        f'当前阶段：{activity}', f'检查点更新：{age}',
        '说明：截图生成不计审校进度；百分比只计已保存的逐条审校。',
        '', '最近操作（日志无时间戳时显示首次观察时间）：',
        *('  ' + line for line in recent), '',
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--watch', type=float, default=0)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.watch and args.watch < 1:
        parser.error('Watch interval must be at least one second')
    readers = {}
    recent = deque(maxlen=8)
    area = args.root / 'ocr/subtitle-review/api-handoff'
    prior = None
    first = True
    while True:
        added = []
        workers = worker_states(args.root)
        for worker, owner, _ in workers:
            reader = readers.setdefault(worker, Events())
            for line in reader.update(owner.get('log')):
                entry = line[:14] + f' [{worker}]' + line[14:]
                recent.append(entry)
                added.append(entry)
        if args.publish and len(workers) > 1:
            # Sole aggregate writer; lane workers update only progress-EP.json.
            active = [p for _, _, p in workers if p.get('complete') is not True and p.get('episode')]
            if active:
                chosen = dict(min(active, key=lambda p:p['episode']))
                chosen['parallel_workers'] = [w for w, _, _ in workers]
                temp = args.root / 'ocr/subtitle-review/progress.json.parallel.tmp'
                temp.write_text(json.dumps(chosen,ensure_ascii=False,indent=2)+'\n')
                temp.replace(args.root / 'ocr/subtitle-review/progress.json')
            elif all(status.get('state')=='COMPLETE' for _,status,_ in workers) and sealed_count(args.root)==13:
                progress_path = args.root / 'ocr/subtitle-review/progress.json'
                completed = read(progress_path)
                completed.update(complete=True,completed_episodes=[f'S03E{n:02d}' for n in range(2,15)],
                                 stage='ALL_13_REVIEWED_SEALED_AND_ASS_READY',updated_at=time.time())
                temporary = progress_path.with_suffix('.parallel.tmp')
                temporary.write_text(json.dumps(completed,ensure_ascii=False,indent=2)+'\n')
                temporary.replace(progress_path)
        progress = read(args.root / 'ocr/subtitle-review/progress.json')
        marker = (progress.get('episode'), len(progress.get('text_reviewed_original_cue_ids', [])))
        if marker != prior:
            message = f'{stamp()} 检查点：{marker[0]} 已保存{marker[1]}/{progress.get("total_original_cues", 0)}条'
            recent.append(message)
            added.append(message)
            prior = marker
        output = render(args.root, recent)
        if args.publish:
            temp = area / 'status.txt.tmp'
            temp.write_text(output)
            temp.replace(area / 'status.txt')
            if added:
                with (area / 'human.log').open('a') as log:
                    # First startup may ingest old JSONL; keep only the latest
                    # eight descriptions instead of fabricating historical times.
                    log.write('\n'.join(added[-8:] if first else added) + '\n')
        else:
            if args.watch and sys.stdout.isatty():
                print('\033[2J\033[H', end='')
            print(output, flush=True)
        first = False
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
