"""Hash-bound cross-filesystem handoff of a generated enhancement concatenation.

Never handles original masters or final deliverables. The only deletable source
is the fixed .enhancement-staging/<episode>/<master16>-<profile16> file. Inner
symlinks are forbidden; the preconfigured post/final link itself is allowed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from enhance_stage import atomic_json, check_space, sha256, WaitDisk
from linux_stage_handoff import release_verified_file, resume_verified_release

META = 16 * 1024**2

def _absolute(path):
    return Path(os.path.abspath(path))

def _ancestor(path):
    p=Path(path)
    while not p.exists():
        if p.is_symlink():raise ValueError('Broken inner symlink')
        p=p.parent
    return p

def _nosymlinks(path, boundary):
    path,boundary=_absolute(path),_absolute(boundary)
    try:path.relative_to(boundary)
    except ValueError:raise ValueError('Stage path escaped its fixed directory')
    while path!=boundary:
        if path.is_symlink():raise ValueError('Inner stage symlinks are forbidden')
        path=path.parent

def _key(identity):
    ep=identity['episode'];master=identity['master_sha256'];fp=identity['backend_fingerprint']
    import re
    if not re.fullmatch(r'S01E\d{2,3}',ep):raise ValueError('Invalid episode')
    if any(not re.fullmatch(r'[0-9a-f]{64}',x) for x in (master,fp)):
        raise ValueError('Full lowercase master/profile SHA256 required')
    return ep,master[:16]+'-'+fp[:16]

def _source(path,identity):
    p=_absolute(path);ep,key=_key(identity)
    if (p.name!='enhancement.concat.mkv' or p.parent.name!=key
            or p.parent.parent.name!=ep or p.parent.parent.parent.name!='.enhancement-staging'):
        raise ValueError('Only a fixed generated staging concatenation may be released')
    _nosymlinks(p,p.parent.parent.parent.parent)
    return p

def _paths(output,contextdir):
    output,contextdir=_absolute(output),_absolute(contextdir)
    if output.is_symlink():raise ValueError('Canonical output must not be a symlink')
    _nosymlinks(contextdir,output.parent)
    return output,contextdir

def _load(path):return json.loads(Path(path).read_text())

def _record(contextdir,identity,output):
    ready=contextdir/'transfer.ready.json'
    r=_load(ready)
    if (r.get('schema_version')!=1 or r.get('identity')!=identity
            or r.get('output')!=str(output) or r.get('validated') is not True):
        raise ValueError('Transfer receipt does not bind this source/profile/output')
    source=_source(r['concat_path'],identity)
    if ready.is_symlink():raise ValueError('Transfer receipt symlink forbidden')
    return r,source

def choose_concat(post,output,contextdir,identity,video_budget,reserve_gib=25,*,releasable_bytes=0):
    """Choose the separate final volume, or (original output, None) on no capacity.

    extra_plan describes each filesystem independently. The caller must budget
    its AI segment/scratch peak separately; after release, root needs one copy.
    """
    if type(video_budget) is not int or video_budget<=0:raise ValueError('Positive video byte budget required')
    if type(releasable_bytes) is not int or releasable_bytes<0:raise ValueError('Invalid releasable byte credit')
    output,contextdir=_paths(output,contextdir);post=_absolute(post)
    ep,key=_key(identity);top=post/'final'
    candidate=top/'.enhancement-staging'/ep/key/'enhancement.concat.mkv'
    _nosymlinks(candidate,top)
    source_device=_ancestor(candidate.parent).stat().st_dev
    output_device=_ancestor(output.parent).stat().st_dev
    if source_device==output_device:return output,None
    reusable=False;canonical=False;owned_unvalidated=False
    if (contextdir/'transfer.ready.json').exists():
        r,saved_source=_record(contextdir,identity,output)
        if saved_source!=_absolute(candidate):raise ValueError('Transfer staging path differs')
        if output.exists() and sha256(output)==r['sha256']:canonical=True
        if candidate.exists():
            if sha256(candidate)!=r['sha256']:raise ValueError('Staging concat hash changed; retained')
            reusable=True
        elif not canonical:raise ValueError('Transfer has no verified complete copy')
    elif candidate.exists():
        pending=contextdir/'concat.pending.json'
        record=_load(pending) if pending.is_file() and not pending.is_symlink() else {}
        if (any(record.get(k)!=v for k,v in identity.items())
                or Path(record.get('output','')).resolve()!=output.resolve()
                or _absolute(record.get('concat_path',''))!=_absolute(candidate)):
            raise ValueError('Existing staging concat without matching intent; retained')
        owned_unvalidated=True
    copy_required=META if canonical else max(META,video_budget+META-releasable_bytes)
    try:
        check_space(candidate.parent,META if reusable or canonical else video_budget+META,reserve_gib)
        check_space(output.parent,copy_required,reserve_gib)
    except WaitDisk:return output,None
    candidate.parent.mkdir(parents=True,exist_ok=True)
    return candidate,{'mode':'cross_filesystem_concat','concat_path':str(candidate),
        'concat_device':source_device,'output_device':output_device,
        'concat_additional_peak_bytes':META if reusable or canonical else video_budget+META,
        'copy_additional_peak_bytes':copy_required,'releasable_bytes':releasable_bytes,
        'reserve_gib':reserve_gib,'reusable_verified_concat':reusable,
        'canonical_already_verified':canonical,'owned_unvalidated_concat':owned_unvalidated}

def _completed(output,contextdir,identity,digest):
    path=contextdir/'completed.json'
    if path.exists():
        r=_load(path)
        if any(r.get(k)!=v for k,v in identity.items()) or r.get('sha256')!=digest:
            raise ValueError('Different completed output receipt retained')
    record={**identity,'sha256':digest}
    atomic_json(path,record)
    return record

def _finish(source,output,contextdir,identity,r):
    if not output.is_file() or output.is_symlink() or sha256(output)!=r['sha256']:
        raise ValueError('Canonical complete output hash differs; staging retained')
    record=_completed(output,contextdir,identity,r['sha256'])
    # Receipt is durable before the only full staging copy may be removed.
    if source.exists():release_verified_file(source,r['sha256'])
    else:resume_verified_release(source,r['sha256'])
    atomic_json(contextdir/'transfer.completed.json',{'schema_version':1,'identity':identity,
        'output':str(output),'sha256':r['sha256'],'staging_released':True})
    return record

def _discard_owned_copy(output, expected_inode):
    # Isolate before inspecting inode; never hash a public replacement and then
    # accidentally authorize deleting it with its own freshly computed digest.
    import tempfile
    folder=Path(tempfile.mkdtemp(prefix='.'+output.name+'.copy-recovery-',dir=output.parent))
    isolated=folder/'partial'
    os.rename(output,isolated)
    st=isolated.lstat()
    if isolated.is_symlink() or [st.st_dev,st.st_ino]!=expected_inode:
        try:os.link(isolated,output,follow_symlinks=False)
        except FileExistsError:pass
        else:isolated.unlink()
        raise ValueError('Copy inode replaced during recovery; replacement preserved')
    isolated.unlink()


def _copy(source,output,contextdir,identity,r,release_callback,reserve_gib):
    pending=contextdir/'transfer.copy.pending.json'
    if output.exists():
        if output.is_symlink():raise ValueError('Canonical symlink retained')
        if sha256(output)==r['sha256']:
            release_callback()
            return _finish(source,output,contextdir,identity,r)
        # Never replace any completed different-hash output, even a stale receipt.
        if (contextdir/'completed.json').exists():raise ValueError('Different completed canonical output retained')
        p=_load(pending) if pending.is_file() else {}
        st=output.stat()
        if (p.get('identity')!=identity or p.get('output')!=str(output)
                or p.get('source_sha256')!=r['sha256']
                or p.get('inode')!=[st.st_dev,st.st_ino]):
            raise ValueError('Unowned/interchanged canonical partial retained')
        # Only the inode created by this copy attempt can be discarded on retry.
        _discard_owned_copy(output,p['inode'])
    if not source.is_file() or sha256(source)!=r['sha256']:
        raise ValueError('Verified staging copy missing or changed; no inputs released')
    release_callback()
    check_space(output.parent,r['bytes']+META,reserve_gib)
    output.parent.mkdir(parents=True,exist_ok=True)
    base={'schema_version':1,'identity':identity,'output':str(output),'source_sha256':r['sha256']}
    atomic_json(pending,{**base,'status':'create_intent'})
    # Exclusive creation prevents replacing a new or different canonical output.
    with output.open('xb') as dst:
        st=os.fstat(dst.fileno())
        atomic_json(pending,{**base,'status':'copying','inode':[st.st_dev,st.st_ino]})
        import hashlib
        digest=hashlib.sha256()
        with source.open('rb') as src:
            while block:=src.read(4*1024**2):
                check_space(output.parent,META,reserve_gib)
                dst.write(block);digest.update(block)
        dst.flush();os.fsync(dst.fileno())
    if digest.hexdigest()!=r['sha256'] or sha256(source)!=r['sha256'] or sha256(output)!=r['sha256']:
        raise ValueError('Transfer SHA mismatch; full staging copy retained')
    # Flush the directory entry as well as the file before releasing staging.
    fd=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return _finish(source,output,contextdir,identity,r)

def complete_transfer(concat_path,output,contextdir,identity,validate_callback,release_callback,
                      source_check=None,reserve_gib=25):
    """Validate+seal concat, release verified segments, then copy and hash-publish.

    source_check() must verify the original master hash before the first receipt
    is published. Callback failures propagate with the complete concat retained.
    """
    output,contextdir=_paths(output,contextdir);source=_source(concat_path,identity)
    if source.resolve()==output.resolve():raise ValueError('Staging and canonical output must be distinct')
    if 'final' in output.parts or '.enhancement-staging' in output.parts:
        raise ValueError('Canonical enhancement output must not target final delivery storage')
    contextdir.mkdir(parents=True,exist_ok=True)
    ready=contextdir/'transfer.ready.json'
    if ready.exists():
        r,saved_source=_record(contextdir,identity,output)
        if source!=saved_source:raise ValueError('Different staging path retained')
    else:
        if source_check is None:raise ValueError('Original-master hash check callback required')
        if not source.is_file():raise ValueError('Complete staging concat missing')
        before=sha256(source);validate_callback(source)
        if source_check() is False:raise ValueError('Original-master hash validation failed')
        if sha256(source)!=before:raise ValueError('Concat changed during validation')
        r={'schema_version':1,'identity':identity,'output':str(output),'concat_path':str(source),
           'sha256':before,'bytes':source.stat().st_size,'validated':True}
        atomic_json(ready,r)
    return _copy(source,output,contextdir,identity,r,release_callback,reserve_gib)

def resume_transfer(output,contextdir,identity,validate_callback,release_callback,
                    source_check=None,reserve_gib=25):
    """Return None if no durable handoff exists; never rerun AI for a saved concat."""
    output,contextdir=_paths(output,contextdir)
    if not (contextdir/'transfer.ready.json').exists():return None
    r,source=_record(contextdir,identity,output)
    return complete_transfer(source,output,contextdir,identity,validate_callback,
                             release_callback,source_check,reserve_gib)
