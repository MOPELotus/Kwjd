import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cache_after_master_handoff import admit
from receive_managed_finals import report_identity, quality_identity


class ManagedHandoffs(unittest.TestCase):
    def test_quality_requires_actual_four_part_evidence(self):
        receipt = dict(episode='S03E02', master_sha256='a'*64, sha256='b'*64, bytes=12)
        quality = dict(schema_version=1, episode='S03E02', master_sha256='a'*64,
                       final_sha256='b'*64, final_bytes=12, review_complete=True,
                       reviewed_by=dict(kind='codex', session_id='actual-session'))
        for name in ('visual', 'subtitles', 'synchronization', 'audio'):
            quality[name] = dict(checked=True, evidence=[dict(path='/post/proof', sha256='c'*64)])
        quality['audio'].update(source_pcm_equal=True, decoded_pcm_sha256='d'*64)
        quality['subtitles'].update(srt_sha256='e'*64, ass_sha256='f'*64)
        self.assertEqual(len(quality_identity(quality, receipt)), 4)
        quality['visual']['evidence'] = []
        with self.assertRaises(ValueError):
            quality_identity(quality, receipt)

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
