"""Persistent, native-size GFPGAN processor for isolated pipeline auditions.

CPU BGR8 input/output, CUDA detection/restoration/parsing, CPU alignment/fusion.
No production backend selection, ROI optimization or silent inference fallback.
"""
import inspect
import textwrap
import time
import types
import sys
import fcntl
from pathlib import Path

from render_face_samples import digest

WEIGHTS = {
    'GFPGANv1.4.pth': 'e2cd4703ab14f4d01fd1383a8a8b266f9a5833dacee8e6a79d3bf21a1b6be5ad',
    'detection_Resnet50_Final.pth': '6d1de9c2944f2ccddca5f5e010ea5ae64a39845a86311af6fdf30841b0a5a16d',
    'parsing_parsenet.pth': '3d558d8d0e42c20224f13cf5a29c79eba2d59913419f945545d8cf7b72920de2',
}


class GFPGANProcessor:
    def __init__(self, weights, *, protected_bottom=None):
        # All new face audition/rework processors share this same-user lease.
        # It does not claim control over other users' unrelated GPU workloads.
        lease = Path.home()/'qingqingwu/post-home/.face-compute.lock'
        self._lease = lease.open('a+')
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lease.close()
            raise RuntimeError('WAIT_GPU: another same-user face processor owns the shared lease') from error
        import torch
        import cv2
        import numpy as np
        import torchvision.transforms.functional as functional
        sys.modules['torchvision.transforms.functional_tensor'] = functional
        from basicsr.utils import img2tensor, tensor2img
        from facexlib.utils.face_restoration_helper import FaceRestoreHelper
        from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean
        for name, expected in WEIGHTS.items():
            if digest(Path(weights)/name) != expected:
                raise ValueError('GFPGAN asset changed: '+name)
        self.torch, self.np, self.cv2 = torch, np, cv2
        self.functional, self.img2tensor, self.tensor2img = functional, img2tensor, tensor2img
        if protected_bottom is not None and not 0 < protected_bottom < 1080:
            raise ValueError('Invalid protected bottom strip')
        self.protected_bottom = protected_bottom
        self.device = torch.device('cuda:0')
        if not torch.cuda.is_available() or torch.cuda.get_device_name(0) != 'NVIDIA RTX A4000':
            raise ValueError('Requires physical A4000 at CUDA0')
        free, total = torch.cuda.mem_get_info(0)
        budget = min(2.5*2**30, free-3*2**30)
        if budget < 1.75*2**30:
            raise RuntimeError('Insufficient shared GPU0 headroom')
        torch.cuda.set_per_process_memory_fraction(budget/total, 0)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.helper = FaceRestoreHelper(1, face_size=512, crop_ratio=(1,1),
            det_model='retinaface_resnet50', use_parse=True, device=self.device,
            model_rootpath=str(weights))
        method = self.helper.paste_faces_to_input_image
        source = textwrap.dedent(inspect.getsource(type(self.helper).paste_faces_to_input_image))
        if source.count('mask = np.zeros(out.shape)') != 1:
            raise ValueError('Unexpected facexlib fusion source')
        scope = {}
        exec(compile(source.replace('mask = np.zeros(out.shape)',
            'mask = np.zeros(out.shape, dtype=np.float32)'), '<float32-mask>', 'exec'),
            method.__func__.__globals__, scope)
        self.helper.paste_faces_to_input_image = types.MethodType(scope['paste_faces_to_input_image'], self.helper)
        self.model = GFPGANv1Clean(out_size=512, num_style_feat=512, channel_multiplier=2,
            decoder_load_path=None, fix_decoder=False, num_mlp=8, input_is_latent=True,
            different_w=True, narrow=1, sft_half=True)
        state = torch.load(Path(weights)/'GFPGANv1.4.pth', map_location='cpu', weights_only=True)
        self.model.load_state_dict(state['params_ema'], strict=True)
        self.model.eval().to(self.device)

    def process_bgr(self, image):
        """Return uint8 BGR and per-frame coverage/timing; failures propagate."""
        torch, np, helper = self.torch, self.np, self.helper
        if image.dtype != np.uint8 or image.shape != (1080,1920,3):
            raise ValueError('Expected native1080p BGR8')
        times = {}
        def timed(key, function):
            torch.cuda.synchronize(self.device)
            start = time.monotonic()
            value = function()
            torch.cuda.synchronize(self.device)
            times[key] = time.monotonic()-start
            return value
        with torch.inference_mode(), torch.cuda.device(self.device):
            timed('prepare', lambda: (helper.clean_all(), helper.read_image(image)))
            count = int(timed('detect', lambda: helper.get_face_landmarks_5(resize=640, eye_dist_threshold=5)) or 0)
            if count:
                timed('align', helper.align_warp_face)
                def restore():
                    for crop in helper.cropped_faces:
                        x = self.img2tensor(crop.astype(np.float32)/255, bgr2rgb=True, float32=True)
                        x = self.functional.normalize(x, (.5,.5,.5), (.5,.5,.5)).unsqueeze(0).to(self.device)
                        y = self.model(x, return_rgb=False, randomize_noise=False)[0]
                        if not torch.isfinite(y).all():
                            raise ValueError('Nonfinite restored face')
                        helper.add_restored_face(self.tensor2img(y, rgb2bgr=True, min_max=(-1,1)).astype(np.uint8))
                with torch.autocast('cuda', dtype=torch.float16):
                    timed('restore', restore)
                    def fuse():
                        helper.get_inverse_affine(None)
                        return helper.paste_faces_to_input_image(upsample_img=None)
                    result = timed('fuse', fuse)
            else:
                result = image
            if self.protected_bottom is not None:
                result[self.protected_bottom:] = image[self.protected_bottom:]
            if result.shape != image.shape or result.dtype != np.uint8 or len(helper.restored_faces) != count:
                raise ValueError('Face output/coverage mismatch')
            return result, dict(faces_detected=count, faces_restored=len(helper.restored_faces),
                               changed_pixels=int(np.any(result != image, axis=2).sum()), times=times)
