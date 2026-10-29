"""Read-only cleanup tests on temporary files and fake proc/locks only."""
import copy
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import server_cleanup_plan as cleanup


def no_fds(paths):return {str(p):[] for p in paths},[]
def no_locks(paths):return []


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'input').mkdir()
        self.media=self.root/'input/S01E49.mp4';self.media.write_bytes(b'old cut input')
        self.evidence=self.root/'reports/review.json';self.evidence.parent.mkdir();self.evidence.write_text('{"reviewed": true}')
        self.item=dict(episode='S01E49',kind='old_cut_input',path=str(self.media),
            rebuild_windows_original='Windows full original E49 master required',reason='obsolete cut OCR source',evidence=[str(self.evidence)])
        self.spec=dict(schema_version=1,candidates=[self.item],lock_paths=[])

    def plan(self):return cleanup.plan(self.root,self.spec,fd_scan=no_fds,lock_scan=no_locks)

    def test_plan_and_revalidate_are_read_only_and_do_not_authorize_deletion(self):
        before=self.media.read_bytes();manifest=self.plan()
        self.assertEqual(self.media.read_bytes(),before)
        self.assertEqual(manifest['candidates'][0]['sha256'],cleanup.sha(self.media))
        self.assertFalse(manifest['deletion_performed']);self.assertFalse(manifest['deletion_authorized_by_plan'])
        self.assertFalse(manifest['candidates'][0]['linux_backup_claimed'])
        self.assertTrue(cleanup.revalidate(manifest,fd_scan=no_fds,lock_scan=no_locks)['revalidated'])
        self.assertTrue(self.media.exists());self.assertTrue(self.evidence.exists())

    def test_e50_reference_e51_unaudited_and_e53_to_e55_inputs_protected(self):
        for ep in ('S01E50','S01E51','S01E52','S01E53','S01E54','S01E55'):
            p=self.root/f'input/{ep}.mp4';p.write_bytes(b'protected')
            self.item.update(episode=ep,path=str(p))
            with self.subTest(episode=ep),self.assertRaises(ValueError):self.plan()

    def test_episode52_old_cut_is_held_for_future_clock_mapping(self):
        p=self.root/'input/S01E52.mp4';p.write_bytes(b'old cut E52')
        self.item.update(episode='S01E52',path=str(p))
        with self.assertRaises(ValueError):self.plan()

    def test_input_named_substitute_or_symlink_escape_rejected(self):
        outside=self.root/'other.mp4';outside.write_bytes(b'other')
        self.item['path']=str(outside)
        with self.assertRaises(ValueError):self.plan()
        self.media.unlink();self.media.symlink_to(outside);self.item['path']=str(self.media)
        with self.assertRaises(ValueError):self.plan()

    def test_old_audition_accepts_post_parent_symlink_but_rejects_nested_escape(self):
        physical=self.root/'physical-post';physical.mkdir();(self.root/'post').symlink_to(physical,target_is_directory=True)
        base=self.root/'post/reports/filter_audition_20261028';base.mkdir(parents=True)
        p=base/'baseline.mkv';p.write_bytes(b'obsolete')
        self.item.update(kind='obsolete_audition_media',path=str(p))
        self.assertEqual(self.plan()['candidates'][0]['resolved_path'],str(p.resolve()))
        outside=self.root/'outside';outside.mkdir();(outside/'movie.mkv').write_bytes(b'other')
        (base/'escape').symlink_to(outside,target_is_directory=True)
        self.item['path']=str(base/'escape/movie.mkv')
        with self.assertRaises(ValueError):self.plan()

    def test_final_hardlink_and_unknown_hardlinks_never_count_as_reclaim(self):
        final=self.root/'post-home/final/S01E49.mkv';final.parent.mkdir(parents=True)
        os.link(self.media,final)
        with self.assertRaisesRegex(ValueError,'protected server final'):self.plan()
        final.unlink();os.link(self.media,self.root/'other-link.mp4')
        with self.assertRaisesRegex(ValueError,'Hardlinked'):self.plan()

    def test_evidence_models_and_shared_environments_are_not_candidates(self):
        for path in (self.evidence,self.root/'work/venv/model.pth',self.root/'output/S01E49.zh.srt'):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'keep')
            self.item['path']=str(path)
            with self.subTest(path=path),self.assertRaises(ValueError):self.plan()

    def test_explicit_rebuild_dependency_and_evidence_are_mandatory(self):
        for key,value in [('rebuild_windows_original',''),('evidence',[])]:
            old=self.item[key];self.item[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.plan()
            self.item[key]=old

    def test_open_exact_fd_or_unknown_scan_blocks(self):
        def opened(paths):return {str(p):[{'pid':123,'fd':'4'}] for p in paths},[]
        manifest=cleanup.plan(self.root,self.spec,fd_scan=opened,lock_scan=no_locks)
        self.assertEqual(manifest['candidates'][0]['status'],'BLOCKED_ACTIVE_OR_UNKNOWN')
        with self.assertRaises(ValueError):cleanup.revalidate(manifest,fd_scan=opened,lock_scan=no_locks)
        def unknown(paths):return {str(p):[] for p in paths},[{'pid':123,'error':'PermissionError'}]
        self.assertEqual(cleanup.plan(self.root,self.spec,fd_scan=unknown,lock_scan=no_locks)['candidates'][0]['status'],'BLOCKED_ACTIVE_OR_UNKNOWN')

    def test_lock_table_exact_inode_check_does_not_acquire_or_touch_lock(self):
        lock=self.root/'worker.lock';lock.write_bytes(b'unchanged')
        s=lock.stat();table=self.root/'locks'
        table.write_text(f'1: FLOCK ADVISORY WRITE 456 {os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino} 0 EOF\n')
        observed=cleanup.locks([lock],table)
        self.assertEqual(observed[0]['owner_pid'],'456');self.assertEqual(lock.read_bytes(),b'unchanged')
        self.spec['required_lock_paths']=[str(lock)]
        manifest=cleanup.plan(self.root,self.spec,fd_scan=no_fds,lock_scan=lambda paths:observed)
        self.assertEqual(manifest['candidates'][0]['status'],'BLOCKED_ACTIVE_OR_UNKNOWN')

    def test_unrelated_production_locks_are_observations_not_cleanup_blockers(self):
        self.spec['lock_paths']=['/observed-production.lock']
        def observe(paths):return [{'owner_pid':'999'}] if paths else []
        manifest=cleanup.plan(self.root,self.spec,fd_scan=no_fds,lock_scan=observe)
        self.assertTrue(manifest['held_locks']);self.assertEqual(manifest['blocking_locks'],[])
        self.assertEqual(manifest['candidates'][0]['status'],'CANDIDATE_FOR_CONTROLLER_REVIEW')
        cleanup.revalidate(manifest,fd_scan=no_fds,lock_scan=observe)

    def test_revalidation_rejects_replaced_media_or_changed_evidence(self):
        manifest=self.plan();self.media.write_bytes(b'new different data')
        with self.assertRaises(ValueError):cleanup.revalidate(manifest,fd_scan=no_fds,lock_scan=no_locks)
        self.media.write_bytes(b'old cut input');manifest=self.plan();self.evidence.write_text('{}')
        with self.assertRaises(ValueError):cleanup.revalidate(manifest,fd_scan=no_fds,lock_scan=no_locks)

    def test_fake_proc_fd_scan_uses_exact_inode_and_filters_uid(self):
        proc=self.root/'proc';fd=proc/'123/fd';fd.mkdir(parents=True)
        (fd/'4').symlink_to(self.media)
        other=self.root/'other.mp4';other.write_bytes(b'not candidate');(fd/'5').symlink_to(other)
        found,warnings=cleanup.open_fds([self.media],proc=proc)
        self.assertEqual(found[str(self.media)],[dict(pid=123,fd='4')]);self.assertEqual(warnings,[])
        self.assertEqual(cleanup.open_fds([self.media],proc=proc,uid=os.getuid()+1)[0][str(self.media)],[])

    def audition_manifest(self):
        p=self.root/'post/reports/filter_audition_20261028/baseline/part_00.mkv'
        p.parent.mkdir(parents=True);p.write_bytes(b'obsolete audition')
        self.item.update(kind='obsolete_audition_media',path=str(p))
        manifest=self.plan();path=self.root/'reviewed-cleanup.json';path.write_text(json.dumps(manifest))
        return p,path,cleanup.sha(path)

    def run_execute(self,path,digest,**kwargs):
        return cleanup.execute(path,digest,'auditions',fd_scan=kwargs.pop('fd_scan',no_fds),
            lock_scan=no_locks,dependency_scan=kwargs.pop('dependency_scan',lambda root,rows:[]),**kwargs)

    def test_execute_exact_selection_keeps_input_evidence_and_is_idempotent(self):
        p,path,digest=self.audition_manifest()
        result=self.run_execute(path,digest)
        self.assertFalse(p.exists());self.assertTrue(self.media.exists());self.assertTrue(self.evidence.exists())
        self.assertTrue(result['deletion_performed']);self.assertTrue(result['completed'])
        again=self.run_execute(path,digest)
        self.assertEqual(again['rows'],result['rows'])

    def test_execute_requires_reviewed_manifest_sha_and_separate_input_authorization(self):
        p,path,digest=self.audition_manifest()
        with self.assertRaises(ValueError):self.run_execute(path,'0'*64)
        with self.assertRaises(ValueError):cleanup.execute(path,digest,'inputs')
        self.assertTrue(p.exists())

    def test_execute_own_cleanup_lock_refuses_concurrent_cleanup_without_worker_lock(self):
        p,path,digest=self.audition_manifest();home=self.root/'post-home';home.mkdir()
        with (home/'.server-cleanup.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.run_execute(path,digest)
        self.assertTrue(p.exists())

    def test_unknown_fd_override_is_only_for_closed_old_two_audition_roots(self):
        def unknown(paths):return {str(p):[] for p in paths},[{'pid':123,'error':'PermissionError'}]
        p,path,digest=self.audition_manifest()
        with self.assertRaises(ValueError):self.run_execute(path,digest,fd_scan=unknown)
        result=self.run_execute(path,digest,fd_scan=unknown,allow_unknown_auditions=True)
        self.assertFalse(p.exists());self.assertTrue(result['limitations'])
        self.assertIn('no claim of complete FD visibility',result['limitations'][0]['override'])

    def test_unknown_override_never_bypasses_known_fd_or_current_dependency(self):
        p,path,digest=self.audition_manifest()
        def known(paths):return {str(p):[{'pid':123,'fd':'4'}] for p in paths},[]
        with self.assertRaises(ValueError):self.run_execute(path,digest,fd_scan=known,allow_unknown_auditions=True)
        with self.assertRaises(ValueError):self.run_execute(path,digest,dependency_scan=lambda root,rows:[{'current':True}],allow_unknown_auditions=True)
        self.assertTrue(p.exists())

    def test_execute_rechecks_identity_and_final_aliases(self):
        p,path,digest=self.audition_manifest();p.write_bytes(b'new replacement')
        with self.assertRaises(ValueError):self.run_execute(path,digest)
        self.assertEqual(p.read_bytes(),b'new replacement')

    def test_execute_preserves_replacement_racing_isolation(self):
        p,path,digest=self.audition_manifest();real_rename=os.rename
        def raced(source,destination):
            Path(source).unlink();Path(source).write_bytes(b'new external replacement')
            real_rename(source,destination)
        with patch.object(cleanup.os,'rename',side_effect=raced),self.assertRaisesRegex(ValueError,'Identity race'):
            self.run_execute(path,digest)
        self.assertEqual(p.read_bytes(),b'new external replacement')

    def test_execute_does_not_follow_candidate_or_receipt_symlinks(self):
        p,path,digest=self.audition_manifest();p.unlink();p.symlink_to(self.media)
        with self.assertRaises(ValueError):self.run_execute(path,digest)
        self.assertTrue(self.media.exists())

    def test_execute_resumes_verified_private_isolation_after_unlink_receipt_crash(self):
        p,path,digest=self.audition_manifest();real_atomic=cleanup.atomic_receipt
        def crash(target,data):
            if any(row.get('status')=='deleted' for row in data.get('rows',{}).values()):
                raise OSError('simulated receipt publication failure after unlink')
            real_atomic(target,data)
        with patch.object(cleanup,'atomic_receipt',side_effect=crash),self.assertRaises(OSError):self.run_execute(path,digest)
        self.assertFalse(p.exists())
        result=self.run_execute(path,digest)
        self.assertTrue(result['completed'])
        self.assertIn('Absent after verified isolation',result['rows'][str(p)]['recovery'])

    def test_recovered_private_isolation_still_checks_exact_fd_holders(self):
        p,path,digest=self.audition_manifest();real_atomic=cleanup.atomic_receipt
        def crash(target,data):
            if any(row.get('status')=='verified_isolated' for row in data.get('rows',{}).values()):
                raise OSError('crash before private unlink')
            real_atomic(target,data)
        with patch.object(cleanup,'atomic_receipt',side_effect=crash),self.assertRaises(OSError):self.run_execute(path,digest)
        def holder(paths):return {str(p):[{'pid':42,'fd':'3'}] for p in paths},[]
        with self.assertRaisesRegex(ValueError,'recovered-isolation'):self.run_execute(path,digest,fd_scan=holder,allow_unknown_auditions=True)
        receipt_path=self.root/'post/reports/server-cleanup'/(digest+'-auditions.json')
        receipt=json.loads(receipt_path.read_text())
        self.assertTrue(Path(receipt['rows'][str(p)]['isolated_path']).exists())


if __name__=='__main__':unittest.main()
