import importlib.util
import os
import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runner', Path(__file__).parents[1] / 'scripts/run_subtitle_omp.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class IsolationTests(unittest.TestCase):
    def test_other_lanes_progress_cannot_count_as_this_lanes_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'ocr/subtitle-review';base.mkdir(parents=True)
            for ep,count in [('S03E11',976),('S03E12',16)]:
                (base/f'progress-{ep}.json').write_text(json.dumps(dict(complete=False,
                    text_reviewed_original_cue_ids=list(range(count)))))
            self.assertEqual(runner.checkpoint(root,['S03E11']),[('S03E11',False,976)])

    def test_overlapping_episode_assignments_are_exclusive(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as first:
            area=Path(directory)
            runner.acquire_episodes(first,area,['S03E11','S03E13'])
            with ExitStack() as second:
                with self.assertRaises(BlockingIOError):
                    runner.acquire_episodes(second,area,['S03E11'])
            with ExitStack() as second:
                runner.acquire_episodes(second,area,['S03E12','S03E14'])

    def test_default_image_budget_cannot_silently_truncate_subtitle_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = dict(id='gpt-6.1-sol',input=['text','image'])
            config = {'providers':{'jigong-relay':dict(baseUrl='https://api.zhehentiaohe.cn/v1',
                api='openai-responses',apiKey='JIGONG_RESPONSES_API_KEY',models=[model])}}
            path = root/'models.yml';path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):runner.validate_configuration(root)
            model['imageCountBudget']=32;path.write_text(json.dumps(config))
            runner.validate_configuration(root)

    def test_provider_auth_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text('test-key')
            key.chmod(0o600)
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'wrong', 'CODEX_HOME': '/wrong',
                                        'ANTHROPIC_API_KEY': 'wrong', 'OMP_PROFILE': 'wrong'}):
                env = runner.child_environment(key, Path(directory) / 'agent')
            self.assertEqual(env['JIGONG_RESPONSES_API_KEY'], 'test-key')
            for name in ['OPENAI_API_KEY', 'CODEX_HOME', 'ANTHROPIC_API_KEY', 'OMP_PROFILE']:
                self.assertNotIn(name, env)

    def test_public_or_empty_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text('')
            key.chmod(0o600)
            with self.assertRaises(ValueError):
                runner.child_environment(key, Path(directory))
            key.write_text('test-key')
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                runner.child_environment(key, Path(directory))

    def test_fixed_external_model_and_bounded_tools(self):
        args = runner.command(Path('/bin/omp'), Path('/data'), Path('/sessions'), 'continue', True)
        self.assertEqual(args[args.index('--model') + 1], 'jigong-relay/gpt-6.1-sol')
        self.assertIn('--continue', args)
        self.assertEqual(args[args.index('--tools') + 1], 'read,bash,edit,write')
        self.assertNotIn('--api-key', args)


if __name__ == '__main__':
    unittest.main()
