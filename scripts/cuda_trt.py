"""Reviewed, strongly typed native TensorRT RIFE on physical Tesla T10.

Same borrowed float32 CUDA output contract as cuda_rife.CudaRife. Neural layers
use half precision; coordinates, flow, resize, warp and blend retain float32.
"""
from pathlib import Path
import json
import math
import subprocess

from enhance_stage import sha256

MODEL_SHA256 = '2888b81b22c2e7b5488a656dd9403cd86afc6a6eb126426962f54ffcbc72b7f4'
SOURCE_SHA256 = '6294aab9a9704f085caa65ab5bc897ac3a3ed0d054200e40b66bd7e43523e969'
ENGINE_SHA256 = '6d8fdb10f8505a863084f29b2f1ff74b9cf68aa6bd0245ad081492ab489f5f0b'
CONVERSION_SHA256 = 'ed9c8e6ef07a2845230a2b4b3cd5ba29ac86a1240544653afacbd1cbdc386440'
TRT_VERSION = '10.13.3.9'
DRIVER_VERSION = '580.178.04'


def validate_assets(cfg):
    """Fail closed before importing a GPU runtime or deserializing native code."""
    pins = cfg.get('file_sha256', {})
    def checked(path, expected=None):
        if not isinstance(path, str) or not pins.get(path):
            raise ValueError('All TRT assets and provenance evidence must be hash pinned')
        actual = sha256(path)
        if actual != pins[path] or (expected is not None and actual != expected):
            raise ValueError('Changed or unreviewed TRT asset: ' + path)
        return path
    engine = checked(cfg['rife_trt_engine'], ENGINE_SHA256)
    model = checked(cfg['rife_trt_model'], MODEL_SHA256)
    provenance = json.loads(Path(checked(cfg['rife_trt_provenance'])).read_text())
    expected = {'schema': 1, 'engine_sha256': ENGINE_SHA256,
        'typed_model_sha256': MODEL_SHA256, 'source_model_sha256': SOURCE_SHA256,
        'conversion_recipe_sha256': CONVERSION_SHA256, 'tensorrt_version': TRT_VERSION,
        'torch_version': '2.6.0+cu126', 'driver_version': DRIVER_VERSION,
        'compiled_gpu': 'Tesla T10', 'compute_capability': [7, 5],
        'physical_gpu_index': 1, 'strongly_typed': True,
        'input_shape': [1, 7, 1080, 1920], 'output_shape': [1, 3, 1080, 1920]}
    if any(type(provenance.get(k)) is not type(v) or provenance.get(k) != v
           for k, v in expected.items()):
        raise ValueError('TRT provenance does not bind the reviewed build')
    checked(provenance['source_model'], SOURCE_SHA256)
    checked(provenance['conversion_recipe'], CONVERSION_SHA256)
    for name in ('build_recipe', 'benchmark_report', 'conversion_report'):
        checked(provenance[name], provenance[name + '_sha256'])
    bench = json.loads(Path(provenance['benchmark_report']).read_text())
    if (bench.get('status') != 'completed' or bench.get('engine_sha256') != ENGINE_SHA256
            or bench.get('model_sha256') != MODEL_SHA256
            or bench.get('reference_sha256') != SOURCE_SHA256
            or bench.get('strongly_typed') is not True):
        raise ValueError('TRT benchmark does not attest this engine')
    if cfg.get('gpu') != 1 or type(cfg.get('gpu')) is not int or cfg.get('gpu_name') != 'Tesla T10':
        raise ValueError('Reviewed TRT production engine requires physical GPU1 Tesla T10')
    return engine, model, provenance


def audit_typed_model(path):
    """Independent CPU type audit; pinned graph plus strong typing binds precision."""
    import onnx
    model = onnx.shape_inference.infer_shapes(onnx.load(path), strict_mode=True)
    types = {v.name: v.type.tensor_type.elem_type
             for v in (*model.graph.input, *model.graph.output, *model.graph.value_info)}
    types.update({v.name: v.data_type for v in model.graph.initializer})
    warps = []; neural = []
    for node in model.graph.node:
        if node.op_type == 'GridSample':
            values = [types.get(v) for v in (*node.input, *node.output) if v]
            if not values or any(v != onnx.TensorProto.FLOAT for v in values):
                raise ValueError('TRT GridSample input/grid/output must remain float32')
            warps.append({'name': node.name, 'types': values})
        if node.op_type in ('Conv', 'ConvTranspose', 'LeakyRelu'):
            values = [types.get(v) for v in (*node.input, *node.output) if v]
            if not values or any(v != onnx.TensorProto.FLOAT16 for v in values):
                raise ValueError('TRT neural layers must use the reviewed float16 graph')
            neural.append({'name': node.name, 'types': values})
    if not warps or not neural:
        raise ValueError('Missing typed warp/neural graph evidence')
    return {'grid_sample_float32': warps, 'neural_float16': neural,
            'policy': 'Pinned conversion preserves original non-neural operators; strongly typed build respects graph tensor dtypes'}


class CudaRife:
    """Not thread safe; output workspace is overwritten by the next call."""
    def __init__(self, cfg):
        engine_path, model, provenance = validate_assets(cfg)
        dtype_audit = audit_typed_model(model)
        import torch
        import tensorrt as trt
        self.torch = torch
        self.device = torch.device('cuda:1')
        if (torch.__version__ != '2.6.0+cu126' or trt.__version__ != TRT_VERSION
                or not torch.cuda.is_available()):
            raise ValueError('Require pinned physical CUDA Torch and TensorRT runtime')
        torch.cuda.set_device(self.device)
        if (torch.cuda.get_device_name(self.device) != 'Tesla T10'
                or torch.cuda.get_device_capability(self.device) != (7, 5)):
            raise ValueError('TRT engine requires actual Tesla T10 sm75')
        actual = subprocess.check_output(['nvidia-smi', '-i', '1',
            '--query-gpu=name,driver_version', '--format=csv,noheader'], text=True).strip()
        if actual != 'Tesla T10, ' + DRIVER_VERSION:
            raise ValueError('TRT physical GPU/driver differs from reviewed build')
        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)
        self.engine = self.runtime.deserialize_cuda_engine(Path(engine_path).read_bytes())
        if self.engine is None:
            raise ValueError('Reviewed TRT engine failed deserialization')
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise ValueError('TRT execution context unavailable')
        names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        inputs = [n for n in names if self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT]
        outputs = [n for n in names if self.engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError('Unexpected TRT I/O count')
        for name, shape in ((inputs[0], (1, 7, 1080, 1920)), (outputs[0], (1, 3, 1080, 1920))):
            if (tuple(self.engine.get_tensor_shape(name)) != shape
                    or self.engine.get_tensor_dtype(name) != trt.float32):
                raise ValueError('TRT must expose fixed native-resolution float32 I/O')
        self._x = torch.empty((1, 7, 1080, 1920), device=self.device, dtype=torch.float32)
        self._y = torch.empty((1, 3, 1080, 1920), device=self.device, dtype=torch.float32)
        for name, tensor in ((inputs[0], self._x), (outputs[0], self._y)):
            if not self.context.set_tensor_address(name, tensor.data_ptr()):
                raise ValueError('TRT I/O binding failed')
        inspector = self.engine.create_engine_inspector()
        inspector.execution_context = self.context
        inspection = inspector.get_engine_information(trt.LayerInformationFormat.JSON)
        evidence = Path(cfg['rife_trt_provenance']).parent / ('inspector-' + ENGINE_SHA256[:16] + '.json')
        record = {'engine_sha256': ENGINE_SHA256, 'typed_onnx_audit': dtype_audit,
            'inspector_json': json.loads(inspection),
            'inspector_limitation': 'Engine built with default layer-name verbosity; dtype proof comes from pinned typed ONNX and hash-bound strongly typed build recipe'}
        if evidence.exists() and json.loads(evidence.read_text()) != record:
            raise ValueError('Existing TRT precision evidence differs; retained')
        if not evidence.exists():
            with evidence.open('x') as handle: json.dump(record, handle, indent=2)
        print(json.dumps({'rife': 'native-tensorrt-strongly-typed', 'engine_sha256': ENGINE_SHA256,
            'gpu': 'Tesla T10', 'logical_cuda_device': 1, 'tensorrt': trt.__version__,
            'precision_evidence': str(evidence), 'grid_sample_count': len(dtype_audit['grid_sample_float32']),
            'neural_half_count': len(dtype_audit['neural_float16'])}), flush=True)

    def interpolate(self, a, b, timestep):
        torch = self.torch
        if not math.isfinite(float(timestep)) or not 0 < float(timestep) < 1:
            raise ValueError('TRT RIFE timestep must be finite and strictly between 0 and 1')
        if any(not isinstance(v, torch.Tensor) or v.shape != (1, 3, 1080, 1920)
               or v.dtype != torch.float32 or v.device != self.device for v in (a, b)):
            raise ValueError('TRT inputs require matching native CUDA1 float32 N3HW RGB')
        with torch.inference_mode(), torch.cuda.device(self.device):
            self._x[:, :3].copy_(a)
            self._x[:, 3:6].copy_(b)
            self._x[:, 6].fill_(float(timestep))
            stream = torch.cuda.current_stream(self.device)
            if not self.context.execute_async_v3(stream.cuda_stream):
                raise RuntimeError('TRT CUDA execution failed; no fallback')
            # Finite check resolves work on this same stream, without a device-wide fence.
            if not torch.isfinite(self._y).all():
                raise ValueError('Nonfinite TRT RIFE output')
            return self._y.clamp_(0, 1)
