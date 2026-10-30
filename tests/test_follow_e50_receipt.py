from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from follow_e50_receipt import receipt_ready, cache_release_ready, EXPECTED
from follow_e50_receipt import remaining_upload_bytes
from follow_e50_receipt import validate_remaining_manifest
from follow_e50_receipt import adopt_native_submission
from follow_e50_receipt import assert_unstarted_native_submission


class ReceiveAdmission(unittest.TestCase):
    def test_generic_queued_submission_uses_calibrated_episode_style(self):
        job=dict(episode='S01E53',state='QUEUED',style='styles/template.ass')
        result=adopt_native_submission(job,'S01E53')
        self.assertEqual(result['style'],'styles/S01E53.ass')
        self.assertEqual(job['style'],'styles/template.ass')
        self.assertEqual(adopt_native_submission(result,'S01E53'),result)

    def test_custom_style_and_started_generic_job_are_preserved(self):
        for style,state in [('styles/custom.ass','QUEUED'),('styles/template.ass','ENHANCING'),('styles/template.ass','WAIT_DISK')]:
            with self.assertRaises(ValueError):
                adopt_native_submission(dict(episode='S01E53',state=state,style=style),'S01E53')

    def test_orphan_production_is_rejected_before_profile_preparation(self):
        job=dict(episode='S01E53',state='QUEUED',style='styles/template.ass')
        with tempfile.TemporaryDirectory() as directory:
            post=Path(directory);cache=post/'cache/S01E53';(cache/'native-style').mkdir(parents=True)
            assert_unstarted_native_submission(post,job,'S01E53')
            (cache/'S01E53.segments').mkdir()
            with self.assertRaises(ValueError):assert_unstarted_native_submission(post,job,'S01E53')
            (cache/'S01E53.segments').rmdir()
            (cache/'S01E53.source.ready.json').write_text('{}')
            with self.assertRaises(ValueError):assert_unstarted_native_submission(post,job,'S01E53')

    def test_finite_manifest_rejects_extra_episode_and_invalid_size(self):
        import copy
        valid=dict(authorized=True,order=[f'S01E{n}' for n in range(52,56)],
            masters={f'S01E{n}':{'bytes':100,'sha256':None} for n in range(52,56)})
        self.assertEqual(validate_remaining_manifest(valid),valid)
        extra=copy.deepcopy(valid);extra['masters']['S01E56']={'bytes':1}
        with self.assertRaises(ValueError):validate_remaining_manifest(extra)
        for invalid in (-1,0,True):
            bad=copy.deepcopy(valid);bad['masters']['S01E55']['bytes']=invalid
            with self.assertRaises(ValueError):validate_remaining_manifest(bad)
        bad=copy.deepcopy(valid);bad['authorized']=False
        with self.assertRaises(ValueError):validate_remaining_manifest(bad)

    def test_remaining_uploads_exclude_current_and_released_predecessors(self):
        with tempfile.TemporaryDirectory() as directory:
            post=Path(directory);(post/'incoming').mkdir()
            masters={f'S01E{n}':{'bytes':100} for n in range(52,56)}
            self.assertEqual(remaining_upload_bytes(post,'S01E53',masters),200)
            self.assertEqual(remaining_upload_bytes(post,'S01E55',masters),0)

    def test_allocated_future_upload_is_not_reserved_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            post=Path(directory);incoming=post/'incoming';incoming.mkdir()
            masters={'S01E53':{'bytes':100},'S01E54':{'bytes':100}}
            (incoming/'S01E53.clean-master.mov').write_bytes(b'x'*100)
            (incoming/'S01E54.clean-master.mov.uploading').write_bytes(b'x'*40)
            self.assertEqual(remaining_upload_bytes(post,'S01E52',masters),60)

    def test_e51_completion_notice_requires_actual_matching_release(self):
        record=dict(episode='S01E51',master_sha256='2eb923278d3dd7526ed7466296ad437fd19411acaf303508ee8013d1239822f1',
                    state='LOCAL_ANCHORED_CACHE_CLEANED',master_retained=True)
        self.assertTrue(cache_release_ready(record))
        record['state']='LOCAL_ANCHORED_CACHE_PLAN'
        self.assertFalse(cache_release_ready(record))
        record.update(state='LOCAL_ANCHORED_CACHE_CLEANED',master_sha256='other')
        self.assertFalse(cache_release_ready(record))

    def test_receipt_and_atomic_publication_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            master=Path(directory)/'S01E50.clean-master.mov'
            receipt=dict(episode='S01E50',sha256=EXPECTED,verified=True,local_master_deleted=True)
            self.assertFalse(receipt_ready(master,receipt))
            master.write_bytes(b'published')
            self.assertTrue(receipt_ready(master,receipt))
            partial=master.with_name(master.name+'.uploading');partial.write_bytes(b'in flight')
            self.assertFalse(receipt_ready(master,receipt))

    def test_unverified_local_release_or_another_revision_does_not_start(self):
        with tempfile.TemporaryDirectory() as directory:
            master=Path(directory)/'S01E50.clean-master.mov';master.write_bytes(b'published')
            receipt=dict(episode='S01E50',sha256=EXPECTED,verified=True,local_master_deleted=False)
            self.assertFalse(receipt_ready(master,receipt))
            receipt.update(local_master_deleted=True,sha256='0'*64)
            self.assertFalse(receipt_ready(master,receipt))


if __name__=='__main__':unittest.main()
