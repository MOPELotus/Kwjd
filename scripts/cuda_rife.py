"""Persistent official RIFE v4.6 ONNX on CUDA, with GPU tensor I/O.

The first real inference audits ORT provider assignment. Only tiny scalar/shape
operations may use CPU; image processing and the neural network must use CUDA.
The returned tensor is a borrowed workspace, overwritten by the next call.
"""
from pathlib import Path
import json
import math
import tempfile

from enhance_stage import sha256

MODEL_SHA256 = '6294aab9a9704f085caa65ab5bc897ac3a3ed0d054200e40b66bd7e43523e969'
MODEL_SOURCE = 'https://github.com/AmusementClub/vs-mlrt/releases/download/external-models/rife_v2_v4.7z'
ARCHIVE_SHA256 = 'c9999d6af6800365f4f3d716dbaa51f5954e05f38424f83b705b055d754c71cc'
_CPU_SHAPE_OPS = frozenset(('Gather', 'Mod', 'Sub', 'Neg', 'Unsqueeze', 'Concat',
    'Reshape', 'Slice', 'Transpose', 'Cast', 'Reciprocal', 'Mul', 'Expand',
    'ScatterND', 'Equal', 'Where', 'Shape'))
_REQUIRED_GPU_OPS = frozenset(('Conv', 'ConvTranspose', 'GridSample', 'Resize'))


def validate_model(cfg):
    path = cfg['rife_onnx']
    if cfg.get('file_sha256', {}).get(path) != MODEL_SHA256:
        raise ValueError('RIFE must pin the verified official v4.6 ONNX SHA256')
    if sha256(path) != MODEL_SHA256:
        raise ValueError('RIFE ONNX differs from the verified official model')
    return path


def validate_provider_profile(events):
    """Reject any CPU image tensor/neural op, allowing only tiny shape work."""
    gpu_ops = set()
    for event in events:
        args = event.get('args', {})
        provider = args.get('provider')
        if not provider:
            continue
        op = args.get('op_name')
        if provider == 'CUDAExecutionProvider':
            gpu_ops.add(op)
            continue
        if provider != 'CPUExecutionProvider' or op not in _CPU_SHAPE_OPS:
            raise ValueError('RIFE operator fell back from CUDA: ' + str(op))
        for key in ('input_type_shape', 'output_type_shape'):
            shapes = args.get(key)
            if shapes is None:
                raise ValueError('Missing CPU shape evidence in RIFE provider profile')
            for typed_shape in shapes:
                for dims in typed_shape.values():
                    if any(not isinstance(d, int) or d < 0 for d in dims) or math.prod(dims) > 8:
                        raise ValueError('CPU image processing forbidden in RIFE: ' + str(op))
    if not _REQUIRED_GPU_OPS <= gpu_ops:
        raise ValueError('RIFE profile did not prove all required neural operators on CUDA')
    return sorted(gpu_ops)


class CudaRife:
    """Not thread safe. Inputs are CUDA float32 RGB tensors in [0, 1]."""
    def __init__(self, cfg):
        model = validate_model(cfg)
        import torch
        import onnxruntime as ort
        import numpy as np
        self.torch = torch
        self.np = np
        self.device = torch.device('cuda:' + str(cfg['gpu']))
        if not torch.cuda.is_available():
            raise ValueError('Physical CUDA is required for RIFE; no CPU fallback')
        torch.cuda.set_device(self.device)
        if torch.cuda.get_device_name(self.device) != cfg['gpu_name']:
            raise ValueError('RIFE CUDA device differs from the verified physical GPU')
        if torch.__version__ != '2.6.0+cu126' or ort.__version__ != '1.23.2':
            raise ValueError('Unexpected isolated CUDA RIFE runtime version')
        if 'CUDAExecutionProvider' not in ort.get_available_providers():
            raise ValueError('ONNX Runtime CUDA provider is unavailable')
        ort.preload_dlls()
        self._profile_dir = tempfile.TemporaryDirectory(prefix='kwjd-rife-profile-')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(self._profile_dir.name) / 'provider')
        self.session = ort.InferenceSession(model, sess_options=options, providers=[
            ('CUDAExecutionProvider', {'device_id': self.device.index,
                'cudnn_conv_algo_search': 'HEURISTIC', 'use_tf32': 0})])
        if self.session.get_providers()[0] != 'CUDAExecutionProvider':
            raise ValueError('RIFE session failed to activate CUDA')
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (len(inputs) != 1 or inputs[0].name != 'input' or inputs[0].type != 'tensor(float)'
                or len(outputs) != 1 or outputs[0].name != 'output'
                or outputs[0].type != 'tensor(float)'):
            raise ValueError('Unexpected pinned RIFE model interface')
        self._x = self._y = self._io = None
        self.provider_verified = False
        self._failure = None
        print(json.dumps({'rife': 'official-v4.6-onnx-v2', 'model_sha256': MODEL_SHA256,
            'source': MODEL_SOURCE, 'onnxruntime': ort.__version__,
            'gpu': torch.cuda.get_device_name(self.device),
            'logical_cuda_device': self.device.index, 'fp32': True}), flush=True)

    def interpolate(self, a, b, timestep):
        """Return borrowed CUDA N3HW output; caller clones it to retain it."""
        if self._failure:
            raise ValueError('RIFE session blocked after provider validation failure') from self._failure
        torch = self.torch
        if not math.isfinite(float(timestep)) or not 0 < float(timestep) < 1:
            raise ValueError('RIFE timestep must be finite and strictly between 0 and 1')
        if (not isinstance(a, torch.Tensor) or not isinstance(b, torch.Tensor)
                or a.shape != b.shape or a.ndim != 4 or a.shape[1] != 3
                or a.shape[0] < 1 or a.shape[2] < 1 or a.shape[3] < 1
                or a.dtype != torch.float32 or b.dtype != torch.float32
                or a.device != self.device or b.device != self.device):
            raise ValueError('RIFE inputs must be matching CUDA float32 N3HW RGB tensors')
        shape = tuple(a.shape)
        with torch.inference_mode():
            if self._y is None or tuple(self._y.shape) != shape:
                self._x = torch.empty((shape[0], 7, shape[2], shape[3]),
                                      device=self.device, dtype=torch.float32)
                self._y = torch.empty(shape, device=self.device, dtype=torch.float32)
                self._io = self.session.io_binding()
                self._io.bind_input('input', 'cuda', self.device.index, self.np.float32,
                                    tuple(self._x.shape), self._x.data_ptr())
                self._io.bind_output('output', 'cuda', self.device.index, self.np.float32,
                                     shape, self._y.data_ptr())
            self._x[:, :3].copy_(a)
            self._x[:, 3:6].copy_(b)
            self._x[:, 6].fill_(float(timestep))
            # ORT owns its stream; explicit fences also support caller nondefault streams.
            torch.cuda.synchronize(self.device)
            self.session.run_with_iobinding(self._io)
            torch.cuda.synchronize(self.device)
            if not self.provider_verified:
                try:
                    profile = self.session.end_profiling()
                    gpu_ops = validate_provider_profile(json.loads(Path(profile).read_text()))
                    self.provider_verified = True
                    print(json.dumps({'rife_provider_verified': True, 'cuda_ops': gpu_ops,
                        'cpu_allowed': 'shape/scalar only, at most 8 elements'}), flush=True)
                except Exception as error:
                    self._failure = error
                    raise
                finally:
                    self._profile_dir.cleanup()
            if not torch.isfinite(self._y).all():
                raise ValueError('Nonfinite CUDA RIFE output')
            return self._y.clamp_(0, 1)
