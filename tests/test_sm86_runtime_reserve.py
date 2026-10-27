import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cuda_trt_sm86 import CudaRife,runtime_reserve_bytes,GIB
from enhance_stage import WaitGPU


class RuntimeReserveTests(unittest.TestCase):
    def test_existing_default_and_authorized_lower_floor(self):
        self.assertEqual(runtime_reserve_bytes({}),4*GIB)
        self.assertEqual(runtime_reserve_bytes({'restore_free_reserve_gib':3}),3*GIB)

    def test_invalid_policy_cannot_silently_disable_protection(self):
        for value in (True,False,None,'3',0,-1,.49,8.1,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                runtime_reserve_bytes({'restore_free_reserve_gib':value})

    def fake_rife(self,free):
        obj=object.__new__(CudaRife)
        obj.reserve_bytes=3*GIB
        obj.thermal_check=Mock()
        obj.memory=Mock();obj.memory.free_bytes.return_value=free
        obj.allocator=Mock();obj.allocator.errors=[]
        return obj

    def test_reduced_floor_allows_same_inference_call(self):
        obj=self.fake_rife(3*GIB+128*1024**2)
        with patch('cuda_trt.CudaRife.interpolate',return_value='same pixels') as infer:
            self.assertEqual(obj.interpolate('a','b',.4),'same pixels')
            infer.assert_called_once_with('a','b',.4)
        obj.thermal_check.assert_called_once()

    def test_memory_below_new_floor_still_refuses_inference(self):
        obj=self.fake_rife(3*GIB+128*1024**2-1)
        with patch('cuda_trt.CudaRife.interpolate') as infer,self.assertRaises(WaitGPU):
            obj.interpolate('a','b',.4)
        infer.assert_not_called()
