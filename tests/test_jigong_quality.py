import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jigong_quality as m

class QualityTests(unittest.TestCase):
    def test_restarted_first_chapter_uses_new25_receipt_instead_of_historical_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'.kwjd').mkdir();folder=root/'post/handoffs';folder.mkdir(parents=True)
            (root/'.kwjd/jigong-native25-restart.json').write_text('{"active":true}')
            target=folder/'S03E02.master-received.json'
            receipt=dict(episode='S03E02',transfer_verified=True,sha256='e'*64,bytes=10,master_frame_rate='25/1')
            target.write_text(json.dumps(receipt))
            self.assertEqual(m.master_identity(root,'S03E02'),'e'*64)
            target.write_text(json.dumps({**receipt,'sha256':m.MASTER}))
            with self.assertRaisesRegex(ValueError,'Superseded'):m.master_identity(root,'S03E02')
            target.write_text(json.dumps({**receipt,'master_frame_rate':'30/1'}))
            with self.assertRaisesRegex(ValueError,'native25'):m.master_identity(root,'S03E02')

    def test_explicit_downstream_media_hold_prevents_receipt_cleanup(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'cleanup') as clean:
            root=Path(temp);folder=root/'post/handoffs';folder.mkdir(parents=True)
            hold=folder/'S03E02.cleanup-hold.json'
            hold.write_text(json.dumps(dict(active=True,episode='S03E02',master_sha256=m.MASTER,reason='First intro local source retention pending')))
            self.assertEqual(m.delivery_step(root)['stage'],'WAIT_MEDIA_DEPENDENCY_HOLD')
            clean.assert_not_called()

    def test_each_following_chapter_requires_its_own_real_master_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'post/handoffs';folder.mkdir(parents=True)
            path=folder/'S03E03.master-received.json'
            with self.assertRaises(FileNotFoundError):m.master_identity(root,'S03E03')
            path.write_text(json.dumps(dict(episode='S03E03',transfer_verified=True,sha256='e'*64,bytes=10)))
            self.assertEqual(m.master_identity(root,'S03E03'),'e'*64)
            path.write_text(json.dumps(dict(episode='S03E04',transfer_verified=True,sha256='e'*64,bytes=10)))
            with self.assertRaises(ValueError):m.master_identity(root,'S03E03')
            with self.assertRaises(ValueError):m.master_identity(root,'S03E15')

    def test_authorized_external_reviewer_is_scoped_and_has_real_identity(self):
        r=dict(kind='external_omp_responses',session_id='01a203ac-7e99-7153-a57a-e82c19cd6fd1',
               provider='jigong-relay',endpoint='https://api.zhehentiaohe.cn/v1/responses',
               model='gpt-6.1-sol',reasoning_effort='medium',response_ids=['resp_actual'])
        self.assertTrue(m.valid_reviewer(r,'S03E04'))
        self.assertTrue(m.valid_reviewer(r,'S03E14'))
        for ep in ['S03E02','S03E03','S03E15','S01E04']:
            self.assertFalse(m.valid_reviewer(r,ep))
        for key,value in [('response_ids',[]),('provider','other'),('model','other'),('session_id','fake')]:
            self.assertFalse(m.valid_reviewer({**r,key:value},'S03E04'))

    def fixture(self,root):
        post=root/'post';(post/'reports').mkdir(parents=True);(post/'receipts').mkdir()
        evidence=post/'evidence.json';evidence.write_text('actual review fixture')
        digest='a'*64
        report=dict(master_sha256=m.MASTER,sha256=digest,bytes=10,final_decode_checked=True,final_pcm_equivalent=True,decoded_pcm_sha256='b'*64,srt_sha256='c'*64,fused_subtitle_binding=dict(ass_sha256='d'*64))
        (post/'reports'/f'{m.EPISODE}.json').write_text(json.dumps(report))
        quality=dict(schema_version=1,episode=m.EPISODE,master_sha256=m.MASTER,final_sha256=digest,final_bytes=10,review_complete=True,reviewed_by=dict(kind='codex',session_id='01a203ac-7e99-7153-a57a-e82c19cd6fd1'),accepted_limitations=['bounded actual visual samples'])
        for name in ['visual','subtitles','synchronization','audio']:
            quality[name]=dict(checked=True,scope='actual fixture review',issues=[],evidence=[dict(path=str(evidence),sha256=m.sha256(evidence))])
        quality['subtitles'].update(srt_sha256='c'*64,ass_sha256='d'*64)
        quality['audio'].update(source_pcm_equal=True,decoded_pcm_sha256='b'*64)
        path=post/'reports'/f'{m.EPISODE}.quality.json';path.write_text(json.dumps(quality))
        return path,quality,evidence

    def test_complete_quality_binds_all_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,data,evidence=self.fixture(root)
            self.assertEqual(m.verify_quality(root)['final_bytes'],10)
            evidence.write_text('changed')
            with self.assertRaises(ValueError):m.verify_quality(root)

    def test_unchecked_section_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,data,evidence=self.fixture(root);data['audio']['checked']=False;path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):m.verify_quality(root)

    def test_no_receipt_waits_without_cleanup(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'cleanup') as clean:
            self.assertEqual(m.delivery_step(Path(temp))['stage'],'WAIT_DELIVERY');clean.assert_not_called()

    def test_windows_receipt_requires_quality_digest(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(m,'cleanup') as clean:
            root=Path(temp);path,data,evidence=self.fixture(root)
            receipt=dict(schema_version=1,episode=m.EPISODE,received=True,technical_checked=True,quality_checked=True,remote_final_name=f'{m.EPISODE}.final-1080p60.mkv',sha256=data['final_sha256'],master_sha256=m.MASTER,bytes=10,quality_evidence_sha256='0'*64)
            target=root/'post/receipts'/f'{m.EPISODE}.json';target.write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):m.delivery_step(root)
            clean.assert_not_called()
            receipt['quality_evidence_sha256']=m.sha256(path);target.write_text(json.dumps(receipt))
            clean.return_value=dict(state='CLEANED')
            self.assertEqual(m.delivery_step(root)['stage'],'WAIT_WINDOWS_CLEANUP')
            local=dict(episode=m.EPISODE,role='windows',state='CLEANED',final_sha256=receipt['sha256'])
            (root/'post/receipts'/f'{m.EPISODE}.windows-cleanup.json').write_text(json.dumps(local))
            self.assertEqual(m.delivery_step(root)['stage'],'DELIVERED_AND_GENERATED_ARTIFACTS_CLEANED')

if __name__=='__main__':unittest.main()
