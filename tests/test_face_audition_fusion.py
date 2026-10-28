"""CPU-only numeric comparisons against installed facexlib0.3.0 official paste.

No helper constructor, model download, GPU context, FFmpeg or source media.
Run using post/face-venv/bin/python with CUDA_VISIBLE_DEVICES=''.

Measured 2026-10-28 with OpenCV 4.11.0 and facexlib 0.3.0: 3 tests
passed in 4.619s. Center / rotation / edge / overlap each max 1 LSB;
mean LSB respectively .0325890 / .0556053 / .0378252 / .0577264.
Global fixed-point map matched full-frame warp exactly. An earlier rejected
ROI-shifted affine experiment reached max 11 LSB because of sampling phase;
that implementation is not used here. Synthetic CPU evidence only: no real
ParseNet inference, GPU comparison, performance or visual acceptance claim.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from face_audition_fusion import paste_faces_roi,_maps


class FusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import cv2,numpy,torch
            from facexlib.utils.face_restoration_helper import FaceRestoreHelper
        except ImportError as error:raise unittest.SkipTest(str(error))
        cls.cv2=cv2;cls.np=numpy;cls.torch=torch;cls.Helper=FaceRestoreHelper
        torch.set_num_threads(1);cv2.setNumThreads(1)
        labels=numpy.zeros((512,512),numpy.int32)
        cv2.ellipse(labels,(256,260),(185,220),0,0,360,1,-1)
        labels[200:220,180:210]=14;labels[300:340,225:280]=15
        labels[100:130,200:240]=16
        logits=torch.zeros((1,19,512,512),dtype=torch.float32)
        logits.scatter_(1,torch.from_numpy(labels).long().unsqueeze(0).unsqueeze(0),1)
        class Parser:
            def __init__(self):self.calls=0
            def __call__(self,tensor):
                if tensor.device.type!='cpu':raise AssertionError('CPU-only test')
                self.calls+=1;return (logits,)
        cls.Parser=Parser

    def helper(self,matrices):
        np=self.np;random=np.random.default_rng(743)
        helper=self.Helper.__new__(self.Helper)
        helper.input_img=random.integers(0,256,(576,768,3),np.uint8)
        helper.upscale_factor=1;helper.use_parse=True;helper.face_size=(512,512)
        helper.restored_faces=[random.integers(0,256,(512,512,3),np.uint8) for _ in matrices]
        helper.inverse_affine_matrices=[m.copy() for m in matrices]
        helper.face_parse=self.Parser();helper.device=self.torch.device('cpu')
        return helper

    def test_center_rotation_edge_and_overlapping_faces(self):
        np=self.np;cv2=self.cv2
        rotated=cv2.getRotationMatrix2D((256,256),23,.85);rotated[:,2]+=[125,20]
        cases={'center':[np.array([[.65,0,180],[0,.65,100]],np.float64)],
               'rotation':[rotated],
               'edge':[np.array([[.9,0,-220],[0,.9,-100]],np.float64)],
               'overlap':[np.array([[.8,0,100],[0,.8,40]],np.float64),rotated]}
        for name,matrices in cases.items():
            with self.subTest(case=name):
                helper=self.helper(matrices)
                official=helper.paste_faces_to_input_image()
                actual=paste_faces_roi(helper)
                delta=np.abs(official.astype(np.int16)-actual.astype(np.int16))
                print(json.dumps({'fusion_cpu_case':name,'max_lsb':int(delta.max()),
                                  'mean_lsb':float(delta.mean()),'different_values':int((delta!=0).sum())}),flush=True)
                self.assertLessEqual(int(delta.max()),2)
                self.assertEqual(helper.face_parse.calls,2*len(matrices))

    def test_global_fixed_point_roi_map_matches_full_frame_warp(self):
        np=self.np;cv2=self.cv2;helper=self.helper([np.eye(2,3)])
        matrix=cv2.getRotationMatrix2D((256,256),17,.72);matrix[:,2]+=[230,80]
        full=cv2.warpAffine(helper.restored_faces[0],matrix,(1280,720))
        xy,alpha=_maps(matrix,(200,30,850,680))
        roi=cv2.remap(helper.restored_faces[0],xy,alpha,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        self.assertTrue(np.array_equal(full[30:680,200:850],roi))

    def test_no_faces_is_exact_copy_and_does_not_alias_input(self):
        helper=self.helper([]);actual=paste_faces_roi(helper)
        self.assertTrue(self.np.array_equal(actual,helper.input_img))
        self.assertFalse(self.np.shares_memory(actual,helper.input_img))


if __name__=='__main__':unittest.main()
