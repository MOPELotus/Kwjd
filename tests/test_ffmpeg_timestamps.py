import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ffmpeg_timestamps import showinfo_frames


class ShowinfoTimestampTests(unittest.TestCase):
    def test_late_episode_integer_ticks_override_rounded_display(self):
        rows=showinfo_frames('config in time_base: 1/16000, frame_rate: 25/1\n'
            'n: 0 pts:17811456 pts_time:1113.22\n'
            'n: 1 pts:17812096 pts_time:1113.26\n')
        self.assertEqual([r['seconds'] for r in rows],[1113.216,1113.256])
        self.assertEqual(rows[0]['pts'],17811456)
        self.assertEqual(rows[0]['time_base'],'1/16000')

    def test_negative_ticks_and_non_unit_numerator_remain_exact(self):
        self.assertEqual(showinfo_frames('config in time_base: 2/30000\n'
            'n: 0 pts: -1 pts_time:-0.000067\n')[0]['seconds'],-1/15000)

    def test_missing_or_changing_clock_and_noncontiguous_frames_fail(self):
        for log in ['n: 0 pts: 10 pts_time:1',
                    'config in time_base: 0/1\nn: 0 pts: 0 pts_time:0',
                    'config in time_base: 1/25\nconfig in time_base: 1/30\nn: 0 pts:0 pts_time:0',
                    'config in time_base: 1/25\nn: 0 pts:0 pts_time:0\nn: 0 pts:1 pts_time:.04',
                    'config in time_base: 1/25\nn: 1 pts:1 pts_time:.04']:
            with self.subTest(log=log),self.assertRaises(ValueError):showinfo_frames(log)


if __name__=='__main__':unittest.main()
