"""Generate ASS from genuinely sealed reviews and proven shared style assets."""
import argparse
from argparse import Namespace
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kwjd import cmd_make_ass

class WaitStyle(RuntimeError): pass
class WaitReview(RuntimeError): pass

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def style_assets(post):
    folder=post/"styles";proof_path=folder/"style-preflight.json"
    if not proof_path.is_file():
        raise WaitStyle("Waiting for actual master/font preview preflight")
    proof=json.loads(proof_path.read_text(encoding="utf8"))
    if (proof.get("schema_version")!=1 or proof.get("technical_checked") is not True
            or proof.get("visual_checked") is not True):
        raise WaitStyle("Shared style has not passed actual technical and visual checks")
    expected=dict(family="MiSans Subtitle",size=84,variation_weight=450,
                  outline=3.5,shadow=1,margin_v=54)
    if proof.get("authorized_design")!=expected:
        raise ValueError("Shared style differs from explicitly authorized design")
    template=Path(proof["template_path"])
    if template.resolve()!=(folder/"template.ass").resolve() or template.is_symlink():
        raise ValueError("Unexpected style template path")
    if sha(template)!=proof["template_sha256"]:
        raise ValueError("Changed preflight template")
    fonts=proof.get("fonts")
    if not isinstance(fonts,dict) or not fonts:
        raise ValueError("No bound fonts")
    for name,digest in fonts.items():
        if Path(name).name!=name or Path(name).suffix.lower() not in [".ttf",".otf",".ttc"]:
            raise ValueError("Invalid font name")
        path=folder/"fonts"/name
        if path.is_symlink() or sha(path)!=digest:
            raise ValueError("Changed bound font")
    for key in ["font_selection_evidence","preview_evidence"]:
        path=Path(proof[key+"_path"])
        if not path.resolve().is_relative_to(post.resolve()) or path.is_symlink():
            raise ValueError("Evidence outside post workspace")
        if sha(path)!=proof[key+"_sha256"]:
            raise ValueError("Changed actual font/preview evidence")
    if not re.fullmatch("[a-f0-9]{64}",proof.get("master_sha256","")):
        raise ValueError("Actual preview master is not hash bound")
    return template,proof_path,proof

def validate_ass(root,episode):
    folder=root/"output/ass"
    ass=folder/(episode+".ass")
    receipt=ass.with_suffix(".ass.ready.json")
    if not ass.is_file() or not receipt.is_file():
        return False
    data=json.loads(receipt.read_text(encoding="utf8"))
    template,proof_path,proof=style_assets(root/"post")
    srt=root/"output"/(episode+".zh.srt")
    seal=srt.with_suffix(".srt.ready.json")
    return (data.get("state")=="ASS_STYLE_READY_REFERENCE_CLOCK"
            and data.get("episode")==episode and data.get("ass_sha256")==sha(ass)
            and data.get("srt_sha256")==sha(srt) and data.get("srt_seal_sha256")==sha(seal)
            and data.get("template_sha256")==sha(template)
            and data.get("style_preflight_sha256")==sha(proof_path)
            and data.get("fonts")==proof["fonts"]
            and data.get("time_base")=="trimmed_program_source_pts_unshifted"
            and data.get("master_offset_applied") is False)

def ensure_ass(root,episode):
    from subtitle_review_supervisor import validate_episode
    if not validate_episode(root,episode):
        raise WaitReview("Episode has not actually passed all review/seal gates")
    template,proof_path,proof=style_assets(root/"post")
    folder=root/"output/ass";folder.mkdir(parents=True,exist_ok=True)
    ass=folder/(episode+".ass")
    if ass.exists() or ass.with_suffix(".ass.ready.json").exists():
        if validate_ass(root,episode):
            return ass
        raise ValueError("Existing ASS has changed binding; retain for inspection")
    srt=root/"output"/(episode+".zh.srt")
    partial=ass.with_suffix(".ass.generating")
    if partial.exists(): raise FileExistsError(partial)
    cmd_make_ass(Namespace(srt=str(srt),template=str(template),output=str(partial)))
    partial.rename(ass)
    receipt=dict(schema_version=1,state="ASS_STYLE_READY_REFERENCE_CLOCK",episode=episode,
                 ass_path=str(ass),ass_sha256=sha(ass),srt_path=str(srt),srt_sha256=sha(srt),
                 srt_seal_sha256=sha(srt.with_suffix(".srt.ready.json")),
                 template_path=str(template),template_sha256=sha(template),
                 style_preflight_path=str(proof_path),style_preflight_sha256=sha(proof_path),
                 fonts=proof["fonts"],authorized_design=proof["authorized_design"],
                 time_base="trimmed_program_source_pts_unshifted",master_offset_applied=False,
                 ready_for_master_burn=False,generated_at=time.time(),
                 rounding="ASS centisecond rounding; source SRT remains millisecond exact",
                 visual_preview_scope="Shared style proof on its actual S03E02 master, not every final composite",
                 production_note="Postproduction must bind this episode's actual master/time mapping before burn")
    target=ass.with_suffix(".ass.ready.json")
    temp=target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    temp.replace(target)
    if not validate_ass(root,episode):
        raise ValueError("New ASS receipt failed binding verification")
    return ass

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--episode",required=True)
    a=p.parse_args()
    if not re.fullmatch(r"S03E(?:0[2-9]|1[0-4])",a.episode):
        p.error("Expected S03E02..S03E14")
    print(ensure_ass(a.root.resolve(),a.episode))

if __name__=="__main__":main()

