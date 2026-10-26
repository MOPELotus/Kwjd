import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from enhance_stage import sha256
import linux_stage_handoff as handoff


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg integration required')
class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.post=Path(self.temp.name);(self.post/'incoming').mkdir();(self.post/'cache/S01E49').mkdir(parents=True)
        self.master=self.post/'incoming/S01E49.clean-master.mkv'
        self.enhanced=self.post/'cache/S01E49/S01E49.enhanced.mkv'
        self.bundle=self.enhanced.with_name('S01E49.source.ready.json');self.log=self.post/'log'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=s=1920x1080:r=25:d=0.12',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.12',
            '-c:v','ffv1','-c:a','pcm_s24le','-ac','2',str(self.master)],check=True)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=s=1920x1080:r=60',
            '-frames:v','8','-c:v','ffv1',str(self.enhanced)],check=True)
        self.masterhash=sha256(self.master)
        self.checkpoint={'master_sha256':self.masterhash,'sha256':sha256(self.enhanced)}

    def preserve(self):
        return handoff.preserve_source(self.master,self.enhanced,self.checkpoint,self.bundle,'S01E49',self.log)

    def test_verified_audio_preserves_pcm_then_release_and_resume(self):
        record,audio=self.preserve()
        self.assertEqual(handoff.decoded_pcm_hash(self.master),handoff.decoded_pcm_hash(audio))
        with self.assertRaisesRegex(ValueError,'Worker lock'):
            handoff.release_master_copy(self.post,self.master,self.bundle,self.enhanced,self.masterhash)
        self.assertTrue(self.master.exists())
        handoff.release_master_copy(self.post,self.master,self.bundle,self.enhanced,self.masterhash,worker_lock_held=True)
        self.assertFalse(self.master.exists());self.assertTrue(audio.exists())
        recovered,_=handoff.validate_bundle(self.bundle,self.enhanced,self.masterhash)
        self.assertEqual(recovered['source_probe'],record['source_probe'])
        handoff.release_master_copy(self.post,self.master,self.bundle,self.enhanced,self.masterhash,worker_lock_held=True)

    def test_same_name_new_master_and_corrupt_audio_are_never_deleted(self):
        _,audio=self.preserve();self.master.write_bytes(b'new-export')
        with self.assertRaisesRegex(ValueError,'replacement differs'):
            handoff.release_master_copy(self.post,self.master,self.bundle,self.enhanced,self.masterhash,worker_lock_held=True)
        self.assertEqual(self.master.read_bytes(),b'new-export')
        audio.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'audio hash differs'):
            handoff.release_master_copy(self.post,self.master,self.bundle,self.enhanced,self.masterhash,worker_lock_held=True)
        self.assertTrue(self.master.exists())

    def test_handoff_rejects_unbound_master_and_orphan_audio(self):
        with self.assertRaisesRegex(ValueError,'Source/enhancement differs'):
            handoff.preserve_source(self.master,self.enhanced,{**self.checkpoint,'master_sha256':'other'},self.bundle,'S01E49',self.log)
        self.bundle.with_name('S01E49.mixed.flac').write_bytes(b'orphan')
        with self.assertRaisesRegex(ValueError,'Orphan'):
            self.preserve()
        self.assertEqual(sha256(self.master),self.masterhash)

    def test_completed_audio_recovers_after_receipt_failure_without_encoding_again(self):
        atomic=handoff.atomic_json
        def fail_receipt(path,data):
            if Path(path)==self.bundle:
                raise OSError('simulated receipt publication crash')
            return atomic(path,data)
        with patch.object(handoff,'atomic_json',side_effect=fail_receipt),self.assertRaises(OSError):
            self.preserve()
        audio=self.bundle.with_name('S01E49.mixed.flac')
        self.assertTrue(audio.exists());self.assertFalse(self.bundle.exists())
        original_run=subprocess.run
        def reject_encoding(args,**kwargs):
            if 'flac' in args and 'pipe:1' in args:
                raise AssertionError('Previously finished audio must not be encoded again')
            return original_run(args,**kwargs)
        with patch.object(handoff.subprocess,'run',side_effect=reject_encoding):
            record,recovered=self.preserve()
        self.assertEqual(recovered,audio);self.assertTrue(record['pcm_equivalent'])
        self.assertEqual(handoff.decoded_pcm_hash(audio),handoff.decoded_pcm_hash(self.master))
        self.assertEqual(sha256(self.master),self.masterhash)

    def test_verified_partial_recovers_before_audio_link(self):
        with patch.object(handoff.os,'link',side_effect=OSError('simulated link crash')),self.assertRaises(OSError):
            self.preserve()
        partial=self.bundle.with_name('S01E49.mixed.partial.flac')
        self.assertTrue(partial.exists())
        original_run=subprocess.run
        def reject_encoding(args,**kwargs):
            if 'flac' in args and 'pipe:1' in args:raise AssertionError('No audio re-encode')
            return original_run(args,**kwargs)
        with patch.object(handoff.subprocess,'run',side_effect=reject_encoding):
            _,audio=self.preserve()
        self.assertTrue(audio.exists());self.assertFalse(partial.exists())

    def test_complete_partial_recovers_before_validation_receipt(self):
        with patch.object(handoff,'decoded_video_frames',side_effect=RuntimeError('decode audit interrupted')),self.assertRaises(RuntimeError):
            self.preserve()
        intent=json.loads(self.bundle.with_name('S01E49.source.pending.json').read_text())
        self.assertEqual(intent['status'],'encoding')
        original_run=subprocess.run
        def reject_encoding(args,**kwargs):
            if 'flac' in args and 'pipe:1' in args:raise AssertionError('Complete partial must be reused')
            return original_run(args,**kwargs)
        with patch.object(handoff.subprocess,'run',side_effect=reject_encoding):
            record,audio=self.preserve()
        self.assertTrue(record['pcm_equivalent']);self.assertTrue(audio.exists())

    def test_different_audio_after_receipt_crash_is_retained(self):
        atomic=handoff.atomic_json
        def fail_receipt(path,data):
            if Path(path)==self.bundle:raise OSError('receipt crash')
            return atomic(path,data)
        with patch.object(handoff,'atomic_json',side_effect=fail_receipt),self.assertRaises(OSError):self.preserve()
        audio=self.bundle.with_name('S01E49.mixed.flac');audio.write_bytes(b'replacement')
        with self.assertRaisesRegex(ValueError,'hash differs'):self.preserve()
        self.assertEqual(audio.read_bytes(),b'replacement');self.assertTrue(self.master.exists())

    def test_owned_incomplete_partial_is_audited_and_recreated_only_as_audio(self):
        original_run=subprocess.run
        def interrupt_encoding(args,**kwargs):
            if 'flac' in args and 'pipe:1' in args:
                kwargs['stdout'].write(b'incomplete FLAC')
                raise subprocess.CalledProcessError(1,args)
            return original_run(args,**kwargs)
        with patch.object(handoff.subprocess,'run',side_effect=interrupt_encoding),self.assertRaises(subprocess.CalledProcessError):self.preserve()
        partial=self.bundle.with_name('S01E49.mixed.partial.flac');digest=sha256(partial)
        record,audio=self.preserve()
        intent=json.loads(self.bundle.with_name('S01E49.source.pending.json').read_text())
        self.assertEqual(intent['previous_release']['released_partial_sha256'],digest)
        self.assertTrue(Path(intent['previous_release']['partial_release_receipt']).is_file())
        self.assertTrue(record['pcm_equivalent']);self.assertTrue(audio.exists())
        self.assertEqual(sha256(self.master),self.masterhash)

    def test_different_partial_inode_does_not_gain_ownership_from_intent(self):
        original_run=subprocess.run
        def interrupt_encoding(args,**kwargs):
            if 'flac' in args and 'pipe:1' in args:raise subprocess.CalledProcessError(1,args)
            return original_run(args,**kwargs)
        with patch.object(handoff.subprocess,'run',side_effect=interrupt_encoding),self.assertRaises(subprocess.CalledProcessError):self.preserve()
        partial=self.bundle.with_name('S01E49.mixed.partial.flac')
        replacement=partial.with_name('replacement');replacement.write_bytes(b'unknown owner');replacement.replace(partial)
        with self.assertRaisesRegex(ValueError,'recoverable handoff intent'):self.preserve()
        self.assertEqual(partial.read_bytes(),b'unknown owner')

    def test_owned_decodable_short_flac_is_audited_and_recreated_only_as_audio(self):
        original_run=subprocess.run
        def short_complete_block_then_crash(args,**kwargs):
            if 'flac' in args and 'pipe:1' in args:
                original_run(args[:-1]+['-t','0.04',args[-1]],**kwargs)
                raise RuntimeError('crash after short but decodable FLAC')
            return original_run(args,**kwargs)
        with patch.object(handoff.subprocess,'run',side_effect=short_complete_block_then_crash),self.assertRaises(RuntimeError):
            self.preserve()
        partial=self.bundle.with_name('S01E49.mixed.partial.flac');digest=sha256(partial)
        # This is the precise gap: probe and full decode both succeed, but the
        # sample-identical proof fails because the owned partial is too short.
        handoff.validate_audio(handoff.probe(self.master),handoff.probe(partial))
        self.assertNotEqual(handoff.decoded_pcm_hash(partial),handoff.decoded_pcm_hash(self.master))
        record,audio=self.preserve()
        previous=json.loads(self.bundle.with_name('S01E49.source.pending.json').read_text())['previous_release']
        self.assertEqual(previous['released_partial_sha256'],digest)
        self.assertEqual(previous['partial_release_reason'],'decoded PCM differs from complete source')
        self.assertTrue(Path(previous['partial_release_receipt']).is_file())
        self.assertTrue(record['pcm_equivalent'])
        self.assertEqual(handoff.decoded_pcm_hash(audio),handoff.decoded_pcm_hash(self.master))
        self.assertEqual(sha256(self.master),self.masterhash)

    def test_previous_stage_release_requires_verified_final_and_preserves_final(self):
        record,audio=self.preserve();(self.post/'final').mkdir()
        final=self.post/'final/S01E49.final-1080p60.mkv';final.write_bytes(b'accepted-final')
        report={'episode':'S01E49','sha256':sha256(final),'final_decode_checked':True,
                'final_pcm_equivalent':True,'enhanced_sha256':sha256(self.enhanced),
                'preserved_audio_sha256':sha256(audio)}
        with self.assertRaisesRegex(ValueError,'acceptance receipt'):
            handoff.release_enhancement_inputs(self.post,'S01E49',self.enhanced,audio,final,
                {**report,'final_pcm_equivalent':False},worker_lock_held=True)
        self.assertTrue(self.enhanced.exists());self.assertTrue(audio.exists())
        handoff.release_enhancement_inputs(self.post,'S01E49',self.enhanced,audio,final,report,worker_lock_held=True)
        self.assertFalse(self.enhanced.exists());self.assertFalse(audio.exists())
        self.assertEqual(final.read_bytes(),b'accepted-final');self.assertTrue(self.bundle.exists())
        handoff.release_enhancement_inputs(self.post,'S01E49',self.enhanced,audio,final,report,worker_lock_held=True)


class ReleaseRaceTests(unittest.TestCase):
    def test_new_transfer_before_isolation_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'master.mov';path.write_bytes(b'old');digest=sha256(path)
            rename=handoff.os.rename
            def replace_then_rename(src,dst):
                replacement=Path(tmp)/'transfer';replacement.write_bytes(b'new')
                rename(replacement,src);rename(src,dst)
            with patch.object(handoff.os,'rename',side_effect=replace_then_rename):
                with self.assertRaisesRegex(ValueError,'preserved'):
                    handoff.release_verified_file(path,digest)
            self.assertEqual(path.read_bytes(),b'new')

    def test_new_transfer_after_isolation_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'master.mov';path.write_bytes(b'old');digest=sha256(path)
            rename=handoff.os.rename
            def rename_then_replace(src,dst):
                rename(src,dst);Path(src).write_bytes(b'new')
            with patch.object(handoff.os,'rename',side_effect=rename_then_replace):
                handoff.release_verified_file(path,digest)
            self.assertEqual(path.read_bytes(),b'new')

    def test_interrupted_isolation_is_recoverable_without_public_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'master.mov';path.write_bytes(b'old');digest=sha256(path)
            rename=handoff.os.rename
            def interrupt(src,dst):
                rename(src,dst);raise RuntimeError('simulated process crash')
            with patch.object(handoff.os,'rename',side_effect=interrupt),self.assertRaises(RuntimeError):
                handoff.release_verified_file(path,digest)
            self.assertFalse(path.exists())
            self.assertTrue(list(Path(tmp).glob('.*.release-*/artifact')))
            recovered=handoff.resume_verified_release(path,digest)
            self.assertTrue(recovered)
            self.assertFalse(list(Path(tmp).glob('.*.release-*/artifact')))


if __name__=='__main__':unittest.main()
