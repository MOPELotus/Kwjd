#!/usr/bin/env python3
"""Durable single-chapter cloud postproduction, independent of Windows uptime."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

EPISODE='S03E02'
MASTER='0aaa63c922cd92e211c3515003169aed8c466251b66f4b233e539cb62cb0baa6'
EXPECTED_FRAMES=222154

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)

def quota():
    result=subprocess.run([str(Path.home()/'.local/bin/codex-quota-status')],text=True,capture_output=True,timeout=45,check=True)
    values=[float(value) for value in re.findall(r'([0-9]+(?:[.][0-9]+)?)%',result.stdout)]
    if not values:raise ValueError('No real quota percentage returned')
    return min(values)

def complete(root):
    manifest=root/'post/handoffs'/f'{EPISODE}.cloud-final.json'
    if not manifest.exists():return False
    data=json.loads(manifest.read_text())
    if (data.get('episode')!=EPISODE or data.get('master_sha256')!=MASTER
        or data.get('final_video_frame_count')!=EXPECTED_FRAMES
        or data.get('technical_checked') is not True or data.get('server_quality_checked') is not True):return False
    for path_key,hash_key in [('final_path','sha256'),('report_path','report_sha256'),('quality_evidence_path','quality_evidence_sha256')]:
        path=Path(data[path_key])
        if not path.is_absolute() or not path.resolve().is_relative_to((root/'post').resolve()) or path.is_symlink() or sha(path)!=data[hash_key]:return False
    report=json.loads(Path(data['report_path']).read_text())
    return (report.get('master_sha256')==MASTER and report.get('sha256')==data['sha256']
        and report.get('final_decode_checked') is True and report.get('final_pcm_equivalent') is True
        and report.get('final_video_frame_count')==EXPECTED_FRAMES)

PROMPT = '用户要求云端全托管《济公传/罗汉爷捉拿华云龙》第一回S03E02到真正成品，Windows不常开，在线自动取回最后压制成片。你是持久服务器后期执行者，持续实际工作到这一回云端成品校验完成；不是只规划/写ready/PID。只这一回后期，不扩展13回。先读 ~/.codex/AGENTS.md、repo/AGENTS.md、docs/WORKFLOW.md、BACKENDS.md、JIGONG_PREPARATION.md、LIFECYCLE.md。quota-status真实低于5%保存现场停止，不造余额。保护其他用户GPU进程，磁盘每个文件系统计峰值仍留25GiB。\n\n数据根ROOT；repo=ROOT/repo；分支master，正常fetch+merge+只提交自己源码+push，无force。新增scripts/jigong_post_supervisor.py是你的持久管理器，不中止自己。先读ROOT/post/managed-S03E02/progress.json若有，接当前状态，不重复启动worker。通过KWJD_PROJECT=config/projects/jigong-s03.json运行kwjd/server_worker，不向默认S01入口提交S03。当前代码已适配25/30fps，三处benchmark实际时钟刚修；仍要查所有生产入口残留S01/25硬编码。\n\nWindows独立上传在进行，禁止修改/移动/写入 post/incoming/S03E02.clean-master.mov.uploading，不启动第二传输。只有最终 post/incoming/S03E02.clean-master.mov 和 post/handoffs/S03E02.master-received.json真实到位才可读取完整母版准入。预期sha=0aaa63c922cd92e211c3515003169aed8c466251b66f4b233e539cb62cb0baa6，10302910705 bytes，1920x1080 Main10，实际连续CFR30/1，111077帧，3702.566667秒，最终60fps应222154帧。用户明确接受30fps，不重导，远景少量桌子缺失正常，尾部2.150667秒背景是预期。中/近景桌子缺失仍需实查。PCM24/48kHz stereo原混音不改不归一化，拍桌0dB接受，峰值仅诊断。上传收据和后端启动是不同事实，不能混淆。\n\n现有独立13回字幕审校服务正在实际审图，不动它的进程和文件，不重复OCR，不拿草稿当通过。不改scripts/*subtitle_review*、tests/*subtitle_review*、docs/SUBTITLE_REVIEW.md。只等待output/S03E02.zh.srt及真实SHA reviewed ready；可import subtitle_review_supervisor.validate_episode严格核实报告。output/drafts和reviewing不是定版。当前已经有真实逐条修订，整回未seal。你可在等字幕时准备后端、字体、技术短测，不能伪造seal来跑生产。\n\n已建post/face-venv：torch2.6+cu126 torchvision0.21 numpy2.2.6 Pillow11.3 ORT-GPU1.23.2 onnx1.17 basicsr1.4.2 facexlib0.3 gfpgan1.3.8，实际cv2=5.1，按真实包hash绑定。官方GFPGAN/检测/解析weights在post/tools/face-candidates/weights，face_frame_processor.WEIGHTS定义真实SHA；RIFE官方v4.6 ONNX在post/tools/rife-onnx/rife_v4.6.onnx SHA6294aab9a9704f085caa65ab5bc897ac3a3ed0d054200e40b66bd7e43523e969。post/reports/rebuild-smoke-v1/report.json证明实际原片邻帧GFPGAN physical0 A4000和CUDA RIFE physical1 TeslaT10成功、GPU算子指派通过，只是模型短测，不是当前母版整链准入。旧TRT引擎/recipe/proof清理了，cuda_trt旧hash不能假造；选择独立可审CUDA ONNX FP32候选，不称TRT。prepare_native_face_backend目前仍依赖旧runtime.trt，需要实现/测试独立ONNX候选准入或真实重建TRT。GFPGAN在A4000保护现有约10GB服务，2.5GiB分配帽+3GiB余量；T10空闲，保持真实身份/温度/头room门禁。\n\n必须真实保持原生CAS轻量→GFPGAN→RIFE60→ASS最后烧→HEVC Main10 NVENC+24bit/48kHz FLAC MKV，优先native fused one-encode与可恢复分段，不偷偷关闭AI/改混音。必须实测真实当前母版整链、全包时钟、cut处理、字体实际libass解析、画面和声音一致性、较长稳态性能后绑定hash准入。短测不需要人工审批但不能伪造生产seal；必要时加明确只限小段/隔离输出的technical calibration scope，不能用旧S01 bounded_test特殊SHA冒充。最终生产等待真实sealed SRT和真实style审批/时钟映射。旧25/S01默认保持，不自动扩大新准入。\n\n现在先恢复MiSans字体和样式链：config/project.json及docs/STYLE.md、DECISIONS.md是之前确认标准MiSans Subtitle静态Demibold，MiSans VF named Demibold weight450，84pt outline3.5 shadow1 marginV54。真实字体/模板已清理，恢复在post/styles/fonts和post/styles/template.ass，二进制不进Git。官方来源/下载SHA记录、fonttools实际静态化和字体名称、fc-match和libass实渲染验证。BACKENDS明确已授权未来回目重用同样84设计，在实际新母版检查可读性后Codex可签新master绑定样式，不重复问同样样式；不能伪造用户新审批。先准备可审的具体真实母版预览，保留图片/检查说明，再如实生成已有授权派生的样式sidecar。字幕未定版时只能用明确标记的临时真实像素已审片段做style/technical预览，不称定版。父控期待字体和ASS进度，要留ROOT/post/managed-S03E02/progress.json方便查看。\n\n磁盘当前/home约55GiB、/约66GiB空闲，incoming约10.3GB。实时df+模型/segment+concat+final+FLAC峰值每挂载保留25GiB，可选用户可写另一挂载按仓库storage声明。不要删除源/母版/工程/唯一最终片，不以Windows源当云端备份。只按真实下游交接登记释放生成中间物；保持原混音全PCM一致证明。服务器最终片必须保留到Windows真正收到同SHA并技术+质量回执，离线不当失败、不唤醒Windows、不以final存在当交付。父控负责Windows自动在线接收器和当前上传后缓存清理，你不碰Windows/cache或上传脚本/validate_master/receive_master。最终发布原子可下载清单post/handoffs/S03E02.cloud-final.json，字段episode/master_sha256/final_path/sha256/report_path/report_sha256/final_video_frame_count=222154/technical_checked=true/server_quality_checked=true/quality_evidence_path/quality_evidence_sha256，仅真实完成才填true；server质量是实际抽帧画面/字幕口型音频诊断记录，不冒称用户听过全片。报告必须final_decode_checked和final_pcm_equivalent，frame数222154，原混音PCM不变。留待Windows真实接收验收，不能直接删除final。记录可下载状态不是已交付。\n\n标准Windows接收协议补充：发布post/final/S03E02.final-1080p60.mkv及post/reports/S03E02.json，报告含episode/sha256/master_sha256/master_duration/final_decode_checked/final_pcm_equivalent/decoded_pcm_sha256/bytes（实际最终字节数）。只有完整最终技术校验后原子发布报告。父控在线接收器会fresh SHA+媒体首中尾解码+完整PCM对照，record_final的quality_checked保持false，必须实际Codex样图/字幕/口型/声音检查再写质量收据，不能无人脚本假定true。用户追加13回字幕各回审完均生成ASS样式版；字体/template由你恢复，审校同事复用，不要互相覆盖。你仍只做S03E02增强生产。\n\n持久工作：ROOT/post/managed-S03E02/progress.json每实际阶段保存当前工作、证据路径、真实等待和下一步，不因一次CLI结束/Windows离线停止。自动重复动作写进版本化工具，别只丢shell。持续准备/实测/修问题，等待真正输入时10-60秒合理轮询，不密集模型请求；数小时字幕审校时可让协调器真实WAIT_SEALED_SRT并持久等待，继续已独立可做工作。真实阻塞记录且不假ready。工作跨上下文保存恢复。同一repo字幕Codex并行只改其review文件，普通merge保留。每个小源码改动有适度必要测试、立即commit/push，报告给父控从progress读取即可。现在实际开始字体和ONNX候选，不要只回复计划。\n\n与字幕服务接口：字体/模板完成实际校验后写post/styles/style-preflight.json，schema_version=1, technical_checked:true, visual_checked:true, template_path, template_sha256, fonts:{filename:sha}, font_selection_evidence_path/font_selection_evidence_sha256, preview_evidence_path/preview_evidence_sha256, authorized_design:{family:MiSans Subtitle,size:84,variation_weight:450,outline:3.5,shadow:1,margin_v:54}, master_sha256。真实完成才true，模板/font SHA精确绑定。字幕服务只读取该证明后为真实sealed SRT生成output/ass/S03E##.ass及绑定sidecar，时间轴为原裁片未施加master offset；你生产需按真实timing mapping派生最终ASS。字体资产完全由你拥有，审校服务不碰。'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args();root=args.root.resolve(strict=True)
    area=root/'post/managed-S03E02';(area/'logs').mkdir(parents=True,exist_ok=True)
    lock=(area/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if not args.execute:print(json.dumps(dict(episode=EPISODE,state='PLAN_ONLY')));return
    state_path=area/'supervisor.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else dict(turn=0)
    while True:
        try:remaining=quota()
        except Exception as error:
            state.update(stage='QUOTA_READ_ERROR',error=str(error));save(state_path,state);time.sleep(60);continue
        if remaining<5:
            state.update(stage='WAIT_QUOTA_RECOVERY',quota_remaining_percent=remaining);save(state_path,state);return
        if complete(root):
            state.update(stage='CLOUD_FINAL_VALIDATED_WINDOWS_RECEIPT_PENDING',completed_at=time.time());save(state_path,state);return
        number=state.get('turn',0)+1
        log=area/'logs'/f'turn-{number:04d}.jsonl';last=area/'logs'/f'turn-{number:04d}.last.txt'
        command=['codex','exec','--json','--output-last-message',str(last),'--cd',str(root/'repo'),'--sandbox','danger-full-access','-c','approval_policy="never"']
        prompt=PROMPT.replace('ROOT',str(root))
        if state.get('session_id'):
            command=['codex','exec','resume','--json','--output-last-message',str(last),'-c','approval_policy="never"',state['session_id']]
            prompt='继续同一S03E02服务器全托管后期任务。读取post/managed-S03E02/progress.json和真实进程/输入状态，从未完阶段继续实际工作；先查真实额度，不重复上传/OCR/worker，不伪造seal/ready。目标为当前30fps母版真实222154帧60fps最终成品，保留至Windows真实SHA技术+质量收据。尚未完成不能只总结停止。'
        command+=['-']
        with log.open('x') as stream:
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stream,stderr=subprocess.STDOUT,text=True,cwd=root/'repo',start_new_session=True)
            process.stdin.write(prompt);process.stdin.close()
            state.update(stage='POST_SESSION_RUNNING',pid=process.pid,turn=number,current_log=str(log),quota_remaining_percent=remaining)
            save(state_path,state);last_quota=time.monotonic()
            while process.poll() is None:
                time.sleep(10)
                for line in log.read_text(errors='replace').splitlines()[:5]:
                    try:
                        event=json.loads(line)
                        if event.get('type')=='thread.started':state['session_id']=event['thread_id']
                    except (ValueError,KeyError):pass
                if time.monotonic()-last_quota>=120:
                    try:
                        remaining=quota();state['quota_remaining_percent']=remaining
                        if remaining<5:
                            os.killpg(process.pid,signal.SIGTERM)
                            state['stage']='WAIT_QUOTA_RECOVERY';save(state_path,state);return
                    except Exception as error:state['quota_read_error']=str(error)
                    last_quota=time.monotonic()
                save(state_path,state)
        state.update(last_returncode=process.returncode,last_finished_at=time.time())
        errors=state.get('consecutive_cli_errors',0)+1 if process.returncode else 0
        state['consecutive_cli_errors']=errors;save(state_path,state)
        if errors>=3:
            state['stage']='CLI_ERROR_REQUIRES_INSPECTION';save(state_path,state);return
        time.sleep(30 if process.returncode else 10)

if __name__=='__main__':main()
