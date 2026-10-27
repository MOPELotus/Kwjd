import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import post_monitor as monitor


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name);self.master='a'*64;self.fp='b'*64
        self.job={'episode':'S01E49','master_sha256':self.master,'state':'ENHANCING'}
        self.production={'master_sha256':self.master,'backend_fingerprint':self.fp}
        self.identity={'episode':'S01E49','master_sha256':self.master,'backend_fingerprint':self.fp,
                       'input_frames':600,'output_frames':1440,'segment_source_frames':250}
        self.directory=self.post/'cache/S01E49/S01E49.segments'/(self.master[:16]+'-'+self.fp[:16])
        self.directory.mkdir(parents=True)
        (self.directory/'context.json').write_text(json.dumps(self.identity))

    def receipt(self,slot,**changes):
        index=slot
        start=index*250;selected=min(250,600-start)
        record={**self.identity,'index':index,'source_start':start,'source_frames':selected,
                'frames':(selected*12+4)//5,'sha256':'c'*64,**changes}
        (self.directory/f'{index:06d}.ready.json').write_text(json.dumps(record))
        (self.directory/f'{index:06d}.mkv').write_bytes(b'present media; deliberately not rehashed')

    def progress(self):
        return monitor.segment_progress(self.post,self.job,self.production)

    def test_bound_receipts_count_exact_frames_without_media_hashing(self):
        self.receipt(0);self.receipt(2)
        result=self.progress()
        self.assertEqual(result['status'],'known');self.assertEqual(result['verified_segments'],2)
        self.assertEqual(result['source_frames_complete'],350);self.assertEqual(result['source_frames_total'],600)
        self.assertEqual(result['percent'],58.333)
        self.assertEqual(result['verification'],'receipt-reported progress; media hash not re-read')
        self.assertTrue(result['not_a_completion_claim'])

    def test_stale_backend_and_other_master_cannot_report_progress(self):
        self.receipt(0)
        self.production['backend_fingerprint']='d'*64
        self.assertEqual(self.progress()['status'],'unknown')
        self.production['master_sha256']='e'*64
        self.assertIsNone(self.progress()['source_frames_complete'])

    def test_each_receipt_must_bind_context_index_and_frame_clock(self):
        for change in ({'master_sha256':'d'*64},{'backend_fingerprint':'d'*64},
                       {'source_start':1},{'source_frames':249},{'frames':599},
                       {'output_frames':1439},{'index':1},{'index':False}):
            with self.subTest(change=change):
                self.receipt(0,**change)
                result=self.progress();self.assertEqual(result['status'],'unknown')
                self.assertIsNone(result['source_frames_complete'])

    def test_missing_empty_or_symlink_media_is_unknown(self):
        self.receipt(0);media=self.directory/'000000.mkv';media.unlink()
        self.assertEqual(self.progress()['status'],'unknown')
        media.touch();self.assertEqual(self.progress()['status'],'unknown')
        media.unlink();media.symlink_to(self.directory/'context.json')
        self.assertEqual(self.progress()['status'],'unknown')

    def test_completed_receipt_survives_normal_segment_release(self):
        (self.directory/'completed.json').write_text(json.dumps({**self.identity,'sha256':'c'*64}))
        result=self.progress()
        self.assertEqual(result['status'],'known');self.assertEqual(result['percent'],100)
        self.assertEqual(result['verified_segments'],3);self.assertEqual(result['source_frames_complete'],600)
        # A similarly named, but differently bound completed receipt is not trusted.
        (self.directory/'completed.json').write_text(json.dumps({**self.identity,'sha256':'c'*64,'backend_fingerprint':'d'*64}))
        self.assertEqual(self.progress()['status'],'unknown')

    def test_bad_json_and_context_disappearance_are_unknown(self):
        self.receipt(0);receipt=self.directory/'000000.ready.json'
        receipt.write_text('{partial JSON')
        self.assertEqual(self.progress()['status'],'unknown')
        receipt.unlink();(self.directory/'context.json').unlink()
        self.assertEqual(self.progress()['status'],'unknown')

    def test_snapshot_keeps_other_telemetry_when_job_disappears(self):
        jobs=self.post/'jobs';jobs.mkdir();job=jobs/'S01E49.json';job.write_text('{}')
        (self.post/'final').mkdir();original=Path.read_text
        def disappear(path,*args,**kwargs):
            if path==job:raise FileNotFoundError('job removed during scan')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',new=disappear), \
             patch.object(monitor.subprocess,'check_output',return_value='1, Tesla T10\n'):
            result=monitor.snapshot(self.post)
        self.assertEqual(result['jobs'][0]['state'],'unknown')
        self.assertEqual(result['gpu_csv_rows'],['1, Tesla T10'])


if __name__=='__main__':unittest.main()
