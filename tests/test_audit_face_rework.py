import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_face_rework import audit,digest


class AuditFaceReworkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name);(self.work/'segments').mkdir()
        self.source=self.work/'source.mkv';self.source.write_bytes(b'source')
        self.write(self.work/'full.manifest.json',dict(start_frame=0,frames=5,segment_frames=3,
            source=str(self.source),source_sha256=digest(self.source)))
        self.write(self.work/'plan.json',dict(fingerprint='f'*64))
        self.make_segment(0,0,3)

    def write(self,path,data):path.write_text(json.dumps(data))

    def make_segment(self,index,start,count):
        folder=self.work/'segments';p=folder/f'segment-{index:05d}.json'
        media=p.with_suffix('.mkv');media.write_bytes(str(index).encode())
        log=folder/f'{index:05d}.frames.jsonl'
        log.write_text(''.join(json.dumps(dict(frame=i,faces_detected=1,faces_restored=1))+'\n'
            for i in range(start,start+count)))
        self.write(p,dict(fingerprint='f'*64,source_start_frame=start,frames=count,
            processed_frames=count,failed_frames=0,protected_bottom_unchanged_frames=count,
            restored_frames=count,no_detection_frames=0,faces_restored=count,
            frame_log=str(log),frame_log_sha256=digest(log),
            validation=dict(frames=count,sha256=digest(media))))
        return p

    def test_partial_and_complete_never_claim_final_acceptance(self):
        result=audit(self.work);self.assertEqual(result['frames'],3)
        self.assertFalse(result['all_frames_present']);self.assertFalse(result['final_acceptance'])
        self.make_segment(1,3,2);result=audit(self.work,True)
        self.assertEqual(result['frames'],5);self.assertTrue(result['all_frames_present'])
        self.assertTrue(result['source_sha_freshly_checked']);self.assertFalse(result['final_acceptance'])

    def test_missing_first_or_middle_segment_rejected(self):
        self.make_segment(2,6,3)
        with self.assertRaises(ValueError):audit(self.work)
        (self.work/'segments/segment-00000.json').unlink()
        with self.assertRaises(ValueError):audit(self.work)

    def test_changed_media_or_log_rejected(self):
        for name in ['segment-00000.mkv','00000.frames.jsonl']:
            p=self.work/'segments'/name;old=p.read_bytes();p.write_bytes(b'changed')
            with self.assertRaises(ValueError):audit(self.work)
            p.write_bytes(old)

    def test_receipt_context_counters_and_decode_count_rejected(self):
        p=self.work/'segments/segment-00000.json';original=json.loads(p.read_text())
        for key,value in [('fingerprint','a'*64),('source_start_frame',1),('frames',2),
                ('processed_frames',2),('failed_frames',1),('protected_bottom_unchanged_frames',2),
                ('restored_frames',2),('faces_restored',2),('no_detection_frames',1),
                ('validation',dict(frames=2,sha256=digest(p.with_suffix('.mkv'))))]:
            d=copy.deepcopy(original);d[key]=value;self.write(p,d)
            with self.subTest(key=key),self.assertRaises(ValueError):audit(self.work)
        self.write(p,original)

    def test_duplicate_frame_rejected_even_with_updated_log_hash(self):
        p=self.work/'segments/segment-00000.json';d=json.loads(p.read_text());log=Path(d['frame_log'])
        records=[json.loads(x) for x in log.read_text().splitlines()];records[1]['frame']=0
        log.write_text(''.join(json.dumps(x)+'\n' for x in records));d['frame_log_sha256']=digest(log);self.write(p,d)
        with self.assertRaises(ValueError):audit(self.work)

    def test_optional_source_sha_check_rejects_changed_source(self):
        self.source.write_bytes(b'new source')
        self.assertFalse(audit(self.work)['source_sha_freshly_checked'])
        with self.assertRaises(ValueError):audit(self.work,True)


if __name__=='__main__':unittest.main()
