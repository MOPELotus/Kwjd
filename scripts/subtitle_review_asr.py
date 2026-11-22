"""Transcribe bounded original audio as review evidence, never replace captions."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def validate_interval(start,end):
    if (type(start) not in (int,float) or type(end) not in (int,float)
            or not math.isfinite(start) or not math.isfinite(end)
            or start<0 or not 0<end-start<=120):
        raise ValueError("Use a finite original-audio interval of at most 120 seconds")

def transcribe(root,episode,start,end,output,cache):
    validate_interval(start,end)
    if not re.fullmatch(r"S03E(?:0[2-9]|1[0-4])",episode):
        raise ValueError("Expected S03E02..S03E14")
    source=root/"input"/(episode+".mp4")
    transfer=json.loads(source.with_suffix(".mp4.transfer.json").read_text())
    source_sha=sha(source)
    if not transfer.get("transfer_verified") or transfer["sha256"]!=source_sha:
        raise ValueError("Changed original clip")
    if shutil.disk_usage(root).free<28*1024**3:
        raise RuntimeError("Need 25 GiB reserve plus 3 GiB bounded ASR/cache budget")
    output.mkdir(parents=True,exist_ok=False); cache.mkdir(parents=True,exist_ok=True)
    audio=output/"original-interval-16k.wav"
    subprocess.run(["ffmpeg","-v","error","-nostdin","-threads","2","-seek_timestamp","1",
                    "-ss",str(start),"-i",str(source),"-t",str(end-start),"-map","0:a:0",
                    "-vn","-ac","1","-ar","16000","-c:a","pcm_s16le","-n",str(audio)],check=True)
    from faster_whisper import WhisperModel
    model=WhisperModel("small",device="cpu",compute_type="int8",cpu_threads=4,
                       num_workers=1,download_root=str(cache))
    segments,info=model.transcribe(str(audio),language="zh",beam_size=5,vad_filter=False,
                                   condition_on_previous_text=False)
    rows=[dict(audio_relative_start=s.start,audio_relative_end=s.end,
               source_start_seconds=start+s.start,source_end_seconds=start+s.end,
               text=s.text,avg_logprob=s.avg_logprob,no_speech_prob=s.no_speech_prob)
          for s in segments]
    report=dict(schema_version=1,scope="ORIGINAL_AUDIO_AUXILIARY_NOT_SUBTITLE_AUTHORITY",
                episode=episode,source=str(source),source_sha256=source_sha,
                requested_interval_seconds=[start,end],audio_path=str(audio),audio_sha256=sha(audio),
                model="faster-whisper-small",engine_version=importlib.metadata.version("faster-whisper"),
                device="CPU",compute_type="int8",language=info.language,
                transcript=rows,audio_audition_performed=False,
                decision="PENDING_COMBINED_AUDIO_PIXEL_REVIEW",
                warnings=["ASR can mishear names, homophones and punctuation",
                          "Keep clear original caption wording/spaces; do not copy ASR wholesale",
                          "No listening claim is made by this text-only helper"])
    (output/"evidence.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps(dict(evidence=str(output/"evidence.json"),segments=len(rows),
                          scope=report["scope"]),ensure_ascii=True))
    return report

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--episode",required=True)
    p.add_argument("--start",type=float,required=True)
    p.add_argument("--end",type=float,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--cache",type=Path,required=True)
    a=p.parse_args();transcribe(a.root,a.episode,a.start,a.end,a.output,a.cache)

