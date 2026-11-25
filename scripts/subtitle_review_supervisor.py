"""Persistent same-session cloud subtitle proofreading, not an OCR/seal shortcut."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import time
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kwjd import read_srt
from subtitle_review_ass import ensure_ass,validate_ass,WaitStyle,WaitReview

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    temporary.replace(path)

def quota():
    tool=Path.home()/".local/bin/codex-quota-status"
    result=subprocess.run([str(tool)],capture_output=True,text=True,timeout=45,check=True)
    values=[float(v) for v in re.findall(r"([0-9]+(?:[.][0-9]+)?)%",result.stdout)]
    if not values:
        raise ValueError("Real quota response has no percentage")
    return min(values)

def review_contract_complete(report):
    """Keep old approvals intact; new focused reports disclose waived audits."""
    policy=report.get('review_policy')
    if policy is None:
        return all(report.get(key) is True for key in ['gap_audit_complete',
            'boundary_audit_complete','short_line_audit_complete','two_line_audit_complete'])
    config=Path(__file__).resolve().parents[1]/'config/projects/jigong-s03.json'
    authorized=json.loads(config.read_text()).get('subtitle_review',{})
    if policy!='focused-v1' or authorized.get('policy')!=policy:return False
    omitted=report.get('omitted_audits')
    return (all(report.get(key) is True for key in ['text_review_complete',
            'timing_review_complete','targeted_review_complete'])
        and isinstance(omitted,list) and all(isinstance(value,str) for value in omitted)
        and len(omitted)==len(set(omitted))
        and set(omitted)==set(authorized['disabled_routine_audits']))

def validate_episode(root,episode):
    review=root/"ocr/subtitle-review"
    source=root/"input"/(episode+".mp4")
    draft=root/"output/drafts"/(episode+".zh.srt")
    final=root/"output"/(episode+".zh.srt")
    report_path=review/"reports"/(episode+".json")
    if not all(p.is_file() for p in [source,draft,final,report_path,final.with_suffix(".srt.ready.json")]):
        return False
    report=json.loads(report_path.read_text())
    seal=json.loads(final.with_suffix(".srt.ready.json").read_text())
    source_sha=sha(source); draft_sha=sha(draft); final_sha=sha(final)
    count=len(read_srt(draft))
    if (report.get("complete") is not True or report.get("source_sha256")!=source_sha
            or report.get("draft_sha256")!=draft_sha or report.get("final_srt_sha256")!=final_sha
            or report.get("reviewed_original_cue_ids")!=list(range(1,count+1))
            or not review_contract_complete(report)
            or report.get("open_issues")!=[]
            or seal.get("reviewed") is not True or seal.get("sha256")!=final_sha):
        return False
    evidence=report.get("evidence")
    sessions=report.get("model_review_sessions")
    if not isinstance(evidence,list) or not evidence or not isinstance(sessions,list) or not sessions:
        return False
    for item in evidence:
        path=Path(item["path"])
        if not path.resolve().is_relative_to(review.resolve()) or sha(path)!=item["sha256"]:
            return False
    cues=read_srt(final)
    if not cues or any(end<=start for start,end,text in cues) or any(cues[i][0]<cues[i-1][1] for i in range(1,len(cues))):
        return False
    return True

PROMPT = """
继续同一原生字幕代理的济公传13回任务，读取ROOT/repo/AGENTS.md与docs/SUBTITLE_REVIEW.md，按2026-11-26用户节省额度授权 focused-v1执行，保留已有进度、证据、封存SRT/ASS，不启动新代理或旧exec监督器。
逐条核对原片文字/空格、删除台标污染、检查时间轴一遍。正常清楚的条目只需一张可读原帧，不强制反复看首中末；根据变化和疑点补图。明显异常、补漏句、重新拆分及疑难字符用必要邻帧/有限原音或ASR复核修正；清楚画面优先，不按语义改写。不要求普通条目每个边界40ms精确逐帧取证。
停止常规整回空档全帧、短句全帧、全画面逐秒、独立全边界二遍、未改正文二遍；不生成这些大专项。不为追求零个偶发小错重复全片审图。按实际已做范围记录；跳过的专项列omitted_audits，不伪造旧*_audit_complete=true。
每条原cue真实处置后，修正项/已知疑点复核，无未解决文字歧义，保存source/draft/final SHA、全部cue ID、实际证据SHA与会话ID。完成报告review_policy:focused-v1，text_review_complete/timing_review_complete/targeted_review_complete为真实true，omitted_audits与config/projects/jigong-s03.json的disabled_routine_audits相同；其余完整性、无重叠及seal门禁照常。随后真实seal并生成source-clock ASS，复用既有样式，不套母版偏移。已完成旧严格版报告保持原样。
使用ROOT/ocr/subtitle-review/progress.json保存进度。真实额度低于2%保存暂停模型，后台媒体继续，不兑卡/用点数。实际写盘留5GiB，按需要小批取证，不为重复专项耗盘。不得动媒体Worker、原片、母版、Windows新缓存或其它项目。此任务不启动任何监督器，只接续现有原生代理。
"""

def refresh_ass(root,state):
    styled=[]
    for episode in [f"S03E{i:02d}" for i in range(2,15)]:
        seal=root/"output"/(episode+".zh.srt.ready.json")
        report=root/"ocr/subtitle-review/reports"/(episode+".json")
        if not seal.is_file() or not report.is_file():
            continue
        try:
            ensure_ass(root,episode)
            if validate_ass(root,episode):
                styled.append(episode)
        except (WaitStyle,WaitReview) as error:
            state["style_wait_reason"]=str(error)
        except (ValueError,KeyError,OSError) as error:
            state["ass_error"]=str(error)
    state["styled_episodes"]=styled
    return styled

def run_turn(root,state,prompt,images,number):
    review=root/"ocr/subtitle-review"
    log=review/"logs"/f"turn-{number:04d}.jsonl"
    message=review/"logs"/f"turn-{number:04d}.last.txt"
    command=["codex","exec","--json","--output-last-message",str(message),
             "--cd",str(root/"repo"),"--sandbox","danger-full-access",
             "-c",'approval_policy="never"']
    if state.get("session_id"):
        command=["codex","exec","resume","--json","--output-last-message",str(message),
                 "-c",'approval_policy="never"',state["session_id"]]
    for image in images:
        command += ["-i",str(image)]
    command += ["-"]
    with log.open("x",encoding="utf8") as out:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out,stderr=subprocess.STDOUT,
                                 cwd=root/"repo",text=True,start_new_session=True)
        process.stdin.write(prompt); process.stdin.close()
        state.update(stage="REVIEW_SESSION_RUNNING",pid=process.pid,current_log=str(log),turn=number)
        save(review/"supervisor.json",state)
        while process.poll() is None:
            time.sleep(10)
            try:
                remaining=quota();state["quota_remaining_percent"]=remaining
                if remaining<2:
                    os.killpg(process.pid,signal.SIGTERM)
                    state["stage"]="WAIT_QUOTA_RECOVERY"
                    save(review/"supervisor.json",state)
                    raise RuntimeError("Real quota fell below 2%; saved review")
            except subprocess.SubprocessError as error:
                state["quota_read_error"]=str(error)
            for line in log.read_text(encoding="utf8",errors="replace").splitlines()[:100]:
                try:
                    event=json.loads(line)
                    if event.get("type")=="thread.started":
                        state["session_id"]=event["thread_id"]
                except (ValueError,KeyError):
                    pass
            refresh_ass(root,state)
            save(review/"supervisor.json",state)
    state["last_returncode"]=process.returncode
    save(review/"supervisor.json",state)
    return process.returncode

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--execute",action="store_true")
    args=parser.parse_args();root=args.root.resolve(strict=True)
    review=root/"ocr/subtitle-review";(review/"logs").mkdir(parents=True,exist_ok=True)
    lock=(review/"supervisor.lock").open("a")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    episodes=[f"S03E{i:02d}" for i in range(2,15)]
    if not args.execute:
        print(json.dumps(dict(episodes=episodes,state="PLAN_ONLY")))
        return
    state_path=review/"supervisor.json"
    state=json.loads(state_path.read_text()) if state_path.exists() else dict(stage="STARTING",turn=0)
    number=state.get("turn",0)
    first=True
    while True:
        remaining=quota()
        if remaining<2:
            state.update(stage="WAIT_QUOTA_RECOVERY",quota_remaining_percent=remaining)
            save(state_path,state);return
        if shutil.disk_usage(review).free<7*1024**3:
            state["stage"]="WAIT_DISK_RESERVE";save(state_path,state);return
        completed=[ep for ep in episodes if validate_episode(root,ep)]
        styled=refresh_ass(root,state)
        state.update(completed_episodes=completed,styled_episodes=styled,
                     quota_remaining_percent=remaining,
                     persistent_target="13 reviewed/sealed SRT plus 13 proven ASS style files",
                     ambiguous_text_policy="original audio/ASR assistance plus source-pixel adjudication")
        if len(completed)==len(episodes) and len(styled)==len(episodes):
            state.update(stage="ALL_13_REVIEWED_SEALED_AND_ASS_READY")
            save(state_path,state);return
        if len(completed)==len(episodes):
            state.update(stage="WAIT_SHARED_STYLE_PROOF_OR_ASS")
            save(state_path,state);time.sleep(30);continue
        prompt=PROMPT.replace("ROOT",str(root))
        if state.get("session_id"):
            prompt=PROMPT.replace("ROOT",str(root))+"\n已验证完成集："+",".join(completed)+"。接续同一原生代理，不重审已封存集。"

        images=[]
        if first and not state.get("session_id"):
            manifest=review/"evidence/S03E02/0000-v1/evidence.json"
            if manifest.exists():
                images=[Path(item["path"]) for item in json.loads(manifest.read_text())["pages"]]
        number+=1; first=False
        result=run_turn(root,state,prompt,images,number)
        state["last_finished_at"]=time.time();save(state_path,state)
        if result:
            state["consecutive_cli_errors"]=state.get("consecutive_cli_errors",0)+1
            if state["consecutive_cli_errors"]>=3:
                state["stage"]="CLI_ERROR_REQUIRES_INSPECTION";save(state_path,state);return
            time.sleep(30)
        else:
            state["consecutive_cli_errors"]=0
            time.sleep(3)

if __name__=="__main__":
    main()

