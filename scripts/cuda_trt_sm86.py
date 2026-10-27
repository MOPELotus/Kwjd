"""Pinned same-model TensorRT RIFE placement on the shared physical A4000."""
import json
import math
from pathlib import Path
import subprocess
import time

from build_rife_sm86 import DeviceMemory,make_allocator,GIB
from cuda_trt import CudaRife as BaseRife,audit_typed_model,MODEL_SHA256,SOURCE_SHA256
from enhance_stage import sha256,WaitGPU

ENGINE_SHA='77a32369d84689d37ee64141a900531239346efa7b9001cc1cd1bdb72ed5f90a'
BUILD_RECIPE_SHA='b850d393872d990c452c52858c8b800e726d9ecb60262a50a8c5fedbe0e82ee3'


def runtime_reserve_bytes(cfg):
    """Runtime admission policy; independent of the historical engine build floor."""
    value=cfg.get('restore_free_reserve_gib',4)
    if type(value) not in (int,float) or not math.isfinite(value) or not .5<=value<=8:
        raise ValueError('Shared CUDA reserve must be finite and within0.5..8GiB')
    return int(value*GIB)


class CudaRife(BaseRife):
    def thermal_check(self):
        now=time.monotonic()
        if now-getattr(self,'_thermal_checked',-100)<5:return
        rows=subprocess.check_output(['nvidia-smi','--query-gpu=index,temperature.gpu',
                                      '--format=csv,noheader,nounits'],text=True).strip().splitlines()
        values={int(r.split(',')[0]):int(r.split(',')[1]) for r in rows}
        if values.get(0,100)>=97 or values.get(1,100)>=86:
            raise WaitGPU('Actual thermal limits require cooling before retry: '+str(values))
        self._thermal_checked=now

    def __init__(self,cfg):
        self.reserve_bytes=runtime_reserve_bytes(cfg)
        self.thermal_check()
        pins=cfg['file_sha256']
        for key in ('rife_trt_engine','rife_trt_model','rife_trt_build_report','rife_trt_build_recipe','rife_onnx'):
            p=cfg[key]
            if pins.get(p)!=sha256(p):raise ValueError('Unpinned SM86 asset: '+key)
        if (sha256(cfg['rife_trt_engine'])!=ENGINE_SHA
                or sha256(cfg['rife_trt_model'])!=MODEL_SHA256
                or sha256(cfg['rife_onnx'])!=SOURCE_SHA256
                or sha256(cfg['rife_trt_build_recipe'])!=BUILD_RECIPE_SHA):
            raise ValueError('SM86 source/model/build identity differs')
        proof=json.loads(Path(cfg['rife_trt_build_report']).read_text())
        expected={'status':'BUILT_ONLY','engine_sha256':ENGINE_SHA,'model_sha256':MODEL_SHA256,
                  'source_sha256':BUILD_RECIPE_SHA,'gpu':'NVIDIA RTX A4000','physical_gpu_index':0,
                  'compute_capability':[8,6],'tensorrt':'10.13.3.9','torch':'2.6.0+cu126',
                  'strongly_typed':True,'tf32':False,'reserve_gib':4,'allocator_errors':[]}
        if any(type(proof.get(k)) is not type(v) or proof.get(k)!=v for k,v in expected.items()):
            raise ValueError('SM86 build proof differs')
        if proof.get('min_observed_free_bytes',0)<4*GIB:raise ValueError('Build reserve evidence failed')
        audit_typed_model(cfg['rife_trt_model'])
        import torch
        import tensorrt as trt
        self.torch=torch;self.device=torch.device('cuda:0')
        if (cfg.get('gpu')!=0 or cfg.get('gpu_name')!='NVIDIA RTX A4000'
                or torch.__version__!='2.6.0+cu126' or trt.__version__!='10.13.3.9'):
            raise ValueError('Require reviewed SM86 runtime profile')
        torch.cuda.set_device(self.device)
        if (torch.cuda.get_device_name(0)!='NVIDIA RTX A4000'
                or torch.cuda.get_device_capability(0)!=(8,6)):
            raise ValueError('Require physical A4000 sm86')
        actual=subprocess.check_output(['nvidia-smi','-i','0','--query-gpu=name,driver_version',
                                       '--format=csv,noheader'],text=True).strip()
        if actual!='NVIDIA RTX A4000, 580.178.04':raise ValueError('Physical GPU/driver differs')
        cap=float(cfg['restore_memory_gib'])*GIB
        def reserve():return self.reserve_bytes+max(0,cap-torch.cuda.memory_reserved(0))+128*1024**2
        self.memory=DeviceMemory();self.allocator=make_allocator(trt,self.memory,reserve)
        self.logger=trt.Logger(trt.Logger.WARNING);self.runtime=trt.Runtime(self.logger)
        self.runtime.gpu_allocator=self.allocator
        self.engine=self.runtime.deserialize_cuda_engine(Path(cfg['rife_trt_engine']).read_bytes())
        if self.engine is None:raise WaitGPU('SM86 engine cannot initialize within shared reserve')
        self.context=self.engine.create_execution_context()
        if self.context is None:raise WaitGPU('SM86 workspace cannot initialize within shared reserve')
        try:
            self._x=torch.empty((1,7,1080,1920),device=self.device,dtype=torch.float32)
            self._y=torch.empty((1,3,1080,1920),device=self.device,dtype=torch.float32)
        except torch.cuda.OutOfMemoryError as error:raise WaitGPU('SM86 Torch I/O exceeds allocator cap') from error
        inputs=[];outputs=[]
        for i in range(self.engine.num_io_tensors):
            name=self.engine.get_tensor_name(i)
            if self.engine.get_tensor_mode(name)==trt.TensorIOMode.INPUT:inputs.append(name)
            else:outputs.append(name)
        if len(inputs)!=1 or len(outputs)!=1:raise ValueError('Unexpected SM86 model I/O')
        for name,tensor in [(inputs[0],self._x),(outputs[0],self._y)]:
            if tuple(self.engine.get_tensor_shape(name))!=tuple(tensor.shape) or self.engine.get_tensor_dtype(name)!=trt.float32:
                raise ValueError('Unexpected SM86 model shape/precision')
            if not self.context.set_tensor_address(name,tensor.data_ptr()):raise ValueError('SM86 I/O binding failed')
        if self.allocator.errors:raise ValueError('SM86 allocator error: '+str(self.allocator.errors))
        if self.memory.free_bytes()<reserve():raise WaitGPU('SM86 initialization leaves insufficient shared headroom')
        print(json.dumps({'rife':'TensorRT same typed model sm86','gpu':'NVIDIA RTX A4000',
                          'engine_sha256':ENGINE_SHA,'global_free_bytes':self.memory.free_bytes(),
                          'context_device_memory_bytes':self.engine.device_memory_size_v2,
                          'allocator_peak_bytes':self.allocator.peak_bytes}),flush=True)
        print(json.dumps({'shared_runtime_reserve_bytes':self.reserve_bytes,
                          'torch_allocator_cap_bytes':int(cap),'headroom_margin_bytes':128*1024**2}),flush=True)

    def interpolate(self,a,b,timestep):
        self.thermal_check()
        if self.memory.free_bytes()<self.reserve_bytes+128*1024**2:raise WaitGPU('Shared SM86 global headroom changed')
        result=super().interpolate(a,b,timestep)
        if self.allocator.errors:raise WaitGPU('SM86 allocation callback failed; segment retained')
        return result
