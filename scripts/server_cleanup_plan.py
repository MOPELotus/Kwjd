#!/usr/bin/env python3
"""E49–52 cleanup inventory; deletion requires explicit reviewed-manifest CLI.

Outputs hash-bound exact-file candidates for separate controller review. Windows
full original masters are the rebuild source; this tool asserts no Linux backup.
No recursive deletion, process signaling, GPU calls or production lock acquisition.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

EPISODES={'S01E49','S01E50','S01E51','S01E52'}
MEDIA={'.mp4','.mkv','.mov','.flac','.wav'}
AUDITIONS=('post/reports/filter_audition_20261028','post/reports/filter_audition_rec709_20261028',
           'post-home/experiments/native_face_backend_20261028','post-home/experiments/native_face_chain_20261028')


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def identity(path):
    s=Path(path).stat()
    return dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns,
                links=s.st_nlink,allocated_bytes=s.st_blocks*512)


def regular(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts or path.is_symlink():
        raise ValueError('Exact absolute regular-file path required')
    real=path.resolve(strict=True)
    if not stat.S_ISREG(real.stat().st_mode):raise ValueError('Candidate must be a regular file')
    return real


def boundary(root,item,*,allow_missing=False):
    root=Path(root).resolve(strict=True);path=Path(item['path']);episode=item['episode']
    if episode not in EPISODES:raise ValueError('Only E49–52 are in cleanup scope')
    if allow_missing and not path.exists() and not path.is_symlink():
        if not path.is_absolute() or '..' in path.parts:raise ValueError('Exact absolute path required')
        real=path.resolve()
    else:real=regular(path)
    if real.suffix.lower() not in MEDIA:raise ValueError('Retain small evidence, images, models, fonts and environments')
    if item['kind']=='old_cut_input':
        if episode in {'S01E50','S01E51','S01E52'}:raise ValueError('E50 reference and E51/E52 unaudited clock inputs are protected')
        expected=root/'input'/f'{episode}.mp4'
        if path!=expected or real!=expected.resolve(strict=not allow_missing):raise ValueError('Not the exact old cut input')
    elif item['kind']=='obsolete_audition_media':
        if episode!='S01E49':raise ValueError('Known obsolete audition roots belong to E49 only')
        allowed=False
        for rel in AUDITIONS:
            base=root/rel
            if base.exists() and path.is_relative_to(base) and real.is_relative_to(base.resolve(strict=True)):
                allowed=True
        if not allowed:raise ValueError('Media outside explicit obsolete E49 audition roots')
    else:raise ValueError('Unrecognized cleanup category; active/cache/build assets need separate review')
    for rel in ('post/final','post-home/final','post/incoming','post-home/incoming','output'):
        protected=root/rel
        if protected.exists() and real.is_relative_to(protected.resolve(strict=True)):
            raise ValueError('Final/master/sealed output is protected')
    return real


def protected_final_inodes(root):
    result=set()
    for rel in ('post/final','post-home/final'):
        directory=Path(root)/rel
        if directory.exists():
            for path in directory.rglob('*'):
                if path.is_file():
                    s=path.stat();result.add((s.st_dev,s.st_ino))
    return result


def open_fds(paths,proc=Path('/proc'),uid=None):
    """Same-UID only; examine descriptors by exact candidate inode/path, no argv dump."""
    uid=os.getuid() if uid is None else uid
    wanted={};found={str(p):[] for p in paths};warnings=[]
    for p in paths:
        s=p.stat();wanted[(s.st_dev,s.st_ino)]=str(p)
    for process in proc.iterdir():
        if not process.name.isdecimal():continue
        try:
            if process.stat().st_uid!=uid:continue
            try:descriptors=list((process/'fd').iterdir())
            except FileNotFoundError:continue
            for fd in descriptors:
                try:
                    s=fd.stat();match=wanted.get((s.st_dev,s.st_ino))
                    if match is not None:found[match].append(dict(pid=int(process.name),fd=fd.name))
                except FileNotFoundError:continue
        except FileNotFoundError:continue
        except PermissionError as error:warnings.append(dict(pid=int(process.name),error=type(error).__name__))
    return found,warnings


def locks(paths,table=Path('/proc/locks')):
    """Read exact supplied lock identities without taking/creating any lock."""
    wanted={};result=[]
    for path in paths:
        real=regular(path);s=real.stat()
        wanted[(os.major(s.st_dev),os.minor(s.st_dev),s.st_ino)]=str(real)
    for line in table.read_text().splitlines():
        fields=line.split()
        for i,field in enumerate(fields):
            match=re.fullmatch(r'([0-9a-f]+):([0-9a-f]+):(\d+)',field)
            if match:
                key=(int(match[1],16),int(match[2],16),int(match[3]))
                if key in wanted:result.append(dict(path=wanted[key],owner_pid=fields[i-1],kind=fields[1]))
    return result


def plan(root,spec,*,fd_scan=open_fds,lock_scan=locks):
    root=Path(root).resolve(strict=True)
    if spec.get('schema_version')!=1:raise ValueError('Explicit cleanup spec v1 required')
    protected=protected_final_inodes(root);rows=[];seen=set();media=[]
    for item in spec['candidates']:
        real=boundary(root,item)
        if real in seen:raise ValueError('Duplicate exact candidate')
        seen.add(real);before=identity(real)
        if (before['device'],before['inode']) in protected:raise ValueError('Candidate aliases a protected server final')
        if before['links']!=1:raise ValueError('Hardlinked candidates need separate alias accounting; no claimed reclaim')
        if not item.get('rebuild_windows_original') or not item.get('reason'):
            raise ValueError('Explicit Windows-original rebuild dependency and obsolete reason required')
        evidence=[]
        for path in item.get('evidence',[]):
            p=regular(path)
            if p.stat().st_size>16*1024**2 or p.suffix.lower() not in {'.json','.md','.txt','.log'}:
                raise ValueError('Keep small immutable boundary/acceptance evidence')
            evidence.append(dict(path=str(p),sha256=sha(p),size=p.stat().st_size))
        if not evidence:raise ValueError('Boundary/acceptance evidence required before cleanup review')
        digest=sha(real)
        if identity(real)!=before:raise ValueError('Candidate changed during SHA inventory')
        rows.append(dict(episode=item['episode'],kind=item['kind'],path=item['path'],resolved_path=str(real),
            identity=before,sha256=digest,rebuild_windows_original=item['rebuild_windows_original'],
            linux_backup_claimed=False,reason=item['reason'],evidence=evidence))
        media.append(real)
    holders,warnings=fd_scan(media)
    held=lock_scan([Path(p) for p in spec.get('lock_paths',[])])
    blocking=lock_scan([Path(p) for p in spec.get('required_lock_paths',[])])
    for row in rows:
        row['open_fds']=holders[row['resolved_path']]
        row['status']='BLOCKED_ACTIVE_OR_UNKNOWN' if row['open_fds'] or warnings or blocking else 'CANDIDATE_FOR_CONTROLLER_REVIEW'
    return dict(schema_version=1,created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        root=str(root),mode='READ_ONLY_PLAN',deletion_performed=False,deletion_authorized_by_plan=False,
        rebuild_source='Windows full original masters; no Linux backup asserted',
        candidates=rows,fd_scan_warnings=warnings,held_locks=held,lock_paths=spec.get('lock_paths',[]),
        blocking_locks=blocking,required_lock_paths=spec.get('required_lock_paths',[]),
        candidate_allocated_bytes=sum(r['identity']['allocated_bytes'] for r in rows),
        limitation='Point-in-time snapshot only. Controller must revalidate manifest SHA, file identity/SHA, Windows rebuild dependencies, exact FD holders and relevant locks immediately before any separately approved deletion. Evidence/files/finals/environments/models/fonts/SRTs are retained.')


def revalidate(manifest,*,fd_scan=open_fds,lock_scan=locks):
    paths=[]
    for row in manifest['candidates']:
        path=boundary(manifest['root'],row)
        if str(path)!=row['resolved_path'] or identity(path)!=row['identity'] or sha(path)!=row['sha256']:
            raise ValueError('Candidate changed since inventory')
        for evidence in row['evidence']:
            if sha(regular(evidence['path']))!=evidence['sha256']:raise ValueError('Retained evidence changed')
        paths.append(path)
    holders,warnings=fd_scan(paths);held=lock_scan([Path(p) for p in manifest.get('required_lock_paths',[])])
    if warnings or held or any(holders.values()):raise ValueError('Active process/lock or incomplete scan blocks cleanup')
    return dict(revalidated=True,deletion_performed=False,mode='READ_ONLY_REVALIDATION')


def dependencies(root,rows):
    """Inspect only active profile/job JSON values and same-user exact argv paths."""
    names={name for row in rows for name in (row['path'],row['resolved_path'])}
    matches=[]
    root=Path(root)
    configs=[root/'post/backend/runtime.native-active.json',root/'post/backend/runtime.json']
    configs+=list((root/'post/jobs').glob('S01E*.json'))
    def strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,dict):
            for v in value.values():yield from strings(v)
        elif isinstance(value,list):
            for v in value:yield from strings(v)
    for path in configs:
        if path.exists():
            data=json.loads(path.read_text())
            for text in strings(data):
                if text in names:matches.append(dict(kind='active_profile_or_job',path=str(path),candidate=text))
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():continue
        try:
            if process.stat().st_uid!=os.getuid():continue
            for arg in (process/'cmdline').read_bytes().split(b'\0'):
                text=os.fsdecode(arg)
                if text in names:matches.append(dict(kind='same_uid_exact_argv',pid=int(process.name),candidate=text))
        except (FileNotFoundError,PermissionError):continue
    return matches


def atomic_receipt(path,data):
    path=Path(path)
    if path.is_symlink():raise ValueError('Receipt symlink forbidden')
    fd,temp=tempfile.mkstemp(prefix=path.name+'.tmp-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(data,out,indent=2);out.flush();os.fsync(out.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def execute(manifest_path,manifest_sha256,group,*,allow_unknown_auditions=False,
            allow_reviewed_inputs=False,fd_scan=open_fds,lock_scan=locks,dependency_scan=dependencies):
    """Explicit reviewed exact-file execution. Agent must never call this on live data.

    Quarantines via same-filesystem rename, rechecks the isolated inode/identity/
    SHA, then deletes ONLY that private inode. Concurrent replacements survive.
    """
    manifest_path=regular(manifest_path)
    if sha(manifest_path)!=manifest_sha256:raise ValueError('Reviewed manifest SHA changed')
    manifest=json.loads(manifest_path.read_text());root=Path(manifest['root']).resolve(strict=True)
    if group not in ('auditions','inputs'):raise ValueError('Explicit selection group required')
    if group=='inputs' and not allow_reviewed_inputs:raise ValueError('Inputs require separate explicit controller authorization')
    rows=[r for r in manifest['candidates'] if r['kind']==('obsolete_audition_media' if group=='auditions' else 'old_cut_input')]
    if not rows:raise ValueError('Selection is empty')
    home=root/'post-home';home.mkdir(exist_ok=True)
    lockpath=home/'.server-cleanup.lock'
    if lockpath.is_symlink():raise ValueError('Cleanup lock symlink forbidden')
    with lockpath.open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt_dir=root/'post/reports/server-cleanup';receipt_dir.mkdir(parents=True,exist_ok=True)
        receipt_path=receipt_dir/(manifest_sha256+'-'+group+'.json')
        if receipt_path.is_symlink():raise ValueError('Cleanup receipt symlink forbidden')
        receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else dict(
            schema_version=1,manifest_path=str(manifest_path),manifest_sha256=manifest_sha256,
            group=group,deletion_performed=False,rows={},limitations=[])
        if receipt.get('manifest_sha256')!=manifest_sha256 or receipt.get('group')!=group:
            raise ValueError('Cleanup receipt context differs')
        for row in rows:
            key=row['resolved_path'];previous=receipt['rows'].get(key)
            if str(boundary(root,row,allow_missing=True))!=key:raise ValueError('Manifest path resolution changed')
            if previous and (previous.get('sha256')!=row['sha256'] or previous.get('identity')!=row['identity']):
                raise ValueError('Prior cleanup receipt identity differs')
            if previous and previous.get('status')=='deleted' and not Path(key).exists() and not Path(key).is_symlink():
                continue
            # A prior private isolation is owned only by this exact transaction.
            isolated=Path(previous['isolated_path']) if previous and previous.get('isolated_path') else None
            if isolated is not None and (isolated.name!='artifact' or not isolated.parent.name.startswith('.server-cleanup-')
                    or isolated.parent.parent!=Path(key).parent or isolated.parent.is_symlink()):
                raise ValueError('Private isolation escaped its original parent')
            if isolated is not None and isolated.is_symlink():raise ValueError('Private isolation symlink preserved')
            if isolated is not None and isolated.exists():
                if regular(isolated)!=isolated or identity(isolated)!=row['identity'] or sha(isolated)!=row['sha256']:
                    raise ValueError('Interrupted private isolation differs; retain')
                if Path(key).exists():raise ValueError('Concurrent public replacement retained; inspect both assets')
                isolated.unlink();previous['status']='deleted';receipt['deletion_performed']=True
                atomic_receipt(receipt_path,receipt);continue
            if isolated is not None and previous.get('status')=='verified_isolated' and not Path(key).exists():
                previous.update(status='deleted',recovery='Absent after verified isolation; unlink may have completed before receipt write')
                receipt['deletion_performed']=True;atomic_receipt(receipt_path,receipt);continue
            real=boundary(root,row)
            if str(real)!=key or identity(real)!=row['identity'] or sha(real)!=row['sha256']:
                raise ValueError('Candidate changed before cleanup')
            if (real.stat().st_dev,real.stat().st_ino) in protected_final_inodes(root) or real.stat().st_nlink!=1:
                raise ValueError('Final alias or hardlinked candidate protected')
            for evidence in row['evidence']:
                if sha(regular(evidence['path']))!=evidence['sha256']:raise ValueError('Retained evidence changed')
            refs=dependency_scan(root,[row])
            holders,warnings=fd_scan([real]);required=lock_scan([Path(p) for p in manifest.get('required_lock_paths',[])])
            if refs or holders[str(real)] or required:raise ValueError('Exact active dependency/FD or relevant lock blocks cleanup')
            permitted=(allow_unknown_auditions and group=='auditions' and row['episode']=='S01E49'
                and any(real.is_relative_to((root/rel).resolve()) for rel in AUDITIONS[:2]))
            if warnings and not permitted:raise ValueError('Unknown same-UID FD visibility requires closed-E49-audition-only override')
            if warnings:receipt['limitations'].append(dict(candidate=key,unknown_same_uid_fd_processes=warnings,
                override='Explicit closed E49 audition override; no known holders/dependencies; no claim of complete FD visibility. Open-file unlink would retain the reader inode.'))
            quarantine=Path(tempfile.mkdtemp(prefix='.server-cleanup-',dir=real.parent))
            isolated=quarantine/'artifact'
            entry=dict(status='isolation_intent',isolated_path=str(isolated),sha256=row['sha256'],identity=row['identity'])
            receipt['rows'][key]=entry;atomic_receipt(receipt_path,receipt)
            os.rename(real,isolated)
            if isolated.is_symlink() or identity(isolated)!=row['identity'] or sha(isolated)!=row['sha256']:
                entry['status']='replacement_preserved';atomic_receipt(receipt_path,receipt)
                if not real.exists() and not real.is_symlink():
                    os.link(isolated,real,follow_symlinks=False);isolated.unlink()
                raise ValueError('Identity race: replacement preserved, never deleted')
            entry['status']='verified_isolated';atomic_receipt(receipt_path,receipt)
            isolated.unlink();entry['status']='deleted';receipt['deletion_performed']=True
            atomic_receipt(receipt_path,receipt)
        receipt['completed']=True;atomic_receipt(receipt_path,receipt)
        return dict(receipt_path=str(receipt_path),receipt_sha256=sha(receipt_path),**receipt)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--spec',type=Path)
    parser.add_argument('--check-plan',type=Path)
    parser.add_argument('--plan-sha256')
    parser.add_argument('--execute',type=Path,help='Execute exact selected files from a controller-reviewed manifest')
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--select-group',choices=('auditions','inputs'))
    parser.add_argument('--allow-reviewed-inputs',action='store_true')
    parser.add_argument('--allow-unknown-fd-for-closed-e49-auditions',action='store_true')
    args=parser.parse_args()
    if args.execute:
        if not args.manifest_sha256 or not args.select_group:parser.error('Execution requires reviewed manifest SHA and explicit selection group')
        result=execute(args.execute,args.manifest_sha256,args.select_group,
            allow_unknown_auditions=args.allow_unknown_fd_for_closed_e49_auditions,
            allow_reviewed_inputs=args.allow_reviewed_inputs)
    elif args.check_plan:
        if not args.plan_sha256 or sha(args.check_plan)!=args.plan_sha256:parser.error('Explicit unchanged plan SHA required')
        result=revalidate(json.loads(args.check_plan.read_text()))
    else:
        if not args.root or not args.spec:parser.error('--root and --spec required for inventory')
        result=plan(args.root,json.loads(args.spec.read_text()))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
