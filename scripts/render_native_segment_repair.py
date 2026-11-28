"""Render one reviewed native-cut segment with the existing fixed recipe.

No publication, Worker, source-clock change or final approval is performed.
"""
import argparse,json,os,subprocess,threading,time
from pathlib import Path
from enhance_stage import atomic_json,sha256

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for key in ['master','config','cut-evidence','output']:p.add_argument('--'+key,type=Path,required=True)
 a=p.parse_args();e=json.loads(a.cut_evidence.read_text());first=e['source_start'];count=e['input_frames']
 if type(first)is not int or first<0 or first%250 or type(count)is not int or count!=250:raise ValueError('One aligned250-frame segment required')
 from enhance_backend import config,source_info,source_packet_count
 from native_face_pipeline import run,reviewed_cut_frames
 from face_rework_worker import validate_video
 from segmented_backend import fingerprint
 cfg=config(a.config);digest=sha256(a.master)
 reviewed_cut_frames(e,e['episode'],digest,first,count)
 if cfg.get('input_frame_rate',25)!=25:raise ValueError('Native25 repair only')
 stat=a.master.stat();prepared=dict(sha256=digest,count=source_packet_count(a.master,25),info=source_info(a.master,25),stat=(stat.st_ino,stat.st_size,stat.st_mtime_ns))
 samples=[];stop=threading.Event()
 def monitor():
  while not stop.is_set():
   q=subprocess.run(['nvidia-smi','--query-compute-apps=pid,gpu_uuid,used_gpu_memory','--format=csv,noheader'],capture_output=True,text=True)
   samples.append(dict(at=time.time(),pid=os.getpid(),compute_apps=q.stdout));stop.wait(3)
 t=threading.Thread(target=monitor,daemon=True);t.start();began=time.monotonic()
 try:
  run(a.master,a.output,e['episode'],cfg,source_start=first,source_frames=count,prepared=prepared,cut_evidence=e)
  validation=validate_video(a.output,600,a.output.parent)
  pipeline=json.loads(a.output.with_name(a.output.name+'.pipeline.json').read_text())
  assert set(e['cut_frames']).issubset(pipeline['cuts']) and pipeline['coverage']['failed_frames']==0
  atomic_json(a.output.with_suffix('.result.json'),dict(technical_checked=True,master_sha256=digest,sample_sha256=sha256(a.output),backend_fingerprint=fingerprint(cfg),source_start=first,input_frames=count,output_frames=600,pipeline=pipeline,validation=validation,wall_seconds=time.monotonic()-began,cut_evidence_path=str(a.cut_evidence),cut_evidence_sha256=sha256(a.cut_evidence),scope='Actual physical fixed-stack bounded repair; requires candidate packet/PCM/full-decode and actual visual review before publication'))
 finally:
  stop.set();t.join();atomic_json(a.output.with_suffix('.gpu.json'),dict(pid=os.getpid(),samples=samples))
if __name__=='__main__':main()
