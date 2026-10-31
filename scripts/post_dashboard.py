#!/usr/bin/env python3
"""Read-only terminal view of post-monitor receipts, telemetry and bounded logs."""
from __future__ import annotations

import argparse
import csv
import curses
from datetime import datetime, timezone
import json
import locale
import math
from pathlib import Path
import re
import subprocess
import unicodedata

TAIL_BYTES=128*1024
PHASES={'ENHANCING':'真实 AI 修复与补帧','PROCESSING':'字幕烧录与编码',
        'READY':'成片已技术验收','ENHANCED_READY':'增强完成，等待后续处理',
        'WAIT_DISK':'等待磁盘余量','WAIT_GPU':'等待 GPU 可用',
        'WAIT_BACKEND_TECHNICAL':'等待后端技术验证'}


def read_json(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def timestamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result if result.tzinfo else None
    except (AttributeError,TypeError,ValueError):return None


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def hms(seconds):
    if not finite(seconds) or seconds<0:return 'unknown'
    seconds=int(seconds)
    return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'


def tail_log(path):
    """At most 128 KiB, dropping a potentially partial first line."""
    try:
        with path.open('rb') as stream:
            stream.seek(0,2);size=stream.tell();start=max(0,size-TAIL_BYTES)
            stream.seek(start);data=stream.read(TAIL_BYTES)
            modified=stream.fileno()
            import os
            mtime=os.fstat(modified).st_mtime
        if start:
            data=data.partition(b'\n')[2]
        return data.decode('utf-8',errors='replace').splitlines(),mtime
    except OSError:return [],None


def recent_rate(lines,episode):
    records=[]
    for line in lines:
        try:row=json.loads(line)
        except ValueError:continue
        if (not isinstance(row,dict) or row.get('cuda_pipeline') is not True
                or row.get('episode')!=episode):continue
        frames=row.get('input_frames');wall=row.get('wall_seconds');start=row.get('source_start')
        if (type(frames) is int and frames>0 and finite(wall) and wall>0
                and type(start) is int and start>=0):
            records.append((start,frames,wall))
    # A resumed log can contain repeated segment indices; use the latest record
    # for each, not duplicate old measurements in the weighted mean.
    selected=[];seen=set()
    for row in reversed(records):
        if row[0] not in seen:
            selected.append(row);seen.add(row[0])
        if len(selected)==3:break
    if not selected:return None
    return {'source_fps':sum(x[1] for x in selected)/sum(x[2] for x in selected),
            'segments':len(selected),'input_frames':sum(x[1] for x in selected),
            'wall_seconds':sum(x[2] for x in selected),
            'latest_source_end':selected[0][0]+selected[0][1]}


def process_status(pid,expected):
    if type(pid) is not int or pid<=0:return 'unknown'
    try:
        cmdline=(Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\0',b' ')
        # A live recycled PID is not evidence that our worker is alive.
        if expected.encode() not in cmdline:return '未运行或 PID 已复用'
        return '存活（进程身份匹配）'
    except OSError:return '未运行或不可读'


def native_worker_pid(post,episode,proc_root=Path('/proc')):
    """Read the matching native service's live process independently of ETA."""
    script=Path(__file__).resolve().with_name('server_worker.py')
    if (not isinstance(episode,str) or not re.fullmatch(r'S01E\d{2,3}',episode)
            or Path(post).resolve()!=(script.parents[2]/'post').resolve()):return None
    try:
        unit='kwjd-native-production-'+episode[3:].lower()+'.service'
        result=subprocess.run(['systemctl','--user','show',unit,'--property=MainPID','--value'],
                              capture_output=True,text=True,check=True,timeout=1)
        pid=int(result.stdout.strip())
        if pid<=0:return None
        process=proc_root/str(pid)
        before=(process/'stat').read_text().rsplit(')',1)[1].split()
        argv=[x.decode(errors='replace') for x in (process/'cmdline').read_bytes().split(b'\0') if x]
        after=(process/'stat').read_text().rsplit(')',1)[1].split()
        if (before[19]!=after[19] or before[0] in ('Z','X') or after[0] in ('Z','X')
                or str(script) not in argv or argv.count('--episode')!=1
                or argv[argv.index('--episode')+1]!=episode):return None
        return pid
    except (OSError,ValueError,IndexError,subprocess.SubprocessError):return None


def validation_status(post,episode):
    record=read_json(post/'reports/active_validation.json')
    if record.get('episode')!=episode:return None
    pid=record.get('pid');name=record.get('log')
    if type(pid) is not int or pid<=0 or not isinstance(name,str) or Path(name).name!=name:return None
    try:
        proc=Path('/proc')/str(pid)
        fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
        if (fields[19]!=str(record.get('starttime')) or fields[0] in ('Z','X')
                or b'/scripts/segmented_backend.py' not in (proc/'cmdline').read_bytes()):return None
    except (OSError,IndexError):return None
    lines,_=tail_log(post/'logs'/name);done=0
    total=record.get('source_frames_total')
    if type(total) is not int or total<=0:return None
    for line in lines:
        try:row=json.loads(line)
        except ValueError:continue
        if not isinstance(row,dict) or row.get('episode')!=episode:continue
        value=row.get('source_frames_complete')
        if type(value) is int and 0<=value<=total and row.get('source_frames_total')==total:done=value
    return {'label':record.get('label','技术验证'),'pid':pid,'done':done,'total':total,
            'rate':recent_rate(lines,episode)}


def collect(post,episode,now=None):
    now=now or datetime.now(timezone.utc)
    snap=read_json(post/'reports/live_status.json')
    captured=timestamp(snap.get('captured_at'));age=(now-captured).total_seconds() if captured else None
    stale=age is None or age>120 or age < -5
    jobs=snap.get('jobs',[])
    if not isinstance(jobs,list):jobs=[]
    sampled=next((j for j in jobs if isinstance(j,dict) and j.get('episode')==episode),{})
    job=read_json(post/'jobs'/f'{episode}.json')
    if job.get('episode')!=episode:job={}
    progress=sampled.get('segment_progress',{})
    if not isinstance(progress,dict):progress={}
    production=read_json(post/'backend/production-ready.json')
    # Frozen/stale telemetry is visible as such, but must also refer to this job
    # and current production fingerprint rather than a previous source/profile.
    digest=job.get('master_sha256');fp=production.get('backend_fingerprint')
    valid_identity=(isinstance(digest,str) and bool(re.fullmatch('[0-9a-f]{64}',digest))
                    and isinstance(fp,str) and bool(re.fullmatch('[0-9a-f]{64}',fp)))
    expected_context=(post/'cache'/episode/(episode+'.segments')/(digest[:16]+'-'+fp[:16])/'context.json'
                      if valid_identity else None)
    context=read_json(expected_context) if expected_context else {}
    bound=(job and valid_identity and production.get('master_sha256')==digest
           and progress.get('backend_fingerprint')==production.get('backend_fingerprint')
           and progress.get('context')==str(expected_context)
           and context.get('master_sha256')==digest and context.get('backend_fingerprint')==fp)
    if not bound:progress={'status':'unknown','reason':'当前任务/后端与快照证据未匹配'}
    eta=read_json(post/'reports'/f'{episode}.eta.json')
    eta_bound=(bool(job) and valid_identity and eta.get('episode')==episode
               and eta.get('master_sha256')==job.get('master_sha256')
               and eta.get('backend_fingerprint')==production.get('backend_fingerprint')
               and bool(production.get('backend_fingerprint')))
    if not eta_bound:eta={}
    final_phase=job.get('state')=='PROCESSING'
    thermal=read_json(post/'reports'/('final_encode_thermal_pause.json' if final_phase else 'thermal_pause.json'))
    thermal_bound=(thermal.get('episode')==episode and thermal.get('master_sha256')==digest
                   and thermal.get('backend_fingerprint')==fp)
    paused=False
    if thermal_bound and thermal.get('owned_stop') is True and type(thermal.get('backend_pid')) is int:
        try:
            process=Path('/proc')/str(thermal['backend_pid'])
            fields=(process/'stat').read_text().rsplit(')',1)[1].split()
            cmd=(process/'cmdline').read_bytes()
            if final_phase:
                argv=[x.decode(errors='replace') for x in cmd.split(b'\0') if x]
                command_matches=(argv[:1]==['ffmpeg'] and argv==thermal.get('backend_cmdline')
                                 and thermal.get('stage')=='FINAL_ENCODE')
            else:command_matches=b'/scripts/segmented_backend.py' in cmd
            paused=(fields[0] in ('T','t') and command_matches
                    and fields[19]==str(thermal.get('backend_starttime'))
                    and fields[1]==str(thermal.get('worker_pid')))
        except (OSError,IndexError):pass
    logs,mtime=tail_log(post/'logs'/f'{episode}.log')
    rate=recent_rate(logs,episode)
    updated=timestamp(job.get('updated_at'))
    if (stale or paused or job.get('state')!='ENHANCING' or mtime is None
            or now.timestamp()-mtime>300 or now.timestamp()-mtime < -5
            or (updated and mtime<updated.timestamp())
            or progress.get('status')!='known'):
        rate=None
    if rate:
        completed=progress.get('source_frames_complete');total=progress.get('source_frames_total')
        # Reject old-run tail records far beyond the current receipt frontier.
        if (type(completed) is not int or type(total) is not int
                or not max(0,completed-4500)<=rate['latest_source_end']<=min(total,completed+1500)):
            rate=None
    size=context.get('segment_source_frames');total=progress.get('source_frames_total')
    segments_total=((total+size-1)//size if type(total) is int and total>0
                    and type(size) is int and size>0 else 'unknown')
    return {'episode':episode,'snapshot':snap,'sampled_job':sampled,'job':job,'now':now,
            'segments_total':segments_total,'thermal':thermal if thermal_bound else {},'thermal_paused':paused,
            'placement_validation':bool(thermal_bound and thermal.get('state')=='VALIDATING_GPU_PLACEMENT'),
            'validation':validation_status(post,episode),
            'progress':progress,'stale':stale,'age':age,'eta':eta,'rate':rate,
            'logs':logs[-3:],'worker_pid':eta.get('worker_pid') or native_worker_pid(post,episode),
            'monitor_pid':snap.get('monitor_pid')}


def clean(text):
    return ''.join(c if c==' ' or not unicodedata.category(c).startswith('C') else ' ' for c in str(text))


def clip(text,width):
    result=[];used=0
    for char in clean(text):
        size=0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('W','F') else 1
        if used+size>max(0,width):break
        result.append(char);used+=size
    return ''.join(result)


def render(view):
    snap=view['snapshot'];job=view['job'] or view['sampled_job'];state=job.get('state','unknown')
    if view.get('thermal_paused'):state='THERMAL_PAUSE'
    if view.get('placement_validation'):state='VALIDATING_GPU_PLACEMENT'
    age=view['age'];age_text=f'{max(0,age):.0f}秒前' if finite(age) else 'unknown'
    freshness='STALE 陈旧快照，以下遥测仅为上次记录' if view['stale'] else '快照有效（monitor 约30秒更新）'
    phase=('温度保护降温，已完成分段保留' if state=='THERMAL_PAUSE' else
           '同模型单卡方案技术验证，已完成分段保留' if state=='VALIDATING_GPU_PLACEMENT' else PHASES.get(state,'等待或状态未知'))
    lines=[f"{view['episode']} 后处理进度 | 只读面板",f'阶段：{phase} | 状态：{state}',
           '任务最近更新：'+str(job.get('updated_at','unknown')),
           f'遥测：{freshness} | {age_text} | {snap.get("captured_at","unknown")}']
    validation=view.get('validation')
    if validation:
        lines.append(f'当前测试：{validation["label"]} | {validation["done"]:,}/{validation["total"]:,} 源帧 '
                     f'({100*validation["done"]/validation["total"]:.1f}%) | PID {validation["pid"]}')
        if validation['rate']:
            lines.append(f'测试近期吞吐：{validation["rate"]["source_fps"]:.3f} 源帧/秒；测试进度不计入整回完成率')
    p=view['progress'];done=p.get('source_frames_complete');total=p.get('source_frames_total');percent=p.get('percent')
    known=(p.get('status')=='known' and type(done) is int and type(total) is int and total>0
           and 0<=done<=total and finite(percent) and 0<=percent<=100)
    if known:
        n=int(percent*24/100);bar='['+'#'*n+'-'*(24-n)+']'
        label='整回保留分段' if view.get('placement_validation') else '分段'
        lines.extend([f'{label} {bar} {percent:.3f}% | 源帧 {done:,}/{total:,} | 已完成段 {p.get("verified_segments","unknown")}/{view.get("segments_total","unknown")}',
                      f'已处理源视频：{hms(done/25)} / {hms(total/25)}（25fps 源时钟）'])
    else:lines.append('分段进度：unknown | '+str(p.get('reason','尚无匹配的已验证分段收据')))
    lines.append('进度仅为收据记录；未重读媒体哈希；百分比不代表最终成片验收')
    if view.get('thermal_paused'):
        lines.append('温度保护：'+str(view.get('thermal',{}).get('reason','等待全部 GPU 降到恢复阈值')))
        thermal=view.get('thermal',{});temps=thermal.get('temperatures') or {}
        details=[]
        for index,item in temps.items():
            if isinstance(item,dict):details.append(f'GPU{index} {item.get("current","?")}°C → 恢复≤{item.get("resume","?")}°C')
        if details:lines.append('恢复门槛：'+' | '.join(details))
        lines.append('累计温控暂停：'+hms(thermal.get('paused_seconds')))
    eta=view['eta'];hours=eta.get('estimated_hours_from_full_start_through_final_checks')
    if isinstance(hours,list) and len(hours)==2 and all(finite(x) and x>0 for x in hours):
        lines.append(f'全流程估计：从整回启动计 {hours[0]}–{hours[1]} 小时（非剩余时间）')
    elif eta.get('state')=='THERMAL_POLICY_REVALIDATION':
        lines.append('全流程 ETA：温控占空比与安全运行方案实测中；旧估计已撤回')
    else:lines.append('全流程 ETA：unknown（缺少当前母片/后端匹配估计）')
    completion=eta.get('expected_completion_beijing_range')
    if isinstance(completion,list) and len(completion)==2:
        lines.append('预计完成窗口（北京时间）：'+' ～ '.join(str(x) for x in completion))
        bounds=[timestamp(x) for x in completion]
        now=view.get('now',datetime.now(timezone.utc))
        if all(bounds) and bounds[1]>=bounds[0]:
            if now>bounds[1]:lines.append('预计剩余：原 ETA 窗口已过，需重新估计；不代表已完成')
            elif state=='READY':lines.append('预计剩余：成片已通过技术验收')
            else:lines.append('预计剩余：'+' ～ '.join(hms(max(0,(x-now).total_seconds())) for x in bounds))
    rate=view['rate']
    lines.append((f'近期 AI 链吞吐：{rate["source_fps"]:.3f} 源帧/秒（最近 {rate["segments"]} 段合计 '
                  f'{rate["input_frames"]} 帧 / {rate["wall_seconds"]:.1f} 秒）') if rate else '近期 AI 链吞吐：unknown（尚无新鲜且匹配的日志样本）')
    lines.append('上述吞吐不含后续字幕烧录、整回拼接和最终验收')
    lines.append('GPU（遥测快照）：设备 | 温度 | SM时钟 | 利用率 | 显存已用/空闲 | 热限频')
    fields=snap.get('gpu_csv_fields','');rows=snap.get('gpu_csv_rows',[])
    if isinstance(fields,str) and isinstance(rows,list):
        keys=fields.split(',')
        for row in rows:
            if not isinstance(row,str):continue
            values=next(csv.reader([row],skipinitialspace=True));gpu=dict(zip(keys,values))
            try:memory=f'{int(gpu["memory.used"])}/{int(gpu["memory.free"])} MiB'
            except (KeyError,ValueError):memory='unknown'
            lines.append(f'GPU {gpu.get("index","?")} {gpu.get("name","unknown")} | '
                         f'{gpu.get("temperature.gpu","?")}°C | {gpu.get("clocks.sm","?")} MHz | '
                         f'{gpu.get("utilization.gpu","?")}% | {memory} | '
                         f'{gpu.get("clocks_event_reasons.sw_thermal_slowdown","unknown")}')
    disks=snap.get('filesystems',{})
    if isinstance(disks,dict):
        for path,data in disks.items():
            free=data.get('free_bytes') if isinstance(data,dict) else None
            lines.append(f'磁盘余量：{path} | '+(f'{free/1024**3:.2f} GiB' if finite(free) and free>=0 else 'unknown'))
    for name,key,expected in [('worker','worker_pid','server_worker.py'),('monitor','monitor_pid','post_monitor.py')]:
        pid=view[key];lines.append(f'{name} PID {pid if type(pid) is int else "unknown"}：{process_status(pid,expected)}')
    lines.append('最新日志（最多读取尾部128KiB，展示末3行）：')
    lines.extend(clip(line,180) for line in view['logs'])
    return [clean(line) for line in lines]


def panel(screen,post,episode):
    try:curses.curs_set(0)
    except curses.error:pass
    screen.timeout(2000)
    while True:
        height,width=screen.getmaxyx();screen.erase()
        lines=render(collect(post,episode))
        for y,line in enumerate(lines[:max(0,height-1)]):
            try:screen.addstr(y,0,clip(line,max(0,width-1)))
            except curses.error:pass
        if height>0:
            try:screen.addstr(height-1,0,clip('q 退出面板（不停止 worker） | Ctrl-b d 脱离 tmux | 面板2秒刷新',max(0,width-1)))
            except curses.error:pass
        screen.refresh()
        if screen.getch() in (ord('q'),ord('Q')):return


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--post',default=str(Path.home()/'qingqingwu/post'))
    parser.add_argument('--episode',default='S01E49');parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'S01E\d{2,3}',args.episode):parser.error('Invalid episode ID')
    try:locale.setlocale(locale.LC_ALL,'')
    except locale.Error:pass
    if args.once:print('\n'.join(render(collect(Path(args.post),args.episode))))
    else:
        try:curses.wrapper(panel,Path(args.post),args.episode)
        except KeyboardInterrupt:pass


if __name__=='__main__':main()
