import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from storage_policy import minimum_free_gib
from queue_storage import phase_peaks,evaluate,GIB


class PolicyTests(unittest.TestCase):
    def test_explicit_jigong_profile_uses_5gib_without_changing_default(self):
        with patch.dict('os.environ',{'KWJD_PROJECT':'config/projects/jigong-s03.json'}):
            self.assertEqual(minimum_free_gib(),5)
        with patch.dict('os.environ',{'KWJD_PROJECT':'../outside.json'}),self.assertRaises((ValueError,FileNotFoundError)):
            minimum_free_gib()
    def test_runtime_reserve_is_validated(self):
        self.assertEqual(minimum_free_gib({'minimum_free_gib':5}),5)
        self.assertEqual(minimum_free_gib({'minimum_free_gib':15}),15)
        for invalid in [True,0,-1,4,math.nan,math.inf,'15']:
            with self.assertRaises(ValueError):minimum_free_gib({'minimum_free_gib':invalid})

    def test_split_volumes_do_not_add_free_space_or_disjoint_phases(self):
        split=phase_peaks(3650.44,48000,2,dict(cache='root',scratch='root',final='home'))
        self.assertAlmostEqual(split['root']/GIB,15.7988173,places=5)
        self.assertAlmostEqual(split['home']/GIB,9.9549077,places=5)
        same=phase_peaks(3650.44,48000,2,dict(cache='home',scratch='home',final='home'))
        self.assertAlmostEqual(same['home']/GIB,18.9098154,places=5)

    def test_admitted_queue_reservation_cannot_be_spent_by_production(self):
        peaks=phase_peaks(3650.44,48000,2,dict(cache='root',scratch='root',final='home'))
        self.assertTrue(evaluate(peaks,{'root':54*GIB,'home':34*GIB},15,{'home':8*GIB})['allowed'])
        self.assertFalse(evaluate(peaks,{'root':54*GIB,'home':32*GIB},15,{'home':8*GIB})['allowed'])
        self.assertFalse(evaluate(peaks,{'root':20*GIB,'home':100*GIB},15)['allowed'])


if __name__=='__main__':unittest.main()
