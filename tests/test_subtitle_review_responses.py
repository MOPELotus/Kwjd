import importlib.util
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch
import urllib.error

P=Path(__file__).resolve().parents[1]/'scripts/subtitle_review_responses.py'
spec=importlib.util.spec_from_file_location('review_responses',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ResponsesReviewTest(unittest.TestCase):
    def row(self,cue=865):
        return dict(cue_id=cue,pixel_text='原字 空格',readable=True,timing_ok=True,issues=[],notes='source pixels')

    def test_actual_completed_message_required(self):
        good=dict(status='completed',id='resp_real',output=[dict(type='message',content=[dict(type='output_text',text='{"rows":[]}')])])
        self.assertEqual(m.output_text(good),{'rows':[]})
        for status in ['incomplete','failed','in_progress']:
            with self.assertRaises(ValueError):m.output_text(dict(good,status=status))
        with self.assertRaises(ValueError):m.output_text(dict(good,id=''))

    def test_missing_duplicate_reordered_cues_rejected(self):
        for rows in [[],[self.row(),self.row()],[self.row(866),self.row(865)]]:
            with self.assertRaises(ValueError):m.validate_rows({'rows':rows},[865,866])

    def test_explicit_types_and_multiline_rejected(self):
        for change in [dict(readable='true'),dict(timing_ok=1),dict(issues='none'),dict(pixel_text='a\nb'),dict(notes=None)]:
            with self.assertRaises(ValueError):m.validate_rows({'rows':[dict(self.row(),**change)]},[865])

    def test_uncertainty_preserved_not_turned_into_approval(self):
        row=dict(self.row(),readable=False,issues=['glyph unclear'])
        self.assertEqual(m.validate_rows({'rows':[row]},[865])[0]['issues'],['glyph unclear'])

    def test_text_and_spaces_preserved(self):
        self.assertEqual(m.validate_rows({'rows':[self.row()]},[865])[0]['pixel_text'],'原字 空格')

    def test_http_failure_has_no_secret_and_cannot_silently_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);image=root/'image.jpg';image.write_bytes(b'actual-fixture')
            index=dict(source_sha256='source',srt_sha256='draft')
            selected=[dict(cue_id=865,start_ms=0,end_ms=1000,draft_text='candidate',
                frames=[dict(path=str(image),sha256=m.sha(image),source_pts_seconds=.5)])]
            error=urllib.error.HTTPError('https://api.zhehentiaohe.cn/v1/responses',502,
                'SENTINEL_PRIVATE_KEY must not be logged',{},None)
            with patch.object(m.urllib.request,'urlopen',side_effect=error) as network:
                with self.assertRaises(RuntimeError):
                    m.request_review(index,selected,root/'attempt','https://api.zhehentiaohe.cn/v1/responses',
                        'SENTINEL_PRIVATE_KEY','gpt-6.1-sol','medium',10)
                with self.assertRaises(ValueError):
                    m.request_review(index,selected,root/'attempt','https://api.zhehentiaohe.cn/v1/responses',
                        'SENTINEL_PRIVATE_KEY','gpt-6.1-sol','medium',10)
                self.assertEqual(network.call_count,1)
            for path in root.rglob('*.json'):
                self.assertNotIn('SENTINEL_PRIVATE_KEY',path.read_text())


if __name__=='__main__':unittest.main()
