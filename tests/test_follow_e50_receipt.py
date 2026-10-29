from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from follow_e50_receipt import receipt_ready, cache_release_ready, EXPECTED


class ReceiveAdmission(unittest.TestCase):
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
