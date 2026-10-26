import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import segmented_backend as backend
from enhance_stage import GIB


class SegmentTests(unittest.TestCase):
    def test_resume_reuses_completed_ai_segments_and_accepts_completed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';master.write_bytes(b'original')
            output=root/'S01E49.enhanced.partial.mkv'
            cfg={'fingerprint':'model','segment_source_frames':25,'scratch':str(root),'restore':False}
            calls=[];fail=[True]
            def encode(src,dest,ep,cfg,**kw):
                start=kw['source_start'];calls.append(start)
                if start==25 and fail[0]:raise RuntimeError('simulated process crash')
                Path(dest).write_bytes(('AI segment '+str(start)).encode())
            def concatenate(args,**kw):Path(args[-1]).write_bytes(b'accepted concatenation')
            with patch.object(backend.engine,'source_info',return_value={'format':{'duration':'2.4'}}), \
                 patch.object(backend.engine,'source_packet_count',return_value=60), \
                 patch.object(backend.engine,'run',side_effect=encode),patch.object(backend,'validate_segment'), \
                 patch.object(backend,'check_space'),patch.object(backend.subprocess,'run',side_effect=concatenate):
                with self.assertRaisesRegex(RuntimeError,'simulated'):backend.run(master,output,'S01E49',cfg)
                fail[0]=False;backend.run(master,output,'S01E49',cfg)
                self.assertEqual(calls,[0,25,25,50])
                backend.run(master,output,'S01E49',cfg)
                self.assertEqual(calls,[0,25,25,50])
            self.assertEqual(master.read_bytes(),b'original')
            self.assertEqual(output.read_bytes(),b'accepted concatenation')

    def test_unowned_partial_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';master.write_bytes(b'original');output=root/'out'
            cfg={'fingerprint':'model','segment_source_frames':25,'scratch':str(root),'restore':False}
            _,_,folder=backend.context(master,output,'S01E49',cfg);folder.mkdir(parents=True)
            partial=folder/'000000.partial.mkv';partial.write_bytes(b'orphan')
            with patch.object(backend.engine,'source_info',return_value={'format':{'duration':'2.4'}}), \
                 patch.object(backend.engine,'source_packet_count',return_value=60),patch.object(backend,'check_space'), \
                 self.assertRaisesRegex(ValueError,'Orphan'):
                backend.run(master,output,'S01E49',cfg)
            self.assertEqual(partial.read_bytes(),b'orphan')

    def test_concat_peak_budgets_both_copies_and_scratch(self):
        with patch.object(backend.engine,'source_info',return_value={'format':{'duration':'3517.56'}}):
            plan=backend.plan('master',{'fingerprint':'model','scratch':'/tmp'})
        self.assertGreater(plan['additional_peak_bytes']/GIB,44)
        self.assertLess(plan['additional_peak_bytes']/GIB,45)


if __name__=='__main__':unittest.main()
