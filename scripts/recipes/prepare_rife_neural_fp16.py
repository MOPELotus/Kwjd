"""Only neural Conv/ConvTranspose/LeakyRelu and block residual Add use FP16.

All original coordinate, flow, resize, warp, blend and shape nodes remain FP32.
Explicit boundary casts prevent normalized coordinates ever becoming FP16.
"""
from pathlib import Path
import copy,hashlib,json
import numpy as np
import onnx
from onnx import helper,numpy_helper,TensorProto
root=Path(__file__).resolve().parents[1];source=root/'tools/rife-onnx/rife_v4.6.onnx';output=root/'tools/rife-onnx/rife_v4.6.neural-fp16.onnx'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(source)=='6294aab9a9704f085caa65ab5bc897ac3a3ed0d054200e40b66bd7e43523e969'
assert onnx.__version__=='1.20.1'
m=onnx.load(source);original=list(m.graph.node);initializers={v.name:v for v in m.graph.initializer}
def selected(n):return n.op_type in ('Conv','ConvTranspose','LeakyRelu') or (n.op_type=='Add' and n.name.startswith('/block'))
neural={v:n for n in original if selected(n) for v in n.output}
half={v:v+'__neural_fp16' for v in neural}
new=[];converted_weights={};boundaries={}
for n0 in original:
 n=copy.deepcopy(n0)
 if selected(n):
  for i,v in enumerate(n.input):
   if v in half:n.input[i]=half[v]
   elif v in initializers:
    if v not in converted_weights:
     weight=numpy_helper.to_array(initializers[v])
     assert weight.dtype==np.float32
     newname=v+'__neural_fp16_weight';m.graph.initializer.append(numpy_helper.from_array(weight.astype(np.float16),newname));converted_weights[v]=newname
    n.input[i]=converted_weights[v]
   elif v:
    if v not in boundaries:
     cast=v+'__to_neural_fp16';new.append(helper.make_node('Cast',[v],[cast],name=cast,to=TensorProto.FLOAT16));boundaries[v]=cast
    n.input[i]=boundaries[v]
  for i,v in enumerate(n.output):n.output[i]=half[v]
  new.append(n)
  for v in n0.output:new.append(helper.make_node('Cast',[half[v]],[v],name=v+'__restore_fp32',to=TensorProto.FLOAT))
 else:new.append(n)
del m.graph.node[:];m.graph.node.extend(new)
onnx.checker.check_model(m,full_check=True)
output.write_bytes(m.SerializeToString())
producers={v:n for n in m.graph.node for v in n.output}
# Coordinate ancestry stops at explicit fp16->fp32 boundary: outside the
# neural blocks, every original arithmetic operator and constant is unchanged.
warp=[]
for n in m.graph.node:
 if n.op_type=='GridSample':
  todo=[n.input[1]];seen=set();ops=[]
  while todo:
   v=todo.pop()
   if v in seen:continue
   seen.add(v);p=producers.get(v)
   if p is None:continue
   if p.op_type=='Cast' and p.name.endswith('__restore_fp32'):continue
   assert not selected(p),'Neural FP16 node escaped the FP32 cast boundary'
   ops.append({'name':p.name,'op_type':p.op_type});todo.extend(p.input)
  warp.append({'grid_sample':n.name,'fp32_ancestry':ops})
r={'source':str(source),'source_sha256':sha(source),'output':str(output),'output_sha256':sha(output),'script_sha256':sha(Path(__file__)),'onnx_version':onnx.__version__,
 'policy':'Only Conv/ConvTranspose/LeakyRelu and /block residual Add FP16. Every original other node/constant and all flow/grid arithmetic remain byte-for-byte original FP32 operators.',
 'selected_neural_nodes':[n.name for n in original if selected(n)],'converted_weight_count':len(converted_weights),'input_cast_count':len(boundaries),'warp_fp32_ancestry':warp,
 'source_url':'https://github.com/AmusementClub/vs-mlrt/releases/download/external-models/rife_v2_v4.7z'}
(root/'reports/rife_neural_fp16_conversion.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k not in ('warp_fp32_ancestry','selected_neural_nodes')}))
