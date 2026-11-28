import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from native_face_pipeline import reviewed_cut_frames
class ReviewedCuts(unittest.TestCase):
 def evidence(self):return dict(episode='S03E06',master_sha256='a'*64,native_fps=25,actual_frames_reviewed=True,reviewed_by={'kind':'codex','session_id':'01a203c2-2956-74e1-a574-bfb03258d250'},cut_frames=[64,161])
 def test_default_and_actual_interior_cuts(self):
  self.assertEqual(reviewed_cut_frames(None,'S03E06','a'*64,0,250),set())
  self.assertEqual(reviewed_cut_frames(self.evidence(),'S03E06','a'*64,0,250),{64,161})
 def test_unbound_unreviewed_or_wrong_native_indices_rejected(self):
  for key,value in [('master_sha256','b'*64),('episode','S03E07'),('actual_frames_reviewed',False),('native_fps',30),('cut_frames',[0]),('cut_frames',[250]),('cut_frames',[64,64]),('cut_frames',[True]),('reviewed_by',{})]:
   with self.subTest(key=key,value=value),self.assertRaises(ValueError):
    d=self.evidence();d[key]=value;reviewed_cut_frames(d,'S03E06','a'*64,0,250)
if __name__=='__main__':unittest.main()
