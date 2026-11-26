import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('status_view', Path(__file__).parents[1] / 'scripts/subtitle_omp_status.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class StatusTests(unittest.TestCase):
    def test_parallel_lanes_use_independent_episode_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);base=root/'ocr/subtitle-review';base.mkdir(parents=True)
            (root/'.kwjd').mkdir()
            workers=[]
            for name,ep,count in [('main','S03E11',976),('lane-b','S03E12',16)]:
                status=root/f'{name}.json';status.write_text('{"state":"RUNNING"}')
                workers.append(dict(id=name,episodes=[ep],status_path=str(status)))
                (base/f'progress-{ep}.json').write_text(json.dumps(dict(episode=ep,
                    total_original_cues=1600,text_reviewed_original_cue_ids=list(range(count)))))
            (root/'.kwjd/subtitle-executor.json').write_text(json.dumps(dict(workers=workers)))
            result=m.render(root,[])
            self.assertIn('并行审校：2路',result)
            self.assertIn('main｜S03E11｜976/1600',result)
            self.assertIn('lane-b｜S03E12｜16/1600',result)

    def test_payloads_and_commands_are_not_rendered(self):
        event = dict(type='tool_execution_start', toolName='bash', args=dict(command='secret-command'))
        self.assertNotIn('secret-command', m.describe(event))
        self.assertIsNone(m.describe(dict(type='message_end', message={'content': 'private image payload'})))

    def test_partial_lines_and_changed_log_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'log.jsonl'
            e = json.dumps(dict(type='tool_execution_start', toolName='read', args=dict(path='page-middle-001.jpg')))
            p.write_text(e[:20]); reader = m.Events()
            self.assertEqual(reader.update(p), [])
            with p.open('a') as f:
                f.write(e[20:] + '\n')
            self.assertEqual(len(reader.update(p)), 1)
            self.assertEqual(reader.update(p), [])
            q = Path(temp) / 'new.jsonl';q.write_text(e+'\n')
            self.assertEqual(len(reader.update(q)), 1)

    def test_saved_cues_are_counted_separately_from_completed_chapters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);base=root/'ocr/subtitle-review';base.mkdir(parents=True)
            (base/'progress.json').write_text(json.dumps(dict(episode='S03E11', total_original_cues=1600,
                text_reviewed_original_cue_ids=list(range(1,801)))))
            (base/'progress-S03E02.json').write_text('{"complete":false}')
            out=root/'output';(out/'ass').mkdir(parents=True)
            (base/'reports').mkdir()
            srt=out/'S03E02.zh.srt';srt.write_bytes(b'sealed-caption')
            ass=out/'ass/S03E02.ass';ass.write_bytes(b'styled-caption')
            srt_sha=hashlib.sha256(srt.read_bytes()).hexdigest()
            seal=srt.with_suffix('.srt.ready.json');seal.write_text(json.dumps(dict(reviewed=True,sha256=srt_sha)))
            ass.with_suffix('.ass.ready.json').write_text(json.dumps(dict(state='ASS_STYLE_READY_REFERENCE_CLOCK',
                srt_sha256=srt_sha,srt_seal_sha256=hashlib.sha256(seal.read_bytes()).hexdigest(),
                ass_sha256=hashlib.sha256(ass.read_bytes()).hexdigest())))
            (base/'reports/S03E02.json').write_text(json.dumps(dict(complete=True,final_srt_sha256=srt_sha)))
            result=m.render(root,[])
            self.assertIn('第10回',result)
            self.assertIn('800/1600条 · 50.0%',result)
            self.assertIn('1/13',result)
            ass.write_bytes(b'changed')
            self.assertEqual(m.sealed_count(root),0)


if __name__ == '__main__':
    unittest.main()
