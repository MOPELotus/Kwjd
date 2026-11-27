import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cache_after_master_handoff import admit, prepare
import json
import tempfile
from unittest.mock import patch
from receive_managed_finals import report_identity, quality_identity
from types import SimpleNamespace
import receive_managed_finals as finals


class ManagedHandoffs(unittest.TestCase):
    def test_unreviewed_cloud_final_never_starts_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / '.kwjd/uploads/S03E04.json'
            upload.parent.mkdir(parents=True)
            upload.write_text(json.dumps(dict(verified=True, sha256='a'*64)))
            report = dict(episode='S03E04', sha256='b'*64, master_sha256='a'*64,
                          final_decode_checked=True, final_pcm_equivalent=True,
                          decoded_pcm_sha256='c'*64, bytes=12)
            answers = [SimpleNamespace(returncode=0, stdout=json.dumps(report)),
                       SimpleNamespace(returncode=1, stdout='')]
            with patch('jigong_master_restart.enforce'), patch.object(finals, 'remote', side_effect=answers), patch.object(finals.subprocess, 'run') as download:
                self.assertEqual(finals.receive(root, dict(remote_root='/post'), 'S03E04'), 'WAIT_ACTUAL_QC_PUBLICATION')
                download.assert_not_called()

    def test_wrong_revision_quality_never_starts_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / '.kwjd/uploads/S03E04.json'
            upload.parent.mkdir(parents=True)
            upload.write_text(json.dumps(dict(verified=True, sha256='a'*64)))
            report = dict(episode='S03E04', sha256='b'*64, master_sha256='a'*64,
                          final_decode_checked=True, final_pcm_equivalent=True,
                          decoded_pcm_sha256='c'*64, bytes=12)
            answers = [SimpleNamespace(returncode=0, stdout=json.dumps(report)),
                       SimpleNamespace(returncode=0, stdout=json.dumps(dict(final_sha256='d'*64)))]
            with patch('jigong_master_restart.enforce'), patch.object(finals, 'remote', side_effect=answers), patch.object(finals.subprocess, 'run') as download:
                with self.assertRaisesRegex(ValueError, 'not bound'):
                    finals.receive(root, dict(remote_root='/post'), 'S03E04')
                download.assert_not_called()

    def test_precleaned_cache_does_not_snapshot_a_later_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            validation=root/'.kwjd/master-validation/S03E04.json'
            validation.parent.mkdir(parents=True)
            validation.write_text(json.dumps(dict(state='TECHNICALLY_VALIDATED',sha256='a'*64,
                                                  path='master',bytes=12)))
            prior=root/'.kwjd/cache-cleanup/S03E04.local-anchor.json'
            prior.parent.mkdir(parents=True)
            prior.write_text(json.dumps(dict(state='LOCAL_ANCHORED_CACHE_CLEANED',master_sha256='a'*64)))
            with patch('cache_after_master_handoff.snapshot', side_effect=AssertionError('new cache must not be sampled')):
                result=prepare(root,'S03E04',root/'receipt.json','host','/var/tmp/receive-root')
            self.assertTrue(result['cache_precleaned'])
            self.assertIsNone(result['cache_snapshot'])
            handoff=dict(episode='S03E04',sha256='a'*64,bytes=12,transfer_verified=True,
                         remote_sampled_decode_verified=True,path='/var/tmp/receive-root/post/incoming/S03E04.clean-master.mov')
            self.assertEqual(admit(result,handoff)['remote_path'],handoff['path'])

    def test_quality_requires_actual_four_part_evidence(self):
        receipt = dict(episode='S03E02', master_sha256='a'*64, sha256='b'*64, bytes=12)
        quality = dict(schema_version=1, episode='S03E02', master_sha256='a'*64,
                       final_sha256='b'*64, final_bytes=12, review_complete=True,
                       reviewed_by=dict(kind='codex', session_id='01a203ac-7e99-7153-a57a-e82c19cd6fd1'))
        for name in ('visual', 'subtitles', 'synchronization', 'audio'):
            quality[name] = dict(checked=True, evidence=[dict(path='/post/proof', sha256='c'*64)])
        quality['audio'].update(source_pcm_equal=True, decoded_pcm_sha256='d'*64)
        quality['subtitles'].update(srt_sha256='e'*64, ass_sha256='f'*64)
        self.assertEqual(len(quality_identity(quality, receipt)), 4)
        quality['visual']['evidence'] = []
        with self.assertRaises(ValueError):
            quality_identity(quality, receipt)

    def test_download_waits_for_actual_qc_and_matching_publication(self):
        import receive_managed_finals as receiver
        from types import SimpleNamespace
        config=dict(remote_root='/home/lotus/qingqingwu')
        report=dict(master_sha256='a'*64,sha256='b'*64,bytes=12)
        with patch.object(receiver,'remote',return_value=SimpleNamespace(returncode=1,stdout='')):
            self.assertFalse(receiver.published_quality(config,'S03E04',report))
        quality=dict(quality_sha256='c'*64)
        publication=dict(server_quality_checked=True,episode='S03E04',**report,
                         quality_evidence_sha256='c'*64)
        def replies():
            return [SimpleNamespace(returncode=0,stdout=json.dumps(x)) for x in (quality,publication)]
        with patch.object(receiver,'quality_identity'),patch.object(receiver,'remote',side_effect=replies()):
            self.assertTrue(receiver.published_quality(config,'S03E04',report))
        publication['sha256']='d'*64
        with patch.object(receiver,'quality_identity'),patch.object(receiver,'remote',side_effect=replies()):
            with self.assertRaises(ValueError):receiver.published_quality(config,'S03E04',report)

    def test_cache_needs_completed_matching_remote_decode(self):
        request = dict(episode='S03E02', master_sha256='a'*64, bytes=12,
                       local_path='master', ssh_alias='host', cache_path='cache',
                       cache_snapshot={'entries': []}, receipt='receipt')
        handoff = dict(episode='S03E02', sha256='a'*64, bytes=12,
                       transfer_verified=True, remote_sampled_decode_verified=True,
                       path='/home/lotus/qingqingwu/post/incoming/S03E02.clean-master.mov')
        self.assertTrue(admit(request, handoff)['verified'])
        handoff['remote_sampled_decode_verified'] = False
        with self.assertRaises(ValueError):
            admit(request, handoff)

    def test_final_needs_lossless_mix_proof_and_current_master(self):
        report = dict(episode='S03E02', sha256='b'*64, master_sha256='a'*64,
                      final_decode_checked=True, final_pcm_equivalent=True,
                      decoded_pcm_sha256='c'*64, bytes=12)
        upload = dict(verified=True, sha256='a'*64)
        self.assertEqual(report_identity(report, 'S03E02', upload), 'b'*64)
        upload['sha256'] = 'd'*64
        with self.assertRaises(ValueError):
            report_identity(report, 'S03E02', upload)


if __name__ == '__main__':
    unittest.main()
