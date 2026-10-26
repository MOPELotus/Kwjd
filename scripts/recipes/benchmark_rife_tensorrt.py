#!/usr/bin/env python3
"""One bounded native TensorRT vs pinned ORT comparison, physical GPU1 only.
No repo integration. Run with CUDA_VISIBLE_DEVICES=1 after GPU release.
"""
import argparse,datetime,gc,hashlib,json,os,subprocess,time,traceback
from pathlib import Path

def digest(p):
 with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def device_status():
 return subprocess.check_output(['nvidia-smi','-i','1','--query-gpu=name,temperature.gpu,clocks.sm,power.draw,memory.used','--format=csv,noheader'],text=True).strip()

def main():
 p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--reference',required=True);p.add_argument('--output',required=True);p.add_argument('--engine',required=True);a=p.parse_args()
 target=Path(a.output)
 if target.exists():raise ValueError('Refusing overwrite report')
 report={'started_at':datetime.datetime.now().astimezone().isoformat(),'physical_gpu_index':1,'model':a.model,'model_sha256':digest(a.model),'reference':a.reference,'reference_sha256':digest(a.reference),'strongly_typed':True,'scope':'One native TensorRT comparison; no production integration or final ETA claim'}
 try:
  if os.environ.get('CUDA_VISIBLE_DEVICES')!='1':raise ValueError('Require physical GPU1-only visibility')
  import torch,numpy as np,tensorrt as trt,onnxruntime as ort
  from PIL import Image
  report['versions']={'torch':torch.__version__,'numpy':np.__version__,'tensorrt':trt.__version__,'ort':ort.__version__}
  if trt.__version__!='10.13.3.9':raise ValueError('Unexpected TRT version')
  if torch.cuda.device_count()!=1 or torch.cuda.get_device_name(0)!='Tesla T10':raise ValueError('Require verified Tesla T10 only')
  torch.cuda.set_device(0);torch.set_num_threads(4)
  report['before_build']=device_status()
  logger=trt.Logger(trt.Logger.WARNING);builder=trt.Builder(logger)
  network=builder.create_network(1<<int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
  parser=trt.OnnxParser(network,logger)
  stamp=time.monotonic()
  if not parser.parse(Path(a.model).read_bytes()):raise ValueError('TRT ONNX parse: '+ '\n'.join(str(parser.get_error(i)) for i in range(parser.num_errors)))
  if network.num_inputs!=1 or network.num_outputs!=1:raise ValueError('Unexpected model I/O')
  source=network.get_input(0);source.shape=(1,7,1080,1920)
  cfg=builder.create_builder_config();cfg.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE,2*1024**3)
  cfg.builder_optimization_level=2;cfg.avg_timing_iterations=1
  # No global FP16 flag: explicit ONNX types keep grid/flow/warp FP32.
  serialized=builder.build_serialized_network(network,cfg)
  if serialized is None:raise RuntimeError('TRT build returned None; stop fallback')
  report['build_seconds']=time.monotonic()-stamp
  enginepath=Path(a.engine)
  if enginepath.exists():raise ValueError('Refusing overwrite engine')
  enginepath.parent.mkdir(parents=True,exist_ok=True);enginepath.write_bytes(bytes(serialized));report['engine_sha256']=digest(enginepath)
  runtime=trt.Runtime(logger);engine=runtime.deserialize_cuda_engine(serialized);context=engine.create_execution_context()
  names=[engine.get_tensor_name(i) for i in range(engine.num_io_tensors)]
  inp=[n for n in names if engine.get_tensor_mode(n)==trt.TensorIOMode.INPUT];out=[n for n in names if engine.get_tensor_mode(n)==trt.TensorIOMode.OUTPUT]
  if len(inp)!=1 or len(out)!=1:raise ValueError('Unexpected engine I/O')
  if engine.get_tensor_dtype(inp[0])!=trt.float32 or engine.get_tensor_dtype(out[0])!=trt.float32:raise ValueError('Engine must expose float32 I/O')
  x=torch.empty((1,7,1080,1920),dtype=torch.float32,device='cuda:0');y=torch.empty((1,3,1080,1920),dtype=torch.float32,device='cuda:0')
  context.set_tensor_address(inp[0],x.data_ptr());context.set_tensor_address(out[0],y.data_ptr())
  pair=[]
  for n in ['rife_pair_01.png','rife_pair_02.png']:
   image=Image.open(Path(__file__).parent/n).convert('RGB')
   if image.size!=(1920,1080):raise ValueError('Expected native actual footage pair')
   pair.append(torch.from_numpy(np.asarray(image,dtype=np.uint8).copy()).permute(2,0,1).float().div(255).cuda())
  x[:,:3]=pair[0];x[:,3:6]=pair[1];x[:,6]=.4
  def infer():
   if not context.execute_async_v3(torch.cuda.current_stream().cuda_stream):raise RuntimeError('TRT execute failed')
   torch.cuda.synchronize()
  report['before_trt_warmup']=device_status()
  for _ in range(5):infer()
  costs=[]
  for _ in range(8):
   stamp=time.monotonic();infer();costs.append(time.monotonic()-stamp)
  results={.4:y.cpu().numpy().copy()}
  x[:,6]=.8;infer();results[.8]=y.cpu().numpy().copy()
  report['trt_seconds']=costs;report['after_trt']=device_status()
  # Same original FP32 official network, CUDA inference for direct numeric reference.
  session=ort.InferenceSession(a.reference,providers=[('CUDAExecutionProvider',{'device_id':0,'cudnn_conv_algo_search':'HEURISTIC','use_tf32':0})])
  if session.get_providers()[0]!='CUDAExecutionProvider':raise RuntimeError('ORT CUDA not active')
  io=session.io_binding();io.bind_input('input','cuda',0,np.float32,tuple(x.shape),x.data_ptr());io.bind_output('output','cuda',0,np.float32,tuple(y.shape),y.data_ptr())
  def ref():torch.cuda.synchronize();session.run_with_iobinding(io);torch.cuda.synchronize()
  errors=[]
  for t in [.4,.8]:
   x[:,6]=t;ref();truth=y.cpu().numpy().copy();diff=np.abs(results[t]-truth)
   errors.append({'timestep':t,'mae':float(diff.mean()),'p99':float(np.quantile(diff,.99)),'max':float(diff.max()),'psnr_db':float(-10*np.log10(np.mean((results[t]-truth)**2)+1e-30))})
  for _ in range(3):ref()
  costs=[]
  for _ in range(8):
   stamp=time.monotonic();ref();costs.append(time.monotonic()-stamp)
  report['ort_reference_seconds']=costs;report['numeric_error_vs_official_fp32']=errors;report['after_ort']=device_status()
  report['status']='completed';report['limitations']=['Short comparison, not sustained full pipeline ETA','GPU clocks/temperature differ across sequential runs; assess measured device status','Only two timesteps/one actual image pair for numeric comparison; visual and longer chain required before production']
 except Exception as error:
  report['status']='failed_stop_fallback';report['error']=repr(error);report['traceback']=traceback.format_exc();raise
 finally:
  report['finished_at']=datetime.datetime.now().astimezone().isoformat();target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
