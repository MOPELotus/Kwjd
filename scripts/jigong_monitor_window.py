#!/usr/bin/env python3
"""A read-only Windows progress window; all console child processes stay hidden."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

from jigong_monitor_snapshot import read

BG = '#101827'
CARD = '#1b2739'
TEXT = '#e7eef7'
MUTED = '#9eafc3'
BLUE = '#6cbcff'
GREEN = '#6fe2b0'
AMBER = '#ffd080'
STATES = {
    'WAIT_DISK': '等待磁盘余量', 'WAIT_GPU': '等待 GPU 可用',
    'WAIT_SUBTITLE': '等待定版字幕', 'WAIT_BACKEND_TECHNICAL': '等待后端技术验证',
    'WAIT_STYLE_APPROVAL': '等待样式检查', 'ENHANCING': 'AI 修复 · 补帧 · 编码',
    'ENHANCED_READY': '增强完成，准备后续处理', 'PROCESSING': '压制与最终校验',
    'READY': '成片技术检查完成', 'FAILED': '处理异常，等待云端处理',
    'QUEUED': '已排队', 'RECEIVED': '已回传', 'CLEANED': '交接清理完成',
}


def ratio(done, total):
    if not isinstance(done, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
        return None
    return max(0, min(100, 100 * done / total))


def percent(value):
    return '暂无计数' if value is None else f'{value:.1f}%'


def duration(seconds):
    if not isinstance(seconds, (int, float)):
        return '—'
    seconds = int(seconds)
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


class Monitor:
    def __init__(self, root, config, interval):
        self.root = root
        self.config = config
        self.interval = interval
        self.messages = queue.Queue()
        self.busy = False
        self.last_success = None
        self.closed = threading.Event()
        self.win = tk.Tk()
        self.win.title('坑王驾到 · 云端进度监测')
        self.win.geometry(f'920x{min(920, self.win.winfo_screenheight() - 100)}')
        self.win.minsize(760, 720)
        self.win.configure(bg=BG)
        self.win.option_add('*Font', ('Microsoft YaHei UI', 10))
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Monitor.Horizontal.TProgressbar', troughcolor='#2c3c52',
                        background=BLUE, borderwidth=0, lightcolor=BLUE, darkcolor=BLUE)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        outer = tk.Frame(self.win, bg=BG)
        outer.pack(fill='both', expand=True, padx=24, pady=20)
        tk.Label(outer, text='济公传 · 云端制作监测', bg=BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
        self.connection = self.label(outer, '正在连接服务器…', MUTED, BG)
        self.banner = self.label(outer, '只读监测，不启动或中断云端任务', MUTED, BG)
        self.values = {}
        self.bars = {}
        self.card(outer, 'post', '第一回后期', ['state', 'frames', 'speed'])
        self.card(outer, 'review', '字幕审校 · 13 回独立推进', ['state', 'text', 'boundary', 'all'])
        self.card(outer, 'delivery', '母版与成片交接', ['upload', 'download'])
        self.telemetry = self.label(outer, 'GPU / 存储：等待首个快照', MUTED, BG)
        self.updated = self.label(outer, '最近成功更新：—', MUTED, BG)
        bottom = tk.Frame(outer, bg=BG)
        bottom.pack(fill='x', pady=(8, 0))
        self.refresh_button = tk.Button(bottom, text='立即刷新', command=self.refresh,
                                       bg='#2c4564', fg=TEXT, relief='flat', padx=18, pady=6)
        self.refresh_button.pack(side='left')
        self.top = tk.BooleanVar()
        tk.Checkbutton(bottom, text='窗口置顶', variable=self.top,
                       command=lambda: self.win.attributes('-topmost', self.top.get()),
                       bg=BG, fg=MUTED, selectcolor=BG, activebackground=BG,
                       activeforeground=TEXT).pack(side='left', padx=14)
        tk.Label(bottom, text=f'每 {interval} 秒刷新 · 关闭窗口不会停止云端工作',
                 bg=BG, fg=MUTED).pack(side='right')
        self.win.after(200, self.refresh)
        self.win.after(200, self.drain)

    def label(self, parent, text, color=TEXT, bg=CARD):
        label = tk.Label(parent, text=text, bg=bg, fg=color, anchor='w', justify='left',
                         wraplength=820)
        label.pack(anchor='w', fill='x', pady=3)
        return label

    def card(self, parent, name, title, rows):
        box = tk.Frame(parent, bg=CARD, padx=16, pady=10)
        box.pack(fill='x', pady=(10, 0))
        tk.Label(box, text=title, bg=CARD, fg=TEXT,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(0, 3))
        for row in rows:
            self.values[name + '.' + row] = self.label(box, '—', MUTED)
            if row in ('frames', 'text', 'boundary', 'download'):
                bar = ttk.Progressbar(box, style='Monitor.Horizontal.TProgressbar', maximum=100)
                bar.pack(fill='x', pady=(0, 4))
                self.bars[name + '.' + row] = bar

    def refresh(self):
        if self.busy or self.closed.is_set():
            return
        self.busy = True
        self.refresh_button.configure(state='disabled')
        threading.Thread(target=self.fetch, daemon=True).start()

    def fetch(self):
        try:
            config = read(self.config)
            client = Path('C:/Program Files/Git/usr/bin/ssh.exe')
            script = Path(__file__).with_name('jigong_monitor_snapshot.py').read_text(encoding='utf-8')
            command = [str(client) if client.exists() else 'ssh', '-o', 'BatchMode=yes',
                       '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
                       '-o', 'ServerAliveCountMax=2', config['host'],
                       shlex.join(['python3', '-', '--root', config['remote_root']])]
            result = subprocess.run(command, input=script, capture_output=True, text=True,
                                    encoding='utf-8', timeout=22,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                raise RuntimeError(result.stderr.strip()[-300:] or 'SSH 连接失败')
            data = json.loads(result.stdout)
            data['local_receiver'] = read(self.root / '.kwjd/managed-receiver.json')
            data['upload'] = read(self.root / '.kwjd/jigong-s03/S03E02.master-upload.json')
            data['upload_transfers'] = [read(p) for p in sorted(
                (self.root / '.kwjd/jigong-s03').glob('S03E*.master-upload.transfer-state.json'))]
            data['received'] = read(self.root / '.kwjd/receipts/S03E02.json')
            report = read(self.root / '.kwjd/remote-reports/S03E02.json')
            wanted = report.get('sha256')
            partial = self.root / '.kwjd/downloads' / (str(wanted) + '.downloading')
            data['download_bytes'] = partial.stat().st_size if partial.exists() else 0
            data['download_total'] = report.get('bytes')
            self.messages.put((data, None))
        except Exception as error:
            self.messages.put((None, str(error)))

    def set(self, key, text, value=None):
        self.values[key].configure(text=text)
        if key in self.bars:
            self.bars[key]['value'] = value or 0

    def render(self, data):
        job = data['job']
        state = job.get('state')
        waiting = state and (state.startswith('WAIT_') or state == 'FAILED')
        self.connection.configure(text='● 已连接 TodayEatUbuntu-7K62', fg=GREEN)
        detail = job.get('detail') or ''
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        self.banner.configure(text=(STATES.get(state, state or '等待云端任务状态') +
                                    (' · ' + detail[:180] if detail else '')),
                              fg=AMBER if waiting else BLUE)
        frames = data['frames']
        pct = frames.get('percent')
        self.set('post.state', '当前阶段：' + STATES.get(state, state or '尚未入队'))
        if frames.get('total_frames'):
            self.set('post.frames', f"已验收分段：{percent(pct)}  ·  {frames['verified_frames']:,} / {frames['total_frames']:,} 原生帧", pct)
        else:
            self.set('post.frames', '暂无已验收帧计数（不会按运行时间估算百分比）')
        running = frames.get('running_frames')
        speed = frames.get('fps')
        self.set('post.speed', f"已处理原生帧：{running:,}" if running is not None else '等待实际处理计数')
        if speed:
            self.values['post.speed'].configure(text=self.values['post.speed'].cget('text') +
                f"  ·  {speed:.2f} 原生帧/秒  ·  预计剩余 {duration(frames.get('eta_seconds'))}")
        review = data['review']
        ep = review.get('episode') or '—'
        part = int(ep[4:]) - 1 if ep.startswith('S03E') else '—'
        self.set('review.state', f'当前审校：第 {part} 回（{ep}） · 文字与时间边界分别核对')
        total = review.get('total')
        for key, title in [('text', '文字已核对'), ('boundary', '时间边界已核对')]:
            done = review.get('text' if key == 'text' else 'boundaries', 0)
            value = ratio(done, total)
            self.set('review.' + key, f'{title}：{done:,} / {total or "—"}  ·  {percent(value)}', value)
        self.set('review.all', f"全部 13 回：SRT 已封存 {len(review['sealed'])}/13  ·  ASS 样式版 {len(review['ass'])}/13（按回执统计）")
        upload = data['upload']
        self.set('delivery.upload', '母版：100% · 云端 SHA / 解码校验通过' if upload.get('transfer_verified') else '母版：尚无已验证上传收据')
        for transfer in data.get('upload_transfers', []):
            if transfer.get('stage') not in ('UPLOADING', 'RETRY_PENDING',
                                              'UPLOAD_TRANSPORT_COMPLETE_REMOTE_HASH_PENDING'):
                continue
            source = Path(transfer.get('source', '')).name
            episode = source.split('.')[0]
            if not episode.startswith('S03E') or not episode[4:].isdigit():
                continue
            part = int(episode[4:]) - 1
            receipt = read(self.root / '.kwjd/jigong-s03' / (episode + '.master-upload.json'))
            if receipt.get('transfer_verified'):
                continue
            done = transfer.get('remote_bytes', 0)
            total = transfer.get('total_bytes')
            value = ratio(done, total)
            self.set('delivery.upload', f'第 {part} 回母版上传：{percent(value)} · {done/1024**3:.2f} / {(total or 0)/1024**3:.2f} GiB'
                     + (' · 等待远端 SHA 校验' if transfer['stage'] == 'UPLOAD_TRANSPORT_COMPLETE_REMOTE_HASH_PENDING' else ''))
        received = data['received']
        if received.get('received'):
            self.set('delivery.download', '成片：100% · 已接收校验' + ('，质量验收完成' if received.get('quality_checked') else '，等待质量验收'), 100)
        elif data.get('download_total'):
            value = ratio(data['download_bytes'], data['download_total'])
            self.set('delivery.download', f"成片下载：{percent(value)} · {data['download_bytes']/1024**3:.2f} / {data['download_total']/1024**3:.2f} GiB", value)
        else:
            self.set('delivery.download', '成片尚未发布 · 本机自动接收器待命')
        devices = []
        for row in data.get('gpu', []):
            fields = [x.strip() for x in row.split(',')]
            if len(fields) >= 6:
                devices.append(f'{fields[1]} {fields[2]}% · {fields[5]}°C')
        disks = ' / '.join(f"{d['name']} 余 {d['free_gib']:.1f} GiB" for d in data.get('disks', []))
        quota = data.get('quota_remaining')
        self.telemetry.configure(text='   |   '.join(devices) + '\n' + disks +
                                 f'   |   云端额度 {quota if quota is not None else "未知"}%（最近巡检值）')
        self.last_success = datetime.now().strftime('%H:%M:%S')
        self.updated.configure(text=f'最近成功更新：{self.last_success}  ·  百分比来自条目/分段计数，不代表整片质量验收')

    def drain(self):
        try:
            data, error = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.refresh_button.configure(state='normal')
            if error:
                self.connection.configure(text='● 连接暂不可用 · 保留上次数据', fg=AMBER)
                self.updated.configure(text=f'最近成功更新：{self.last_success or "尚无"}  ·  {error[:150]}')
            else:
                self.render(data)
                path = self.root / '.kwjd/jigong-monitor.json'
                temp = path.with_suffix('.json.tmp')
                temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(path)
            self.win.after(self.interval * 1000, self.refresh)
        self.win.after(200, self.drain)

    def close(self):
        self.closed.set()
        self.win.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--interval', type=int, default=20)
    args = parser.parse_args()
    if not 10 <= args.interval <= 300:
        parser.error('Refresh interval must be 10..300 seconds')
    Monitor(args.root.resolve(), args.config, args.interval).win.mainloop()


if __name__ == '__main__':
    main()
