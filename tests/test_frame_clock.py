from fractions import Fraction
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from frame_clock import frame_position,output_count,input_rate


class FrameClockTests(unittest.TestCase):
    def test_existing_25fps_mapping_stays_exact(self):
        for index in range(600):
            left,alpha=frame_position(index,25)
            self.assertEqual(left+alpha,Fraction(index*25,60))
        self.assertEqual(output_count(250,25),600)

    def test_30fps_maps_to_original_then_halfway_frame(self):
        for index in range(1000):
            left,alpha=frame_position(index,30)
            self.assertEqual(left,index//2)
            self.assertEqual(alpha,Fraction(index%2,2))
        self.assertEqual(output_count(111077,30),222154)

    def test_approved_chunk_boundaries_do_not_accumulate_rounding(self):
        for rate in (25,30):
            for count in (250,1000,92510):
                self.assertEqual(output_count(count,rate),
                                 sum(output_count(min(250,count-start),rate) for start in range(0,count,250)))

    def test_other_rates_and_invalid_indexes_fail(self):
        for rate in (24,'30000/1001',0):
            with self.assertRaises((ValueError,ZeroDivisionError)):input_rate(rate)
        for value in (-1,True,1.5):
            with self.assertRaises(ValueError):frame_position(value,30)


if __name__=='__main__':unittest.main()
