"""Persistent FP16 real-life SRVGG restoration on a verified physical CUDA GPU.

Architecture executes the pinned official BSD-3-Clause source, removing only its
optional BasicSR registry import/decorator. No BasicSR/TorchVision or OCR env use.
"""
from pathlib import Path
import json

from enhance_stage import sha256,WaitGPU


class Restorer:
    def __init__(self,cfg):
        for key in ('srvgg_source','srvgg_general','srvgg_wdn'):
            filename=cfg[key]
            if cfg['file_sha256'].get(filename) != sha256(filename):
                raise ValueError('Unpinned/changed official restoration asset: '+key)
        import torch
        self.torch=torch;self.cfg=cfg
        self.device=torch.device('cuda:'+str(cfg.get('restore_gpu',cfg['gpu'])))
        if not torch.cuda.is_available():raise ValueError('Physical CUDA required; no CPU fallback')
        torch.cuda.set_device(self.device)
        props=torch.cuda.get_device_properties(self.device)
        if torch.cuda.get_device_name(self.device) != cfg.get('restore_gpu_name',cfg['gpu_name']):
            raise ValueError('CUDA physical device differs from tested restoration GPU')
        if f'sm_{props.major}{props.minor}' not in torch.cuda.get_arch_list():
            raise ValueError('Installed PyTorch wheel does not support physical GPU architecture')
        if torch.__version__ != '2.6.0+cu126':raise ValueError('Unexpected isolated PyTorch version')
        torch.set_num_threads(4)
        cap=cfg.get('restore_memory_gib')
        fraction=.8 if cap is None else float(cap)*1024**3/props.total_memory
        if not 0<fraction<=.8:raise ValueError('Invalid CUDA restoration memory cap')
        torch.cuda.set_per_process_memory_fraction(fraction,self.device)
        self.allocator_cap_bytes=fraction*props.total_memory
        self._check_headroom()
        torch.backends.cudnn.benchmark=True
        torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
        raw=Path(cfg['srvgg_source']).read_text()
        raw=raw.replace('from basicsr.utils.registry import ARCH_REGISTRY\n','').replace('@ARCH_REGISTRY.register()\n','')
        namespace={};exec(compile(raw,cfg['srvgg_source'],'exec'),namespace)
        self.model=namespace['SRVGGNetCompact'](num_in_ch=3,num_out_ch=3,num_feat=64,num_conv=32,upscale=4,act_type='prelu')
        def weights(path):
            result=torch.load(path,map_location='cpu',weights_only=True)
            return result.get('params_ema',result.get('params',result))
        strong=weights(cfg['srvgg_general']);weak=weights(cfg['srvgg_wdn'])
        strength=float(cfg.get('denoise_strength',.35))
        if not 0<=strength<=1:raise ValueError('DNI strength must be0..1')
        self.model.load_state_dict({k:strength*strong[k]+(1-strength)*weak[k] for k in strong},strict=True)
        try:self.model.eval().to(device=self.device,dtype=torch.float16,memory_format=torch.channels_last)
        except torch.cuda.OutOfMemoryError as error:
            raise WaitGPU('Shared GPU memory changed during restoration initialization') from error
        print(json.dumps({'restoration':'official-general-SRVGG','torch':torch.__version__,
            'gpu':torch.cuda.get_device_name(self.device),'compute_capability':[props.major,props.minor],
            'fp16':True,'channels_last':True,'denoise_strength':strength}),flush=True)

    def _check_headroom(self):
        reserve=float(self.cfg.get('restore_free_reserve_gib',0))
        if reserve:
            remaining=max(0,self.allocator_cap_bytes-self.torch.cuda.memory_reserved(self.device))
            required=reserve*1024**3+remaining+128*1024**2
            if self.torch.cuda.mem_get_info(self.device)[0]<required:
                raise WaitGPU('Shared GPU must retain configured reserve plus remaining allocation budget')

    def restore_tensor(self,image):
        try:return self._restore_tensor(image)
        except self.torch.cuda.OutOfMemoryError as error:
            with self.torch.cuda.device(self.device):self.torch.cuda.empty_cache()
            raise WaitGPU('Shared GPU memory changed during restoration; segment retained for retry') from error

    def _restore_tensor(self,image):
        import numpy as np
        torch=self.torch
        self._check_headroom()
        with torch.inference_mode():
            pixels=np.asarray(image,dtype=np.uint8).copy()
            x=torch.from_numpy(pixels).permute(2,0,1).unsqueeze(0).to(self.device,dtype=torch.float16)/255
            x=x.contiguous(memory_format=torch.channels_last)
            tile=int(self.cfg.get('restore_tile',0))
            def predict(part):
                raw=self.model(part)
                return torch.nn.functional.interpolate(raw.float(),size=part.shape[-2:],mode='bicubic',
                    align_corners=False,antialias=True).clamp_(0,1)
            if tile:
                if tile<128 or tile>1024:raise ValueError('Invalid bounded restoration tile')
                # 34 spatial convolutions plus bicubic filter support;40 pixels
                # avoids exposing an interior tile's artificial padded boundary.
                pad=40;y=torch.empty((1,3,1080,1920),device=self.device,dtype=torch.float32)
                for top in range(0,1080,tile):
                    for left in range(0,1920,tile):
                        bottom=min(top+tile,1080);right=min(left+tile,1920)
                        y0=max(0,top-pad);x0=max(0,left-pad)
                        part=x[:,:,y0:min(1080,bottom+pad),x0:min(1920,right+pad)].contiguous(memory_format=torch.channels_last)
                        restored=predict(part)
                        y[:,:,top:bottom,left:right]=restored[:,:,top-y0:bottom-y0,left-x0:right-x0]
            else:y=predict(x)
            mix=float(self.cfg.get('restore_strength',.35))
            if not 0<=mix<=1:raise ValueError('Restoration blend must be0..1')
            y=mix*y+(1-mix)*x.float()
            if not torch.isfinite(y).all():raise ValueError('Nonfinite GPU restoration output')
            return y.contiguous()

    def restore(self,image):
        from PIL import Image
        torch=self.torch
        with torch.inference_mode():
            y=self.restore_tensor(image)
            pixels=(y[0].permute(1,2,0)*255).round().clamp_(0,255).to(torch.uint8).cpu().numpy()
            return Image.fromarray(pixels,'RGB')

    def release_workspace(self):
        with self.torch.cuda.device(self.device):self.torch.cuda.empty_cache()
