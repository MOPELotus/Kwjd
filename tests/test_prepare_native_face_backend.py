import importlib.machinery
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_native_face_backend as prepare
from native_face_pipeline import pipeline_options


class PrepareNativePolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.post = Path(temporary.name)
        (self.post / 'backend').mkdir()
        self.asset = self.post / 'asset'
        self.asset.write_bytes(b'fake pinned model asset')
        self.base = dict(rife_backend='cuda_trt', rife_trt_engine=str(self.asset),
                         rife_trt_model=str(self.asset), rife_trt_provenance=str(self.asset),
                         rife_onnx=str(self.asset), rife_precision='float32',
                         pipeline_prefetch=False, pipeline_prefetch_frames=2,
                         resource_wait_in_process=True, resource_wait_poll_seconds=2,
                         resource_wait_timeout_seconds=30,
                         benchmark_native_frame_sha256=True,
                         native_recipe='old recipe', native_recipe_sha256='old sha',
                         unrelated_quality_contract='unchanged',
                         audio_codec='flac', audio_bits=24, audio_sample_rate=48000)
        (self.post / 'backend/runtime.trt.json').write_text(json.dumps(self.base))
        weights = self.post / 'tools/face-candidates/weights'
        weights.mkdir(parents=True)
        model = weights / 'official.pth'
        model.write_bytes(b'fake official weight')
        self.expected_weights = {model.name: prepare.sha256(model)}
        self.provenance = {name: str(self.asset) for name in
                           ('source_model', 'conversion_recipe', 'build_recipe',
                            'benchmark_report', 'conversion_report')}
        package = self.post / 'fake-package'
        package.mkdir()
        (package / '__init__.py').write_text('# fake package source\n')
        spec = importlib.machinery.ModuleSpec('fake', loader=None, is_package=True)
        spec.submodule_search_locations = [str(package)]
        for mock in (patch.object(prepare, 'validate_assets', return_value=(None, None, self.provenance)),
                     patch.object(prepare, 'WEIGHTS', self.expected_weights),
                     patch.object(prepare.importlib.metadata, 'version', return_value='test-version'),
                     patch.object(prepare.importlib.util, 'find_spec', return_value=spec)):
            mock.start()
            self.addCleanup(mock.stop)

    def test_formal_default_profile_is_explicit_bounded_and_hashing_disabled(self):
        self.assertEqual(prepare.PRODUCTION_SCHEDULING_PROFILE, 'overlap1-legacy')
        self.assertEqual(prepare.scheduling_profile(), {
            'pipeline_prefetch': True, 'pipeline_prefetch_frames': 1,
            'resource_wait_in_process': False, 'resource_wait_poll_seconds': 1.5,
            'resource_wait_timeout_seconds': 120., 'benchmark_native_frame_sha256': False})
        profile = prepare.scheduling_profile()
        profile['pipeline_prefetch_frames'] = 2
        self.assertEqual(prepare.scheduling_profile()['pipeline_prefetch_frames'], 1)
        with self.assertRaises(ValueError):
            prepare.scheduling_profile('unknown')

    def test_standalone_profiles_keep_serial_gate_independent_from_overlap(self):
        expected = {'serial': (False, False, 1), 'serial-gate': (False, True, 1),
                    'overlap1-legacy': (True, False, 1),
                    'overlap1-gate': (True, True, 1), 'overlap2-gate': (True, True, 2)}
        self.assertEqual(set(prepare.SCHEDULING_PROFILES), set(expected))
        for name, (prefetch, gate, frames) in expected.items():
            with self.subTest(profile=name):
                cfg = prepare.scheduling_profile(name)
                self.assertEqual((cfg['pipeline_prefetch'], cfg['resource_wait_in_process'],
                                  cfg['pipeline_prefetch_frames']), (prefetch, gate, frames))
                self.assertFalse(cfg['benchmark_native_frame_sha256'])
                self.assertEqual(pipeline_options(cfg)['thermal_timeout'], 120.)

    def test_template_overrides_stale_scheduling_but_retains_quality_and_pins(self):
        cfg = prepare.template(self.post)
        for key, expected in prepare.scheduling_profile().items():
            self.assertEqual(cfg[key], expected)
        for key in ('rife_backend', 'rife_trt_engine', 'rife_trt_model',
                    'rife_trt_provenance', 'rife_onnx', 'rife_precision',
                    'unrelated_quality_contract', 'audio_codec', 'audio_bits',
                    'audio_sample_rate'):
            self.assertEqual(cfg[key], self.base[key])
        self.assertEqual(cfg['light_filter'], 'clarity')
        self.assertEqual(cfg['max_video_mbps'], 16)
        self.assertEqual(cfg['segment_source_frames'], 250)
        self.assertEqual(cfg['restoration_backend'], 'cuda_gfpgan_fused')
        self.assertEqual(cfg['restore_gpu'], 0)
        self.assertNotIn('native_recipe', cfg)
        self.assertNotIn('native_recipe_sha256', cfg)
        for name in ('native_frame_queue.py', 'native_resource_gate.py',
                     'native_face_pipeline.py', 'prepare_native_face_backend.py'):
            source = Path(prepare.__file__).with_name(name).resolve()
            self.assertEqual(cfg['file_sha256'][str(source)], prepare.sha256(source))

    def test_prepare_archives_legacy_template_without_activating_or_rebinding(self):
        template = self.post / 'backend/runtime.native-template.json'
        template.write_text(json.dumps({'pipeline_prefetch': False, 'legacy_source': 'pinned'}))
        old_bytes, old_sha = template.read_bytes(), prepare.sha256(template)
        active = self.post / 'backend/runtime.native-active.json'
        active.write_bytes(b'legacy active runtime must stay exact')
        launcher = self.post / 'backend/enhance.sh'
        launcher.write_bytes(b'legacy production launcher must stay exact')
        result = prepare.prepare(self.post)
        self.assertFalse(result['activated'])
        self.assertEqual(active.read_bytes(), b'legacy active runtime must stay exact')
        self.assertEqual(launcher.read_bytes(), b'legacy production launcher must stay exact')
        self.assertEqual((self.post / 'backend/native-template-history' / (old_sha + '.json')).read_bytes(), old_bytes)
        self.assertTrue(json.loads(template.read_text())['pipeline_prefetch'])
        self.assertEqual(json.loads(template.read_text())['pipeline_prefetch_frames'], 1)
        self.assertFalse(json.loads(template.read_text())['resource_wait_in_process'])
        self.assertFalse((self.post / 'backend/native-enabled.json').exists())
        # Re-preparation must preserve the archived legacy identity and template.
        self.assertEqual(prepare.prepare(self.post), result)
        self.assertEqual(len(list((self.post / 'backend/native-template-history').glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
