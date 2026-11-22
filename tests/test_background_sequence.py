import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from split_background_sequence import allocate


class SequenceAllocationTests(unittest.TestCase):
    def test_crossing_stays_in_same_chapter_and_preserves_order(self):
        plan=allocate([4,4,4],[6,10])
        self.assertEqual(plan, [
            [{'source_index':0,'start_seconds':0,'target_seconds':4}],
            [{'source_index':0,'start_seconds':4,'target_seconds':2},
             {'source_index':1,'start_seconds':0,'target_seconds':2}],
            [{'source_index':1,'start_seconds':2,'target_seconds':4}]])

    def test_exact_boundary_does_not_make_empty_part(self):
        self.assertEqual(allocate([6,4],[6,4])[1],
                         [{'source_index':1,'start_seconds':0,'target_seconds':4}])

    def test_insufficient_sources_reject_before_writing(self):
        with self.assertRaises(ValueError):
            allocate([11],[5,5])


if __name__=='__main__':
    unittest.main()
