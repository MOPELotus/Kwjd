"""Repair a failed concat from bound complete segments; never runs neural work."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
from concat_clock import write_concat,verify_packet_clock,normalize_copy_timestamps
from enhance_stage import atomic_json,sha256,check_space


def payload_hash(path):
    return subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(path),
        '-map','0:v:0','-an','-c:v','copy','-f','hash','-hash','sha256','-'],text=True).strip()


def repair(root,episode):
    if not re.fullmatch(r'S03E\d{2}',episode):raise ValueError('Explicit S03 episode required')
    root=Path(root).resolve();post=root/'post';job_path=post/'jobs'/(episode+'.json')
    with (post/'jobs/.worker.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        job=json.loads(job_path.read_text())
        if job.get('state')!='FAILED':raise ValueError('Repair only an idle failed job')
        master=post/'incoming'/(episode+'.clean-master.mov')
        if job.get('master')!='incoming/'+master.name:raise ValueError('Noncanonical master binding')
        import enhance_backend
        import segmented_backend as backend
        from native_face_pipeline import validate_pipeline
        cfg=enhance_backend.config(post/'backend/runtime.native-active.json')
        output=post/'cache'/episode/(episode+'.enhanced.partial.mkv')
        digest,fp,context=backend.context(master,output,episode,cfg)
        identity=json.loads((context/'context.json').read_text())
        intent=json.loads((context/'concat.pending.json').read_text())
        if (digest!=job['master_sha256'] or identity['backend_fingerprint']!=fp
                or any(intent.get(k)!=v for k,v in identity.items())
                or intent['output']!=str(output.resolve()) or not output.is_file()):
            raise ValueError('Existing failed concatenation binding differs')
        paths=[];counts=[];records=[];position=0
        for index,pin in enumerate(intent['segments']):
            ready=context/f'{index:06d}.ready.json';record=json.loads(ready.read_text())
            media=context/f'{index:06d}.mkv'
            if (record!=pin or record['index']!=index or record['source_start']!=position
                    or any(record.get(k)!=v for k,v in identity.items())
                    or sha256(media)!=record['sha256']):
                raise ValueError('Existing segment identity/content differs')
            evidence=validate_pipeline(context/f'{index:06d}.partial.mkv',cfg,
                position,record['source_frames'],identity['input_frames'])
            if any(record.get(k)!=v for k,v in evidence.items()):raise ValueError('Pipeline evidence differs')
            paths.append(media);counts.append(record['frames']);position+=record['source_frames']
            records.append(dict(index=index,media_sha256=record['sha256'],original_ready_sha256=sha256(ready)))
        if position!=identity['input_frames'] or sum(counts)!=identity['output_frames']:
            raise ValueError('Complete contiguous original segments required')
        folder=post/'reports'/(episode+'-exact-concat-v1');folder.mkdir(exist_ok=True)
        failed_sha=sha256(output);listing=folder/'full.concat.txt'
        candidate=output.with_name(episode+'.exact-clock.candidate.mkv')
        archive=output.with_name(episode+'.duration-drift.retained.mkv')
        if archive.exists():raise FileExistsError('Prior published repair retained; inspect before retry')
        check_space(output.parent,sum(p.stat().st_size for p in paths)+128*1024**2,5)
        write_concat(listing,paths,counts)
        command=['ffmpeg','-nostdin','-v','error','-n','-f','concat','-safe','0','-i',str(listing),
            '-map','0:v:0','-an','-c:v','copy',str(candidate)]
        atomic_json(folder/'repair-status.json',dict(state='STREAM_COPY_AND_FULL_VALIDATION_RUNNING',
            at=time.time(),previous_job=job,segments=len(records),neural_frames_recomputed=0,command=command))
        if not candidate.exists():subprocess.run(command,check=True)
        normalization=None
        try:clock=verify_packet_clock(candidate,identity['output_frames'])
        except ValueError:
            previous=candidate
            candidate=output.with_name(episode+'.exact-clock.normalized.candidate.mkv')
            if candidate.exists():raise FileExistsError('Prior normalized candidate retained; inspect before retry')
            normalization=normalize_copy_timestamps(previous,candidate,identity['output_frames'])
            clock=normalization['clock']
        backend.validate_segment(candidate,identity['output_frames'],identity['output_frames']/60,folder/'full-validation.log')
        original_payload=payload_hash(output);corrected_payload=payload_hash(candidate)
        if original_payload!=corrected_payload:raise ValueError('Stream-copy video packet payload changed')
        if sha256(master)!=digest or sha256(output)!=failed_sha:raise ValueError('Retained source changed during repair')
        candidate_sha=sha256(candidate)
        proof=dict(schema_version=1,at=time.time(),state='FULL_CONCAT_CLOCK_AND_DECODE_ACCEPTED',
            episode=episode,master_sha256=digest,producer_backend_fingerprint=fp,
            original_concat_sha256=failed_sha,corrected_concat_sha256=candidate_sha,
            original_concat_retained=str(archive),original_segment_receipts=records,
            full_decode_checked=True,decoded_frames=identity['output_frames'],packet_clock=clock,
            compressed_video_payload_hash=corrected_payload,compressed_video_payload_equal=True,
            concat_list_sha256=sha256(listing),repair_helper_sha256=sha256(Path(__file__)),
            concat_clock_helper_sha256=sha256(Path(__file__).with_name('concat_clock.py')),
            timestamp_normalization=normalization,
            neural_frames_recomputed=0,new_lossy_encodes=0,command=command,
            final_quality_approval=False)
        atomic_json(folder/'repair-proof.json',proof)
        output.rename(archive);candidate.rename(output)
        atomic_json(context/'completed.json',{**identity,'sha256':candidate_sha,
            'exact_clock_repair_provenance':str(folder/'repair-proof.json'),
            'exact_clock_repair_provenance_sha256':sha256(folder/'repair-proof.json')})
        # Original producer/segments and the failed concat remain preserved. The
        # existing worker resumes normal source/audio/final checks from this receipt.
        atomic_json(job_path,{**job,'state':'QUEUED','updated_at':time.time(),
            'detail':'All original segments reused; frame-count concat full decode/PTS/payload verified; resume existing Worker',
            'concat_clock_repair_proof':str(folder/'repair-proof.json')})
        atomic_json(folder/'repair-status.json',dict(state='ACCEPTED_EXISTING_WORKER_REQUEUED',at=time.time(),
            proof=str(folder/'repair-proof.json'),new_workers=0,neural_frames_recomputed=0))
        print(json.dumps(dict(state='ACCEPTED_EXISTING_WORKER_REQUEUED',frames=identity['output_frames'])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path);parser.add_argument('--episode',required=True)
    args=parser.parse_args();repair(args.root,args.episode)
