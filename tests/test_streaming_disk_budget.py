import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import segmented_backend as backend
from enhance_stage import GIB,check_space,WaitDisk

class StreamingBudgetTests(unittest.TestCase):
    def test_streaming_still_budgets_two_full_video_copies(self):
        with patch.object(backend.engine,'source_info',return_value={'format':{'duration':'3517.56'}}):
            result=backend.plan('master',{'fingerprint':'model','scratch':'/tmp','rife_backend':'cuda_trt_sm86'})
        self.assertEqual(result['scratch_peak_bytes'],GIB//2)
        self.assertGreater(result['additional_peak_bytes'],37*GIB)
        self.assertLess(result['additional_peak_bytes'],38*GIB)
        self.assertEqual(result['max_video_bitrate'],40_000_000)
        self.assertEqual(result['reserve_gib'],25)
    def test_cross_volume_admission_without_png_dump_and_preserves_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg={'fingerprint':'model','scratch':str(root),'rife_backend':'cuda_trt_sm86','post_root':str(root)};fp=backend.fingerprint(cfg)
            (root/'context.json').write_text(json.dumps({'master_sha256':'digest','backend_fingerprint':fp}))
            with patch.object(backend.engine,'source_info',return_value={'format':{'duration':'3517.56'}}),patch.object(backend,'context',return_value=('digest',fp,root)),patch.object(backend,'verified_segment_bytes',return_value=0),patch.object(backend.concat_transfer,'choose_concat',return_value=(root/'home-concat',{'concat_path':str(root/'home-concat'),'concat_additional_peak_bytes':int(3517.56*45_000_000/8)+16*1024**2})):
                result=backend.plan('master',cfg,root/'output','S01E49')
            # Current root can host one bounded video copy plus pipe bookkeeping and25GiB.
            with patch('enhance_stage.shutil.disk_usage',return_value=SimpleNamespace(free=49*GIB)):
                check_space(root,result['additional_peak_bytes'],25)
                with self.assertRaises(WaitDisk):check_space(root,result['additional_peak_bytes']+8*GIB-GIB//2,25)
            with patch('enhance_stage.shutil.disk_usage',return_value=SimpleNamespace(free=25*GIB)):
                with self.assertRaises(WaitDisk):check_space(root,result['additional_peak_bytes'],25)
            self.assertGreater(result['extra_volumes'][0]['additional_peak_bytes'],18*GIB)

if __name__=="__main__":unittest.main()
