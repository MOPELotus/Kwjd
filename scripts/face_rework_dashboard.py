#!/usr/bin/env python3
"""Read-only terminal view of the independent face rework observer."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import time


def render(root):
    path=root/'monitor.json'
    try:
        data=json.loads(path.read_text()); state=data.get('state',{}); service=data.get('service',{})
        age=max(0,time.time()-path.stat().st_mtime)
        done=int(state.get('frames_completed',0)); total=int(state.get('total_frames',0))
        verified=int(data.get('verified_frames',0)); pct=100*done/total if total else 0
        remaining=data.get('estimated_remaining_inference_seconds')
        eta=f'{remaining/3600:.1f} 小时' if isinstance(remaining,(int,float)) else '待积累分段'
        handoff=None
        acceptance=root/'server_acceptance.json'
        report=root.parents[2]/'post/reports/S01E49.face-rework.json'
        if acceptance.exists() and report.exists():
            candidate=json.loads(report.read_text())
            approval=json.loads(acceptance.read_text())
            if (candidate.get('server_acceptance_sha256')==hashlib.sha256(acceptance.read_bytes()).hexdigest()
                    and approval.get('server_accepted') is True
                    and candidate.get('sha256')==approval.get('sha256')
                    and candidate.get('status')=='SERVER_READY_PENDING_WINDOWS'):
                handoff=candidate
        stage=('SERVER_READY_PENDING_WINDOWS' if handoff and not state.get('windows_received')
               else state.get('stage','UNKNOWN'))
        lines=[
            'S01E49 全帧修脸返工 | 只读实时监测',
            f"阶段：{stage} | 服务：{service.get('ActiveState','unknown')}/{service.get('SubState','unknown')}",
            f"遥测：{age:.0f} 秒前{'（已过期，请检查 observer）' if age>90 else ''} | {data.get('at','')}",
            f'实际已处理：{done:,} / {total:,} 帧（{pct:.2f}%）',
            f'已完成严格分段校验：{verified:,} 帧 / {data.get("verified_segments",0)} 段',
            f"当前段：{state.get('current_segment','?')} | 段内速度：{state.get('attempt_segment_fps',0):.2f} fps",
            f"暂估剩余推理：{eta}（按累计分段速度；不含暂停、拼接和最终审校）",
            '第一回保留全部现有60fps帧；仅GFPGAN，不再次RIFE或烧字幕',
            f"worker记录PID（可能已退出）：{state.get('pid','?')} | 服务自动重试次数：{service.get('NRestarts','?')}",
            f"工作磁盘空闲：{data.get('free_gib',0):.2f} GiB（底线25 GiB）",
            'GPU遥测：编号 / 设备 / 温度 / 显存已用 / 显存空闲 / 利用率',
            str(data.get('gpu','unknown')),
            '显卡0修脸；显卡1当前闲置正常。保护：显卡0温度95°C、空闲3GiB。',
            ('Windows新SHA/媒体/PCM技术接收已通过；实际画面质量复核仍须单独确认。'
             if state.get('windows_received') else
             '服务器整片技术/混音与郭德纲有界视觉复核通过；Windows新版接收和质量回执待完成。'
             if handoff else '进度不是最终验收：整片解码、原混音一致性、视觉和Windows新版交付仍需完成。'),
            '', '近期日志：'
        ]
        if handoff:
            lines[2:2]=['交接清单：'+str(report),
                        '郭德纲37:16–37:33远景小脸保持柔软；观众效果已由用户豁免。',
                        '独立新版：'+str(handoff['final_path'])]
        log=root/'progress.log'
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-16384)); tail=f.read().decode(errors='replace').splitlines()
        lines.extend(tail[-5:]); lines.extend(['','Ctrl-b d 脱离tmux；Ctrl-c仅退出本窗口，不停止处理。'])
        return '\n'.join(lines)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return f'监测数据暂不可读：{exc}\n只读面板不会停止或重启worker。'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True,type=Path)
    p.add_argument('--once',action='store_true');args=p.parse_args()
    try:
        while True:
            text=render(args.root)
            print(('' if args.once else '\033[2J\033[H')+text,flush=True)
            if args.once:return
            time.sleep(2)
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
