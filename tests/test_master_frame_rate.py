import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_master import frame_rate_check


class MasterFrameRateTests(unittest.TestCase):
    def test_default_cannot_silently_accept_thirty(self):
        with self.assertRaises(ValueError):
            frame_rate_check({'r_frame_rate':'30/1','avg_frame_rate':'30/1','nb_frames':'60'})

    def test_twenty_five_keeps_default_gate(self):
        self.assertTrue(frame_rate_check({'r_frame_rate':'25/1','avg_frame_rate':'25/1'})['default_25fps_gate_passed'])

    def test_thirty_requires_current_hash_bound_clock(self):
        video={'r_frame_rate':'30/1','avg_frame_rate':'30/1','nb_frames':'60'}
        with self.assertRaises(ValueError):frame_rate_check(video,'30/1')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'proof.json'
            proof=dict(master_sha256='a'*64,packet_clock_verified=True,input_rate='30',
                       input_packets=60,expected_output_frames=120,output_rate=60)
            path.write_text(json.dumps(proof))
            self.assertFalse(frame_rate_check(video,'30/1',path,'a'*64)['default_25fps_gate_passed'])
            for key,value in [('master_sha256','b'*64),('packet_clock_verified',False),
                              ('input_packets',59),('expected_output_frames',144)]:
                changed=dict(proof);changed[key]=value;path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):frame_rate_check(video,'30/1',path,'a'*64)


if __name__=='__main__':unittest.main()
