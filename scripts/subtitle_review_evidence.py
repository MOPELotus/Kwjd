"""Source-pixel evidence pages for subtitle review, never automatic approval."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kwjd import read_srt
from scripts.ffmpeg_timestamps import showinfo_frames

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def capture(source,seconds,output):
    command=["ffmpeg","-v","info","-nostdin","-threads","2","-copyts",
             "-seek_timestamp","1","-ss",f"{seconds:.6f}","-i",str(source),
             "-frames:v","1","-filter_threads","1","-vf","trim=end_frame=1,crop=1920:220:0:800,showinfo",
             "-fps_mode","passthrough","-n",str(output)]
    result=subprocess.run(command,capture_output=True,text=True,check=True)
    stamps=showinfo_frames(result.stderr)
    if len(stamps)!=1 or not output.is_file():
        raise ValueError("No decoded source timestamp")
    output.with_suffix(output.suffix+'.decode.log').write_text(result.stderr)
    return stamps[0]['seconds']

def prepare(root,episode,srt,start,count,output,times=None):
    from PIL import Image,ImageDraw
    if not re.fullmatch(r"S03E(?:0[2-9]|1[0-4])",episode):
        raise ValueError("Expected S03E02..S03E14")
    source=root/"input"/(episode+".mp4")
    receipt=json.loads(source.with_suffix(".mp4.transfer.json").read_text())
    source_sha=sha(source)
    if not receipt.get("transfer_verified") or source_sha!=receipt["sha256"]:
        raise ValueError("Source transfer SHA differs")
    cues=read_srt(srt)
    if times is None:
        selected=list(enumerate(cues,start=1))[start:start+count]
        rows=[dict(cue_id=i,start_ms=a,end_ms=b,draft_text=text,
                   requested_seconds=[(a+min(40,(b-a)/4))/1000,(a+b)/2000,
                                      (b-min(40,(b-a)/4))/1000])
              for i,(a,b,text) in selected]
    else:
        rows=[dict(cue_id="frame-"+str(i),start_ms=round(t*1000),end_ms=round(t*1000),
                   draft_text="",requested_seconds=[t]) for i,t in enumerate(times)]
    if not rows:
        raise ValueError("Empty requested batch")
    output.mkdir(parents=True,exist_ok=False)
    pages=[]
    variants=3 if times is None else 1
    labels=["first","middle","last"] if times is None else ["requested"]
    for variant in range(variants):
        for base in range(0,len(rows),8):
            group=rows[base:base+8]
            sheet=Image.new("RGB",(960,140*len(group)),"white")
            for j,row in enumerate(group):
                seconds=row["requested_seconds"][variant]
                frame=output/f"cue-{row['cue_id']}-{labels[variant]}.jpg"
                actual=capture(source,seconds,frame)
                row.setdefault("frames",[]).append(dict(kind=labels[variant],
                    path=str(frame),source_pts_seconds=actual,sha256=sha(frame),
                    timestamp_method='integer_showinfo_pts_times_input_time_base',
                    decode_log_path=str(frame.with_suffix(frame.suffix+'.decode.log')),
                    decode_log_sha256=sha(frame.with_suffix(frame.suffix+'.decode.log'))))
                image=Image.open(frame);image.thumbnail((960,110))
                sheet.paste(image,(0,j*140+25))
                ImageDraw.Draw(sheet).text((8,j*140+6),
                    f"{episode} cue {row['cue_id']} {labels[variant]} PTS={actual:.6f}s",
                    fill=(0,0,0))
            page=output/f"page-{labels[variant]}-{base//8:03d}.jpg"
            sheet.save(page,quality=97,subsampling=0)
            pages.append(dict(path=str(page),sha256=sha(page),
                              cue_ids=[r["cue_id"] for r in group],kind=labels[variant]))
    manifest=dict(schema_version=1,episode=episode,source=str(source),source_sha256=source_sha,
                  srt=str(srt),srt_sha256=sha(srt),scope="SOURCE_PIXEL_EVIDENCE_NOT_REVIEW",
                  rows=rows,pages=pages)
    (output/"evidence.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf8")
    return manifest

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--episode",required=True)
    parser.add_argument("--srt",required=True,type=Path)
    parser.add_argument("--start-index",type=int,default=0)
    parser.add_argument("--count",type=int,default=16)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--times",type=float,nargs="+")
    args=parser.parse_args()
    if args.start_index<0 or not 1<=args.count<=32:
        parser.error("Invalid batch bounds")
    result=prepare(args.root,args.episode,args.srt,args.start_index,args.count,args.output,args.times)
    print(json.dumps(dict(evidence=str(args.output/"evidence.json"),rows=len(result["rows"]),
                          pages=[p["path"] for p in result["pages"]]),ensure_ascii=True))
if __name__=="__main__":
    main()

