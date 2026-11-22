import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from fixed_workflow import validate_fixed_profile


class FixedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.cfg=dict(rife_backend='cuda_trt',restoration_backend='cuda_gfpgan_fused',
            restore=True,restore_gpu=0,restore_gpu_name='NVIDIA RTX A4000',restore_memory_gib=2.5,
            restore_free_reserve_gib=3.0,gpu=1,gpu_name='Tesla T10',encoder_gpu=1,
            light_filter='clarity',max_video_mbps=16,segment_source_frames=250,
            pipeline_prefetch=True,pipeline_prefetch_frames=1,resource_wait_in_process=False,
            input_frame_rate=25)

    def test_only_native_input_clock_varies_without_reselecting_stack(self):
        first=validate_fixed_profile(self.cfg)
        self.cfg['input_frame_rate']=30
        self.assertEqual(validate_fixed_profile(self.cfg),first)

    def test_cleanup_cannot_silently_replace_backend_devices_quality_or_overlap(self):
        for key,value in [('rife_backend','cuda_onnx'),('gpu',0),('restore',False),
                          ('light_filter','none'),('max_video_mbps',8),('pipeline_prefetch',False),
                          ('pipeline_prefetch_frames',2),('resource_wait_in_process',True),
                          ('input_frame_rate',24),('gpu',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_fixed_profile({**self.cfg,key:value})


if __name__=='__main__':unittest.main()
