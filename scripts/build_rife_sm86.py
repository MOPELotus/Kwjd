#!/usr/bin/env python3
"""One strongly typed A4000 build with reserve-aware TensorRT allocations.

No production activation. The caller schedules this after releasing its own
existing GPU contexts. Other users and hardware settings are never changed.
"""
import argparse
import ctypes as C
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sysconfig
import threading
import time

from enhance_stage import atomic_json,sha256

MODEL_SHA='2888b81b22c2e7b5488a656dd9403cd86afc6a6eb126426962f54ffcbc72b7f4'
GIB=1024**3


class Memory(C.Structure):
    _fields_=[('total',C.c_ulonglong),('free',C.c_ulonglong),('used',C.c_ulonglong)]


class DeviceMemory:
    def __init__(self):
        lib=Path(sysconfig.get_paths()['purelib'])/'nvidia/cuda_runtime/lib/libcudart.so.12'
        self.cuda=C.CDLL(str(lib));self.nvml=C.CDLL('libnvidia-ml.so.1')
        self.cuda.cudaSetDevice.argtypes=[C.c_int]
        self.cuda.cudaMemGetInfo.argtypes=[C.POINTER(C.c_size_t),C.POINTER(C.c_size_t)]
        self.cuda.cudaMalloc.argtypes=[C.POINTER(C.c_void_p),C.c_size_t]
        self.cuda.cudaFree.argtypes=[C.c_void_p]
        self.cuda.cudaMemcpy.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t,C.c_int]
        self.nvml.nvmlInit_v2.restype=C.c_int
        self.nvml.nvmlDeviceGetHandleByIndex_v2.argtypes=[C.c_uint,C.POINTER(C.c_void_p)]
        self.nvml.nvmlDeviceGetMemoryInfo.argtypes=[C.c_void_p,C.POINTER(Memory)]
        self.handle=C.c_void_p()
        self.check(self.nvml.nvmlInit_v2())
        self.check(self.nvml.nvmlDeviceGetHandleByIndex_v2(0,C.byref(self.handle)))

    @staticmethod
    def check(code):
        if code:raise RuntimeError('CUDA/NVML call failed: '+str(code))

    def select(self):self.check(self.cuda.cudaSetDevice(0))

    def free_bytes(self):
        self.select();free=C.c_size_t();total=C.c_size_t();physical=Memory()
        self.check(self.cuda.cudaMemGetInfo(C.byref(free),C.byref(total)))
        self.check(self.nvml.nvmlDeviceGetMemoryInfo(self.handle,C.byref(physical)))
        return min(free.value,physical.free)

    def malloc(self,size):
        self.select();pointer=C.c_void_p()
        self.check(self.cuda.cudaMalloc(C.byref(pointer),size))
        return pointer.value

    def release(self,pointer):self.select();self.check(self.cuda.cudaFree(C.c_void_p(pointer)))

    def copy(self,dst,src,size):
        self.select();self.check(self.cuda.cudaMemcpy(C.c_void_p(dst),C.c_void_p(src),size,3))


def pointer_value(value):
    if value is None:return 0
    if isinstance(value,int):return value
    name=C.pythonapi.PyCapsule_GetName;name.argtypes=[C.py_object];name.restype=C.c_char_p
    get=C.pythonapi.PyCapsule_GetPointer;get.argtypes=[C.py_object,C.c_char_p];get.restype=C.c_void_p
    return get(value,name(value)) or 0


def make_allocator(trt,memory,floor=4*GIB+256*1024**2):
    class BudgetAllocator(trt.IGpuAllocator):
        def __init__(self):
            trt.IGpuAllocator.__init__(self)
            self.live={};self.raw={};self.allocated={};self.lock=threading.RLock();self.peak_bytes=0
            self.min_free_bytes=memory.free_bytes();self.denied=[];self.errors=[]

        def allocate(self,size,alignment,flags):
            with self.lock:
                pointer=None
                try:
                    free=memory.free_bytes();self.min_free_bytes=min(self.min_free_bytes,free)
                    limit=floor() if callable(floor) else floor
                    if size<=0:return None
                    if alignment<0 or (alignment and alignment&(alignment-1)) or alignment>2**21:
                        self.denied.append({'size':size,'alignment':alignment,'reason':'unsupported alignment'});return None
                    padded=size+(alignment-1 if alignment>256 else 0)
                    if free-padded<limit:
                        self.denied.append({'size':size,'alignment':alignment,'free':free});return None
                    pointer=memory.malloc(padded)
                    after=memory.free_bytes();self.min_free_bytes=min(self.min_free_bytes,after)
                    if after<limit:
                        # Never retry cudaFree after an ambiguous CUDA error.
                        raw=pointer;pointer=None;memory.release(raw)
                        self.denied.append({'size':size,'after':after});return None
                    aligned=((pointer+alignment-1)//alignment*alignment) if alignment else pointer
                    self.live[aligned]=size;self.raw[aligned]=pointer;self.allocated[aligned]=padded
                    self.peak_bytes=max(self.peak_bytes,sum(self.allocated.values()))
                    return aligned
                except Exception as error:
                    self.errors.append(str(error))
                    if pointer:
                        try:memory.release(pointer)
                        except Exception as cleanup:self.errors.append(str(cleanup))
                    return None

        def allocate_async(self,size,alignment,flags,stream):return self.allocate(size,alignment,flags)

        def deallocate(self,memory_pointer):
            with self.lock:
                try:
                    pointer=pointer_value(memory_pointer)
                    if not pointer:return True
                    if pointer not in self.live:return False
                    memory.release(self.raw[pointer]);del self.live[pointer]
                    del self.raw[pointer];del self.allocated[pointer];return True
                except Exception as error:self.errors.append(str(error));return False

        def deallocate_async(self,memory_pointer,stream):return self.deallocate(memory_pointer)

        def reallocate(self,address,alignment,new_size):
            with self.lock:
                old=pointer_value(address)
                if old not in self.live:return None
                if new_size<=self.live[old] and (not alignment or old%alignment==0):return old
                new=self.allocate(new_size,alignment,0)
                if new is None:return None
                try:memory.copy(new,old,min(new_size,self.live[old]))
                except Exception as error:
                    self.errors.append(str(error));self.deallocate(new);return None
                if not self.deallocate(old):self.deallocate(new);return None
                return new
    return BudgetAllocator()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--post',default=str(Path.home()/'qingqingwu/post'))
    p.add_argument('--engine',required=True);p.add_argument('--report',required=True)
    args=p.parse_args();post=Path(args.post);engine=Path(args.engine);report=Path(args.report)
    if engine.exists() or report.exists():raise ValueError('Existing build assets retained')
    if os.environ.get('CUDA_VISIBLE_DEVICES')!='0':raise ValueError('Require physical GPU0-only visibility')
    model=post/'tools/rife-onnx/rife_v4.6.neural-fp16.onnx'
    if sha256(model)!=MODEL_SHA:raise ValueError('Typed RIFE model changed')
    from cuda_trt import audit_typed_model
    audit=audit_typed_model(model)
    import torch
    import tensorrt as trt
    if torch.__version__!='2.6.0+cu126' or trt.__version__!='10.13.3.9':raise ValueError('Runtime differs')
    torch.cuda.set_device(0)
    if torch.cuda.get_device_name(0)!='NVIDIA RTX A4000' or torch.cuda.get_device_capability(0)!=(8,6):
        raise ValueError('Require actual RTX A4000 sm86')
    memory=DeviceMemory()
    if memory.free_bytes()<int(4.5*GIB):raise ValueError('Insufficient pre-build headroom')
    allocator=make_allocator(trt,memory)
    record={'started_at':datetime.now(timezone.utc).isoformat(),'status':'BUILDING',
            'model_sha256':MODEL_SHA,'source_sha256':sha256(__file__),
            'gpu':'NVIDIA RTX A4000','physical_gpu_index':0,'compute_capability':[8,6],
            'tensorrt':trt.__version__,'torch':torch.__version__,'strongly_typed':True,
            'workspace_mib':256,'reserve_gib':4,'allocation_buffer_mib':256,
            'dtype_audit':audit,'limitation':'Allocator observes managed requests; external library allocations and other-user races remain separately monitored'}
    atomic_json(report,record);start=time.monotonic()
    try:
        logger=trt.Logger(trt.Logger.WARNING);builder=trt.Builder(logger);builder.gpu_allocator=allocator
        network=builder.create_network(1<<int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
        parser=trt.OnnxParser(network,logger)
        if not parser.parse(model.read_bytes()):
            raise ValueError('; '.join(str(parser.get_error(i)) for i in range(parser.num_errors)))
        network.get_input(0).shape=(1,7,1080,1920)
        config=builder.create_builder_config();config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE,256*1024**2)
        config.builder_optimization_level=2;config.avg_timing_iterations=1;config.clear_flag(trt.BuilderFlag.TF32)
        blob=builder.build_serialized_network(network,config)
        if blob is None:raise RuntimeError('TRT build refused or failed within allocation budget')
        if allocator.errors:raise ValueError('Allocator callback errors; reject build')
        if memory.free_bytes()<4*GIB or allocator.min_free_bytes<4*GIB:
            raise ValueError('Observed global free below reserve; candidate rejected')
        engine.parent.mkdir(parents=True,exist_ok=True)
        with engine.open('xb') as out:out.write(bytes(blob));out.flush();os.fsync(out.fileno())
        record.update(status='BUILT_ONLY',engine_sha256=sha256(engine),engine=str(engine),tf32=False)
    except Exception as error:
        record.update(status='FAILED',error=str(error));raise
    finally:
        record.update(seconds=time.monotonic()-start,allocator_peak_bytes=allocator.peak_bytes,
                      min_observed_free_bytes=allocator.min_free_bytes,denied=allocator.denied,
                      allocator_errors=allocator.errors,finished_at=datetime.now(timezone.utc).isoformat())
        atomic_json(report,record)


if __name__=='__main__':main()
