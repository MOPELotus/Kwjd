import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from concat_clock import write_concat,verify_packet_clock,normalize_copy_timestamps,subtitle_frame_origin


class ConcatClockTests(unittest.TestCase):
    def test_subtitle_offset_uses_integer_ticks_for_fractional_cfr30_segment_starts(self):
        self.assertEqual(subtitle_frame_origin(0,16250,30),32500)
        self.assertEqual(subtitle_frame_origin(45,5,25),2712)
        with self.assertRaises(ValueError):subtitle_frame_origin(0,1,25)

    def test_cfr30_segments_use_output_frames_not_rounded_container_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'concat.txt'
            write_concat(p,[Path(directory)/'one.mkv',Path(directory)/'two.mkv'],[500,154])
            self.assertIn('duration 8.333333333333333333\n',p.read_text())
            self.assertIn('duration 2.566666666666666667\n',p.read_text())
            write_concat(p,[Path(directory)/'legacy.mkv'],[600])
            self.assertIn('duration 10.000000000000000000\n',p.read_text())

    def test_rejects_unknown_counts_and_path_line_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'concat.txt'
            for counts in [[],[0],[True]]:
                with self.assertRaises(ValueError):write_concat(p,['one.mkv'],counts)
            with self.assertRaises(ValueError):write_concat(p,['one\nfile.mkv'],[500])

    def test_packet_clock_keeps_quantization_tolerance_but_rejects_drift(self):
        data={'streams':[{'time_base':'1/1000'}],'packets':[{'pts':v} for v in [0,17,33]]}
        with patch('concat_clock.subprocess.check_output',return_value=json.dumps(data)):
            self.assertLess(verify_packet_clock('test.mkv',3)['max_video_grid_error'],.0011)
        data['packets'][-1]['pts']=36
        with patch('concat_clock.subprocess.check_output',return_value=json.dumps(data)):
            with self.assertRaises(ValueError):verify_packet_clock('test.mkv',3)

    def test_timestamp_repair_preserves_b_frame_reordering_without_encoding(self):
        data={'streams':[{'time_base':'1/1000','has_b_frames':2}],
              'packets':[{'pts':v} for v in [0,67,33,17,50,100]]}
        with patch('concat_clock.subprocess.check_output',return_value=json.dumps(data)), \
             patch('concat_clock.subprocess.run') as run, \
             patch('concat_clock.verify_packet_clock',return_value={'frames':6}):
            result=normalize_copy_timestamps('source.mkv','out.mkv',6)
        self.assertEqual(result['corrected_packet_ranges'],[(5,6,1)])
        command=run.call_args.args[0]
        self.assertEqual(command[command.index('-c:v')+1],'copy')
        self.assertIn("dts='(N-2)/(60*TB)'",command[command.index('-bsf:v')+1])
        data['packets'][-1]['pts']=1000
        with patch('concat_clock.subprocess.check_output',return_value=json.dumps(data)):
            with self.assertRaises(ValueError):normalize_copy_timestamps('source.mkv','out.mkv',6)


if __name__=='__main__':unittest.main()
