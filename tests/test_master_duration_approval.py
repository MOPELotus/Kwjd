import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_master import duration_check


class DurationApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'S01E50.clean-master.mov'
        self.approval=self.root/'S01E50.json';self.digest='a'*64
        self.data=dict(schema_version=1,approved=True,approved_by='human',episode='S01E50',
            scope='extra_tail_background_only',master_sha256=self.digest,
            expected_duration_seconds=3649.933,actual_duration_seconds=3650.440,
            reason='Reviewed extra scenic background after performer exit',
            human_quote='尾部是一点点背景 属合理情况')

    def check(self):
        self.approval.write_text(json.dumps(self.data))
        return duration_check(self.path,3650.440,3649.933,self.approval,self.digest)

    def test_default_tolerance_remains_and_excess_without_approval_fails(self):
        self.assertTrue(duration_check(self.path,101,100.5)['default_duration_gate_passed'])
        with self.assertRaises(ValueError):duration_check(self.path,3650.440,3649.933)
        with self.assertRaises(ValueError):duration_check(self.path,99.499,100)

    def test_explicit_approval_keeps_default_failure_in_audit(self):
        result=self.check()
        self.assertFalse(result['default_duration_gate_passed'])
        self.assertEqual(result['exception']['approval'],self.data)
        self.assertEqual(len(result['exception']['approval_sha256']),64)
        self.assertAlmostEqual(result['delta_seconds'],0.507)

    def test_stale_or_unapproved_records_never_waive_duration(self):
        original=copy.deepcopy(self.data)
        for key,value in [('master_sha256','b'*64),('episode','S01E51'),
                ('approved',False),('approved',1),('approved_by','model'),
                ('schema_version',True),('scope','all_duration_errors'),
                ('expected_duration_seconds',3649.9),('actual_duration_seconds',3650.4),
                ('expected_duration_seconds',True),('actual_duration_seconds',float('nan')),
                ('human_quote',''),('reason','')]:
            with self.subTest(key=key,value=value):
                self.data=copy.deepcopy(original);self.data[key]=value
                with self.assertRaises(ValueError):self.check()

    def test_wrong_filename_or_missing_verified_hash_rejected(self):
        self.check()
        for path,digest in [(self.root/'other.mov',self.digest),(self.path,None),(self.path,'invalid')]:
            with self.assertRaises(ValueError):duration_check(path,3650.440,3649.933,self.approval,digest)

    def test_tail_approval_cannot_allow_shortened_master(self):
        self.check()
        with self.assertRaises(ValueError):duration_check(self.path,3649,3649.933,self.approval,self.digest)

    def test_invalid_measurements_rejected(self):
        for value in [True,float('nan'),float('inf'),0,-1]:
            with self.subTest(value=value),self.assertRaises(ValueError):duration_check(self.path,value,3649.933)

    def test_bom_and_changed_approval_are_hash_bound(self):
        first=self.check()
        self.approval.write_text(json.dumps(self.data),encoding='utf-8-sig')
        second=duration_check(self.path,3650.440,3649.933,self.approval,self.digest)
        self.assertNotEqual(first['exception']['approval_sha256'],second['exception']['approval_sha256'])


if __name__=='__main__':unittest.main()
