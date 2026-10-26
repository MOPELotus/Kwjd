import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cuda_rife import MODEL_SHA256, validate_model, validate_provider_profile


def gpu(op):
    return {'args': {'provider': 'CUDAExecutionProvider', 'op_name': op}}


def cpu(op='Reshape', shape=(4, 2)):
    return {'args': {'provider': 'CPUExecutionProvider', 'op_name': op,
        'input_type_shape': [{'int64': list(shape)}],
        'output_type_shape': [{'int64': list(shape)}]}}


def neural():
    return [gpu(op) for op in ('Conv', 'ConvTranspose', 'GridSample', 'Resize')]


class CudaRifeContractTests(unittest.TestCase):
    def test_changed_or_unpinned_model_rejected_before_runtime_import(self):
        cfg = {'rife_onnx': 'model.onnx', 'file_sha256': {'model.onnx': MODEL_SHA256}}
        with patch('cuda_rife.sha256', return_value='changed'):
            with self.assertRaises(ValueError): validate_model(cfg)
        with self.assertRaises(ValueError): validate_model({'rife_onnx': 'model.onnx'})
        with patch('cuda_rife.sha256', return_value=MODEL_SHA256):
            self.assertEqual(validate_model(cfg), 'model.onnx')

    def test_gpu_neural_with_tiny_cpu_metadata_accepted(self):
        self.assertIn('Conv', validate_provider_profile(neural() + [cpu(), cpu('Reciprocal', ())]))

    def test_cpu_neural_and_image_ops_rejected(self):
        for event in (cpu('Conv', (1,)), cpu('Reshape', (1, 3, 1080, 1920))):
            with self.subTest(event=event), self.assertRaises(ValueError):
                validate_provider_profile(neural() + [event])

    def test_missing_provider_evidence_rejected(self):
        with self.assertRaises(ValueError): validate_provider_profile([gpu('Conv')])
        with self.assertRaises(ValueError):
            validate_provider_profile(neural() + [{'args': {
                'provider': 'CPUExecutionProvider', 'op_name': 'Reshape'}}])


if __name__ == '__main__': unittest.main()
