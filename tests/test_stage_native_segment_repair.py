import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from stage_native_segment_repair import replacement_bounds,unchanged_packets
class RepairProofTests(unittest.TestCase):
 def test_bad_bounds_refused(self):
  for first,count,total in [(755,250,206031),(750,125,206031),(750,250,2400)]:
   with self.assertRaises(ValueError):replacement_bounds(first,count,total)
  self.assertEqual(replacement_bounds(750,250,206031),(1800,600))
  self.assertEqual(replacement_bounds(0,250,206031),(0,600))
 def test_every_nonpatch_packet_must_stay_exact(self):
  original=['a','b','c','d','e'];patch=['x','y'];candidate=['a','x','y','d','e']
  self.assertTrue(unchanged_packets(original,patch,candidate,1,2)['unchanged_outside_replacement'])
  for bad in [['A','x','y','d','e'],['a','x','y','D','e'],['a','x','c','d','e'],['a','x','y','d']]:
   with self.assertRaises(ValueError):unchanged_packets(original,patch,bad,1,2)
if __name__=='__main__':unittest.main()
