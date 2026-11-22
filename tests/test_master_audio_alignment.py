import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from master_audio_alignment import lag


class AudioAlignmentTests(unittest.TestCase):
    def test_added_music_and_gain_do_not_change_delay_sign(self):
        random=np.random.default_rng(27)
        source=random.normal(size=16000)
        delayed=np.r_[np.zeros(160),source[:-160]]*3+random.normal(size=16000)*.1
        result=lag(source,delayed)
        self.assertEqual(result['master_delay_ms'],20)
        self.assertGreater(result['speech_band_correlation'],.97)

    def test_early_mix_is_negative(self):
        source=np.random.default_rng(9).normal(size=16000)
        mixed=np.r_[source[80:],np.zeros(80)]
        self.assertEqual(lag(source,mixed)['master_delay_ms'],-10)


if __name__=='__main__':unittest.main()
