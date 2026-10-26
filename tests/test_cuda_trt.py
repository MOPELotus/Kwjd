import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cuda_trt import (validate_assets, ENGINE_SHA256, MODEL_SHA256, SOURCE_SHA256,
                      CONVERSION_SHA256, TRT_VERSION, DRIVER_VERSION)


class TensorRTAssetContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.provenance_path = str(root / 'provenance.json')
        self.bench_path = str(root / 'bench.json')
        self.provenance = {'schema': 1, 'engine_sha256': ENGINE_SHA256,
            'typed_model_sha256': MODEL_SHA256, 'source_model_sha256': SOURCE_SHA256,
            'conversion_recipe_sha256': CONVERSION_SHA256, 'tensorrt_version': TRT_VERSION,
            'torch_version': '2.6.0+cu126', 'driver_version': DRIVER_VERSION,
            'compiled_gpu': 'Tesla T10', 'compute_capability': [7, 5],
            'physical_gpu_index': 1, 'strongly_typed': True,
            'input_shape': [1, 7, 1080, 1920], 'output_shape': [1, 3, 1080, 1920],
            'source_model': 'source', 'conversion_recipe': 'conversion',
            'build_recipe': 'build', 'build_recipe_sha256': 'buildhash',
            'conversion_report': 'conversionreport', 'conversion_report_sha256': 'conversionreporthash',
            'benchmark_report': self.bench_path, 'benchmark_report_sha256': 'benchhash'}
        self.bench = {'status': 'completed', 'engine_sha256': ENGINE_SHA256,
            'model_sha256': MODEL_SHA256, 'reference_sha256': SOURCE_SHA256,
            'strongly_typed': True}
        pins = {'engine': ENGINE_SHA256, 'model': MODEL_SHA256, 'source': SOURCE_SHA256,
            'conversion': CONVERSION_SHA256, 'build': 'buildhash',
            'conversionreport': 'conversionreporthash', self.bench_path: 'benchhash',
            self.provenance_path: 'provenancehash'}
        self.cfg = {'gpu': 1, 'gpu_name': 'Tesla T10', 'rife_trt_engine': 'engine',
            'rife_trt_model': 'model', 'rife_trt_provenance': self.provenance_path,
            'file_sha256': pins}
        self.hashes = dict(pins)
        self.hash_patch = patch('cuda_trt.sha256', side_effect=lambda path: self.hashes[path])
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def validate(self):
        Path(self.provenance_path).write_text(json.dumps(self.provenance))
        Path(self.bench_path).write_text(json.dumps(self.bench))
        return validate_assets(self.cfg)

    def test_exact_reviewed_assets_are_accepted_without_gpu_import(self):
        self.assertEqual(self.validate()[:2], ('engine', 'model'))

    def test_changed_asset_is_rejected_even_if_provenance_was_valid(self):
        self.hashes['engine'] = 'changed'
        with self.assertRaises(ValueError): self.validate()

    def test_arbitrary_engine_with_matching_config_pin_is_still_unreviewed(self):
        self.hashes['engine'] = self.cfg['file_sha256']['engine'] = 'anotherengine'
        self.provenance['engine_sha256'] = 'anotherengine'
        with self.assertRaises(ValueError): self.validate()

    def test_all_supporting_evidence_must_be_pinned(self):
        for name in ('source', 'conversion', 'build', 'conversionreport', self.bench_path, self.provenance_path):
            with self.subTest(name=name):
                removed = self.cfg['file_sha256'].pop(name)
                with self.assertRaises(ValueError): self.validate()
                self.cfg['file_sha256'][name] = removed

    def test_wrong_precision_hardware_driver_or_shape_fails_closed(self):
        for key, value in [('strongly_typed', False), ('physical_gpu_index', 0),
                           ('driver_version', 'different'), ('compute_capability', [8, 6]),
                           ('input_shape', [1, 7, 720, 1280])]:
            with self.subTest(key=key):
                previous = self.provenance[key]
                self.provenance[key] = value
                with self.assertRaises(ValueError): self.validate()
                self.provenance[key] = previous

    def test_benchmark_for_other_engine_is_not_accepted(self):
        self.bench['engine_sha256'] = 'other'
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_runtime_gpu_config_rejected(self):
        for gpu in (0, True):
            self.cfg['gpu'] = gpu
            with self.assertRaises(ValueError): self.validate()


if __name__ == '__main__': unittest.main()
