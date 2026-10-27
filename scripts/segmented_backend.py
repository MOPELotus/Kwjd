#!/usr/bin/env python3
"""Immutable compressed AI segments, exact-clock concatenation and crash recovery.

Uses the real restoration/RIFE backend, retaining hash-bound segments until the
whole enhancement has been decoded and accepted. Never decodes a full frame dump.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

import enhance_backend as engine
import concat_transfer
from enhance_stage import atomic_json,check_space,probe,sha256,GIB,validate_video,WaitDisk,WaitGPU
from linux_stage_handoff import decoded_video_frames,release_verified_file,resume_verified_release
from master_policy import reject_retired


def fingerprint(cfg):
    frames=cfg.get('segment_source_frames',250)
    if type(frames) is not int or frames<5 or frames>1500 or frames%5:
        raise ValueError('Resumable segment length must be5..1500 native frames, multiple of5')
    return hashlib.sha256((cfg['fingerprint']+sha256(__file__)+sha256(concat_transfer.__file__)+str(frames)).encode()).hexdigest()


def make_identity(episode,digest,fp,count,size):
    return {'episode':episode,'master_sha256':digest,'backend_fingerprint':fp,
            'input_frames':count,'output_frames':engine.output_count(count),'segment_source_frames':size}


def verified_segment_bytes(directory,digest,fp):
    occupied=0;seen=set()
    for ready in directory.glob('[0-9]*.ready.json'):
        record=json.loads(ready.read_text());target=ready.with_name(ready.name.replace('.ready.json','.mkv'))
        if (record.get('master_sha256')==digest and record.get('backend_fingerprint')==fp
                and target.is_file() and not target.is_symlink() and sha256(target)==record.get('sha256')):
            stat=target.stat();inode=(stat.st_dev,stat.st_ino)
            if inode not in seen and stat.st_nlink==1:
                occupied+=min(stat.st_size,stat.st_blocks*512);seen.add(inode)
    return occupied


def plan(master,cfg,output=None,episode=None):
    result=engine.disk_plan(engine.source_info(master),cfg)
    # CUDA uses rawvideo pipes: only compressed video, receipts and logs touch disk.
    # Retain the portable PNG pipeline's8GiB bound and the25GiB filesystem floor.
    streaming=cfg.get('rife_backend') in ('cuda_onnx','cuda_trt','cuda_trt_sm86')
    working_bytes=512*1024**2 if streaming else 8*GIB
    result['scratch_peak_bytes']=working_bytes
    result['scratch_strategy']='rawvideo_pipe' if streaming else 'png_exchange'
    # Segments plus concatenated output coexist until complete decode acceptance.
    seconds=result['input_duration']
    result['additional_peak_bytes']=int(seconds*90_000_000/8)+working_bytes
    result['backend_fingerprint']=fingerprint(cfg)
    result['resumable_segments']=True
    if output is not None:
        ep=episode or Path(output).name.split('.')[0]
        digest,fp,directory=context(master,output,ep,cfg)
        header=directory/'context.json'
        identity=json.loads(header.read_text()) if header.is_file() else {}
        occupied=0
        if identity.get('master_sha256')==digest and identity.get('backend_fingerprint')==fp:
            occupied=verified_segment_bytes(directory,digest,fp)
            candidate=Path(output)
            if candidate.is_file() and not candidate.is_symlink() and resume_check(master,output,ep,cfg):
                saved=directory/'completed.json'
                completed=json.loads(saved.read_text()) if saved.is_file() else {}
                if completed.get('sha256')==sha256(candidate):
                    result['additional_peak_bytes']=16*1024**2
                    result['scratch_peak_bytes']=0
                    result['already_validated_complete_output']=True
                    return result
                stat=candidate.stat()
                if stat.st_nlink==1:occupied+=min(stat.st_size,stat.st_blocks*512)
            result['existing_verified_bytes']=occupied
            result['additional_peak_bytes']=max(16*1024**2,result['additional_peak_bytes']-occupied)
        if cfg.get('post_root'):
            if not identity:
                identity=make_identity(ep,digest,fp,engine.source_packet_count(master),cfg.get('segment_source_frames',250))
            video_budget=int(seconds*45_000_000/8)+1
            _,extra=concat_transfer.choose_concat(cfg['post_root'],output,directory,identity,video_budget,
                                                  releasable_bytes=occupied)
            if extra:
                result['additional_peak_bytes']=max(16*1024**2,video_budget+working_bytes-occupied)
                result['extra_volumes']=[{'path':str(Path(extra['concat_path']).parent),
                    'additional_peak_bytes':extra['concat_additional_peak_bytes']}]
                result['concat_placement']=extra
    return result


def context(master,output,episode,cfg):
    if not re.fullmatch(r'S01E\d{2,3}',episode):raise ValueError('Invalid episode ID')
    digest=sha256(master);reject_retired(digest)
    fp=fingerprint(cfg)
    directory=Path(output).parent/(episode+'.segments')/(digest[:16]+'-'+fp[:16])
    return digest,fp,directory


def resume_check(master,output,episode,cfg):
    digest,fp,directory=context(master,output,episode,cfg)
    intent=directory/'concat.pending.json'
    if not intent.is_file():return False
    record=json.loads(intent.read_text())
    return (record.get('master_sha256')==digest and record.get('backend_fingerprint')==fp
            and record.get('output')==str(Path(output).resolve()))


def validate_segment(path,frames,seconds,log):
    info=probe(path)
    validate_video({'format':{'duration':str(seconds)}},info)
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    if video.get('codec_name')!='hevc' or video.get('pix_fmt') not in ('yuv420p10le','p010le'):
        raise ValueError('Segment must be HEVC10bit')
    if abs(float(info['format']['duration'])-seconds)>.02:
        raise ValueError('Segment duration differs from its exact frame clock')
    if decoded_video_frames(path,log)!=frames:
        raise ValueError('Segment frame count mismatch')


def release_segments(directory):
    manifest=json.loads((directory/'concat.pending.json').read_text())
    for record in manifest['segments']:
        index=record['index']
        if type(index) is not int or not 0<=index<1000000:raise ValueError('Invalid segment release index')
        path=directory/f'{index:06d}.mkv'
        if path.exists():
            if sha256(path)!=record['sha256']:raise ValueError('Segment changed before stage release')
            release_verified_file(path,record['sha256'])
        else:resume_verified_release(path,record['sha256'])


def run(master,output,episode,cfg):
    master,output=Path(master),Path(output)
    digest,fp,directory=context(master,output,episode,cfg)
    directory.mkdir(parents=True,exist_ok=True)
    count=engine.source_packet_count(master);source=engine.source_info(master)
    total=engine.output_count(count);size=cfg.get('segment_source_frames',250)
    identity=make_identity(episode,digest,fp,count,size)
    header=directory/'context.json'
    if header.exists() and json.loads(header.read_text())!=identity:
        raise ValueError('Existing segment context differs; retained')
    atomic_json(header,identity)
    log=directory/'validation.log'
    completed=directory/'completed.json'
    validate=lambda path:validate_segment(path,total,total/60,log)
    def check_source():
        if sha256(master)!=digest:raise ValueError('Master changed during segmented enhancement')
        return True
    if concat_transfer.resume_transfer(output,directory,identity,validate,
                                      lambda:release_segments(directory),check_source):
        print(json.dumps({'resumed_verified_transfer':str(output)}),flush=True);return
    concat_path=output
    if cfg.get('post_root'):
        concat_path,_=concat_transfer.choose_concat(cfg['post_root'],output,directory,identity,
            int(float(source['format']['duration'])*45_000_000/8)+1,
            releasable_bytes=verified_segment_bytes(directory,digest,fp))
        if concat_path!=output and concat_path.exists():
            try:validate(concat_path)
            except (ValueError,subprocess.CalledProcessError,StopIteration) as error:
                atomic_json(directory/'external-concat.discarded.json',{'reason':str(error),'sha256':sha256(concat_path)})
                release_verified_file(concat_path,sha256(concat_path))
            else:
                concat_transfer.complete_transfer(concat_path,output,directory,identity,validate,
                    lambda:release_segments(directory),check_source)
                return
    if output.exists():
        if not resume_check(master,output,episode,cfg):
            raise ValueError('Existing enhancement partial lacks matching concat intent; retained')
        saved=json.loads(completed.read_text()) if completed.is_file() else {}
        if completed.is_file():
            if any(saved.get(k)!=v for k,v in identity.items()) or saved.get('sha256')!=sha256(output):
                raise ValueError('Completed enhancement hash/binding changed; output and segments retained')
            release_segments(directory)
            print(json.dumps({'resumed_complete_output':str(output)}),flush=True);return
        try:
            validate_segment(output,total,total/60,log)
        except (ValueError,subprocess.CalledProcessError,StopIteration) as error:
            atomic_json(directory/'concat.discarded.json',{'reason':str(error),'sha256':sha256(output)})
            release_verified_file(output,sha256(output))
        else:
            atomic_json(completed,{**identity,'sha256':sha256(output)});release_segments(directory);return
    budget=plan(master,cfg,output,episode)
    check_space(output.parent,budget['additional_peak_bytes'],25)
    check_space(cfg['scratch'],budget['scratch_peak_bytes'],25)
    for volume in budget.get('extra_volumes',[]):
        check_space(volume['path'],volume['additional_peak_bytes'],25)
    stat=master.stat()
    prepared={'stat':(stat.st_ino,stat.st_size,stat.st_mtime_ns),'sha256':digest,'count':count,'info':source}
    restorer=None;paths=[];receipts=[]
    for index,start in enumerate(range(0,count,size)):
        selected=min(size,count-start);frames=engine.output_count(selected)
        target=directory/f'{index:06d}.mkv';partial=directory/f'{index:06d}.partial.mkv'
        ready=target.with_suffix('.ready.json');pending=target.with_suffix('.pending.json')
        binding={**identity,'index':index,'source_start':start,'source_frames':selected,'frames':frames}
        reused=target.exists()
        if target.exists():
            record=json.loads(ready.read_text()) if ready.is_file() else {}
            if any(record.get(k)!=v for k,v in binding.items()) or record.get('sha256')!=sha256(target):
                raise ValueError('Segment differs from its completion receipt; retained')
            if partial.exists():
                if (not pending.is_file() or json.loads(pending.read_text())!=binding
                        or sha256(partial)!=record['sha256']):
                    raise ValueError('Residual segment partial differs; retained')
                release_verified_file(partial,record['sha256'])
        else:
            if partial.exists():
                if not pending.is_file() or json.loads(pending.read_text())!=binding:
                    raise ValueError('Orphan partial not owned by this segment; retained')
                try:validate_segment(partial,frames,frames/60,log)
                except (ValueError,subprocess.CalledProcessError,StopIteration) as error:
                    atomic_json(directory/f'{index:06d}.discarded.json',{'reason':str(error),'sha256':sha256(partial)})
                    release_verified_file(partial,sha256(partial))
            if not partial.exists():
                atomic_json(pending,binding)
                if restorer is None and cfg['restore'] and cfg.get('restoration_backend')=='cuda_srvgg':
                    from cuda_restore import Restorer
                    restorer=Restorer(cfg)
                engine.run(master,partial,episode,cfg,source_start=start,source_frames=selected,
                           prepared=prepared,restorer=restorer)
                validate_segment(partial,frames,frames/60,log)
            record={**binding,'sha256':sha256(partial)}
            # Publish receipt first: a crash after link can validate target on retry.
            atomic_json(ready,record);os.link(partial,target);partial.unlink()
        paths.append(target);receipts.append(record)
        print(json.dumps({'episode':episode,'segment_complete':index,'source_frames_complete':start+selected,
                          'source_frames_total':count,'resumed':reused}),flush=True)
    listing=directory/'concat.txt'
    listing.write_text(''.join("file '"+str(p.resolve()).replace("'","'\\''")+"'\n" for p in paths))
    atomic_json(directory/'concat.pending.json',{**identity,'output':str(output.resolve()),
                                               'concat_path':str(concat_path.absolute()),'segments':receipts})
    subprocess.run(['ffmpeg','-nostdin','-v','error','-n','-f','concat','-safe','0','-i',str(listing),
                    '-map','0:v:0','-an','-c:v','copy',str(concat_path)],check=True)
    if concat_path!=output:
        concat_transfer.complete_transfer(concat_path,output,directory,identity,validate,
                                          lambda:release_segments(directory),check_source)
        print(json.dumps({'episode':episode,'cross_filesystem_transfer_complete':str(output)}),flush=True)
        return
    validate_segment(output,total,total/60,log)
    if sha256(master)!=digest:raise ValueError('Master changed during segmented enhancement')
    atomic_json(completed,{**identity,'sha256':sha256(output)})
    # A verified complete next-stage picture now replaces the generated segments.
    release_segments(directory)
    print(json.dumps({'episode':episode,'output_frames':total,'resumable_segments_complete':len(paths),
                      'sha256':sha256(output)}),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True);p.add_argument('--plan',action='store_true')
    p.add_argument('--fingerprint',action='store_true');p.add_argument('--resume-check',action='store_true')
    p.add_argument('input',nargs='?');p.add_argument('output',nargs='?');p.add_argument('episode',nargs='?')
    args=p.parse_args();cfg=engine.config(args.config)
    if args.fingerprint:print(json.dumps({'backend_fingerprint':fingerprint(cfg)}));return
    if not args.input:p.error('INPUT required')
    if args.plan:print(json.dumps(plan(args.input,cfg,args.output,args.episode)));return
    if not args.output:p.error('OUTPUT required')
    ep=args.episode or Path(args.output).name.split('.')[0]
    if args.resume_check:
        print(json.dumps({'resume_allowed':resume_check(args.input,args.output,ep,cfg)}));return
    run(args.input,args.output,ep,cfg)


if __name__=='__main__':
    try:main()
    except WaitDisk as error:
        print(json.dumps({'wait_disk':str(error)}),flush=True)
        raise SystemExit(75)
    except WaitGPU as error:
        print(json.dumps({'wait_gpu':str(error)}),flush=True)
        raise SystemExit(76)
