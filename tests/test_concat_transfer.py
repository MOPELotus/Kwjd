import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import concat_transfer as transfer
from enhance_stage import sha256, WaitDisk


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name);self.identity={'episode':'S01E49',
            'master_sha256':'a'*64,'backend_fingerprint':'b'*64,'input_frames':60,'output_frames':144}
        self.out=self.post/'cache/S01E49/S01E49.enhanced.partial.mkv';self.out.parent.mkdir(parents=True)
        self.ctx=self.out.parent/'S01E49.segments/context';self.ctx.mkdir(parents=True)
        self.src=self.post/'final/.enhancement-staging/S01E49'/('a'*16+'-'+'b'*16)/'enhancement.concat.mkv'
        self.src.parent.mkdir(parents=True);self.src.write_bytes(b'complete verified enhancement'*100)
        self.calls=[]
    def validate(self,p):
        self.assertEqual(p.read_bytes(),b'complete verified enhancement'*100);self.calls.append('validate')
    def release(self):
        ready=json.loads((self.ctx/'transfer.ready.json').read_text())
        self.assertEqual(ready['sha256'],sha256(self.src));self.calls.append('release')
    def run_transfer(self,**kw):
        with patch.object(transfer,'check_space',return_value=10**12):
            return transfer.complete_transfer(self.src,self.out,self.ctx,self.identity,
                self.validate,self.release,source_check=lambda:True,**kw)
    def test_completed_copy_is_hash_bound_and_only_staging_is_released(self):
        record=self.run_transfer()
        self.assertEqual(record['sha256'],sha256(self.out));self.assertFalse(self.src.exists())
        self.assertEqual(self.calls,['validate','release'])
        self.assertEqual(json.loads((self.ctx/'completed.json').read_text()),record)
        # Resume needs no model, no concat decode, no extant staging file.
        with patch.object(transfer,'check_space',return_value=10**12):
            r=transfer.resume_transfer(self.out,self.ctx,self.identity,lambda p:self.fail('redecode'),lambda:None)
        self.assertEqual(r,record);self.assertTrue(self.out.exists())
    def test_source_validation_failure_retains_complete_concat_and_segments(self):
        with patch.object(transfer,'check_space'),self.assertRaisesRegex(ValueError,'hash validation'):
            transfer.complete_transfer(self.src,self.out,self.ctx,self.identity,self.validate,
                lambda:self.fail('released'),source_check=lambda:False)
        self.assertTrue(self.src.exists());self.assertFalse(self.out.exists())
    def test_failed_release_resumes_without_revalidating_concat(self):
        with patch.object(transfer,'check_space'),self.assertRaisesRegex(RuntimeError,'crash'):
            transfer.complete_transfer(self.src,self.out,self.ctx,self.identity,self.validate,
                lambda:(_ for _ in ()).throw(RuntimeError('crash')),source_check=lambda:True)
        self.assertTrue(self.src.exists());self.assertFalse(self.out.exists())
        with patch.object(transfer,'check_space'):
            transfer.resume_transfer(self.out,self.ctx,self.identity,lambda p:self.fail('redecode'),self.release)
        self.assertTrue(self.out.exists());self.assertFalse(self.src.exists())
    def test_interrupted_copy_recovers_owned_inode_and_retains_source_until_done(self):
        real_fsync=transfer.os.fsync;failed=[False]
        def crash(fd):
            if not failed[0]:failed[0]=True;raise RuntimeError('copy crash')
            return real_fsync(fd)
        with patch.object(transfer.os,'fsync',side_effect=crash),self.assertRaisesRegex(RuntimeError,'copy crash'):
            self.run_transfer()
        self.assertTrue(self.src.exists());self.assertTrue(self.out.exists())
        # Simulate a truncated writer before a crash (matching recorded inode).
        self.out.write_bytes(b'truncated')
        with patch.object(transfer,'check_space'):
            r=transfer.resume_transfer(self.out,self.ctx,self.identity,lambda p:self.fail('redecode'),self.release)
        self.assertEqual(sha256(self.out),r['sha256']);self.assertFalse(self.src.exists())
    def test_different_canonical_is_never_replaced(self):
        self.out.write_bytes(b'different-complete-output')
        with self.assertRaisesRegex(ValueError,'Unowned'):
            self.run_transfer()
        self.assertEqual(self.out.read_bytes(),b'different-complete-output');self.assertTrue(self.src.exists())
        self.assertNotIn('release',self.calls)
    def test_completed_hash_mismatch_is_never_relabelled(self):
        self.out.write_bytes(b'different-complete-output')
        (self.ctx/'completed.json').write_text(json.dumps({**self.identity,'sha256':sha256(self.out)}))
        with self.assertRaisesRegex(ValueError,'Different completed'):
            self.run_transfer()
        self.assertTrue(self.src.exists());self.assertEqual(self.out.read_bytes(),b'different-complete-output')
    def test_copy_recovery_does_not_delete_a_same_name_replacement(self):
        self.out.write_bytes(b'partial');st=self.out.stat();old=[st.st_dev,st.st_ino]
        rename=transfer.os.rename
        def race(src,dst):
            newer=self.out.with_name('new.tmp');newer.write_bytes(b'NEW-CANONICAL');newer.replace(self.out)
            rename(src,dst)
        with patch.object(transfer.os,'rename',side_effect=race),self.assertRaisesRegex(ValueError,'inode replaced'):
            transfer._discard_owned_copy(self.out,old)
        self.assertEqual(self.out.read_bytes(),b'NEW-CANONICAL')
    def test_wrong_identity_and_inner_symlink_are_refused(self):
        with patch.object(transfer,'check_space'),self.assertRaises(RuntimeError):
            transfer.complete_transfer(self.src,self.out,self.ctx,self.identity,self.validate,
                lambda:(_ for _ in ()).throw(RuntimeError()),source_check=lambda:True)
        with self.assertRaisesRegex(ValueError,'bind'):
            transfer.resume_transfer(self.out,self.ctx,{**self.identity,'master_sha256':'c'*64},self.validate,self.release)
        other=self.post/'elsewhere';other.mkdir();self.src.unlink();self.src.parent.rmdir()
        self.src.parent.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):
            transfer.complete_transfer(self.src,self.out,self.ctx,self.identity,self.validate,self.release,source_check=lambda:True)
    def test_no_cross_volume_returns_fallback_without_touching_staging(self):
        path,plan=transfer.choose_concat(self.post,self.out,self.ctx,self.identity,100)
        self.assertEqual(path,self.out);self.assertIsNone(plan);self.assertTrue(self.src.exists())
    def test_choose_distinct_volumes_budget_and_preconfigured_final_link(self):
        from types import SimpleNamespace
        ancestor=transfer._ancestor
        class Ancestor:
            def __init__(self,device):self.device=device
            def stat(self):return SimpleNamespace(st_dev=self.device)
        with patch.object(transfer,'_ancestor',side_effect=lambda p:Ancestor(2 if '.enhancement-staging' in str(p) else 1)), \
             patch.object(transfer,'check_space',return_value=99) as check:
            self.src.unlink()
            path,plan=transfer.choose_concat(self.post,self.out,self.ctx,self.identity,100)
            self.assertEqual(path,self.src);self.assertEqual(plan['mode'],'cross_filesystem_concat')
            self.assertEqual(check.call_count,2)
        with patch.object(transfer,'_ancestor',side_effect=lambda p:Ancestor(2 if '.enhancement-staging' in str(p) else 1)), \
             patch.object(transfer,'check_space',side_effect=WaitDisk('full')):
            self.assertEqual(transfer.choose_concat(self.post,self.out,self.ctx,self.identity,100),(self.out,None))

    def test_pending_unsealed_concat_is_returned_without_crediting_its_bytes(self):
        from types import SimpleNamespace
        class Ancestor:
            def __init__(self,device):self.device=device
            def stat(self):return SimpleNamespace(st_dev=self.device)
        (self.ctx/'concat.pending.json').write_text(json.dumps({**self.identity,
            'output':str(self.out.resolve()),'concat_path':str(self.src.absolute()),'segments':[]}))
        with patch.object(transfer,'_ancestor',side_effect=lambda p:Ancestor(2 if '.enhancement-staging' in str(p) else 1)), \
             patch.object(transfer,'check_space',return_value=99) as check:
            path,plan=transfer.choose_concat(self.post,self.out,self.ctx,self.identity,100)
        self.assertEqual(path,self.src);self.assertTrue(plan['owned_unvalidated_concat'])
        self.assertFalse(plan['reusable_verified_concat'])
        self.assertEqual(plan['concat_additional_peak_bytes'],100+transfer.META)
    def test_root_copy_budget_credits_only_caller_declared_releasable_segments(self):
        from types import SimpleNamespace
        class Ancestor:
            def __init__(self,device):self.device=device
            def stat(self):return SimpleNamespace(st_dev=self.device)
        self.src.unlink()
        with patch.object(transfer,'_ancestor',side_effect=lambda p:Ancestor(2 if '.enhancement-staging' in str(p) else 1)), \
             patch.object(transfer,'check_space',return_value=99) as check:
            path,plan=transfer.choose_concat(self.post,self.out,self.ctx,self.identity,100,releasable_bytes=70)
        self.assertEqual(plan['copy_additional_peak_bytes'],transfer.META+30)
        self.assertEqual(plan['concat_additional_peak_bytes'],transfer.META+100)
        self.assertEqual(check.call_args_list[1].args[1],transfer.META+30)
    def test_final_delivery_path_is_never_a_transfer_target(self):
        bad=self.post/'final/S01E49.final.mkv';context=bad.parent/'context';context.mkdir()
        with self.assertRaisesRegex(ValueError,'final delivery'):
            transfer.complete_transfer(self.src,bad,context,self.identity,self.validate,self.release,source_check=lambda:True)

if __name__=='__main__':unittest.main()
