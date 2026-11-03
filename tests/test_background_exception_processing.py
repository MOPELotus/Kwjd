import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('exception_processor',Path(__file__).resolve().parents[1]/'scripts/process_background_exceptions.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ExceptionProcessingTests(unittest.TestCase):
    def test_vfr_packets_do_not_require_60fps(self):
        self.assertEqual(module.timestamp_check(['0,-20','40,0','100,40','160,100']),4)

    def test_duplicate_pts_and_reordered_dts_are_rejected(self):
        for packets in [['0,0','0,40'],['0,0','40,0'],['N/A,0']]:
            with self.assertRaises(ValueError):module.timestamp_check(packets)

    def test_crop_keeps_geometry_even(self):
        with self.assertRaises(ValueError):module.video_filter(dict(crop=[0,0,1919,1000],output_size=[1920,1080]))

    def test_non_square_sar_corrected_without_stretch(self):
        f=module.video_filter(dict(crop=[0,0,1920,1080],output_size=[1920,1080],source_sar='513:497'))
        self.assertIn('scale=1980:1080,setsar=1',f)
        self.assertNotIn('fps=',f)


if __name__=='__main__':unittest.main()
