import sys
from pathlib import Path
import random
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import audit_music_versions as v

class VersionTests(unittest.TestCase):
    def test_vocal_instrumental_and_backing_are_separate(self):
        self.assertNotEqual(v.variant('下一站'),v.variant('下一站（伴奏）'))
        self.assertNotEqual(v.variant('下一站（和声伴奏）'),v.variant('下一站（伴奏）'))
        self.assertNotEqual(v.variant('Theme'),v.variant('Theme Live Version'))

    def test_normalization_keeps_different_day_night_names(self):
        self.assertEqual(v.english('清风 The Breeze'),v.english('The Breeze 清风'))
        self.assertNotEqual(v.english('Theme Day'),v.english('Theme Night'))

    def test_full_track_not_just_opening_must_match(self):
        rng=random.Random(2);a=[rng.getrandbits(32) for _ in range(120)]
        self.assertTrue(v.compare(a,a)['match'])
        b=a[:40]+[rng.getrandbits(32) for _ in range(80)]
        self.assertFalse(v.compare(a,b)['match'])
        self.assertFalse(v.compare([0]*120,[0]*120)['match'])

if __name__=='__main__':unittest.main()
