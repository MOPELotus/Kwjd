import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('journal', Path(__file__).resolve().parents[1] / 'scripts/subtitle_review_journal.py')
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.draft = self.root / 'output/drafts/S03E02.zh.srt'
        self.draft.parent.mkdir(parents=True)
        self.draft.write_text('1\n00:00:01,000 --> 00:00:02,000\nfixture one\n\n2\n00:00:02,000 --> 00:00:03,000\nfixture two\n')
        self.review = self.root / 'ocr/subtitle-review'
        self.path = self.review / 'decisions/S03E02/0000.json'
        self.path.parent.mkdir(parents=True)
        self.evidence = self.review / 'evidence.json'
        self.evidence.write_text('{"fixture": true}')
        self.data = dict(draft_sha256=journal.sha(self.draft), model_review_session='fixture-session',
                         evidence=[dict(path=str(self.evidence), sha256=journal.sha(self.evidence))],
                         rows=[dict(original_cue_id=1, original_text='fixture one', frames_viewed=['fixture'],
                                    segments=[])])

    def write(self):
        self.path.write_text(json.dumps(self.data))

    def test_deletion_preserves_unreviewed_cue_and_draft(self):
        initial = self.draft.read_bytes()
        self.write()
        result = journal.rebuild(self.root, 'S03E02')
        self.assertFalse(result['complete'])
        self.assertEqual(result['recorded_original_cue_ids'], [1])
        self.assertEqual(journal.read_srt(Path(result['working_srt'])), [(2000, 3000, 'fixture two')])
        self.assertEqual(self.draft.read_bytes(), initial)
        self.assertFalse((self.root / 'output/S03E02.zh.srt').exists())

    def test_changed_evidence_rejected(self):
        self.write()
        self.evidence.write_text('changed')
        with self.assertRaises(ValueError):
            journal.rebuild(self.root, 'S03E02')

    def test_unseen_cue_rejected(self):
        self.data['rows'][0]['frames_viewed'] = []
        self.write()
        with self.assertRaises(ValueError):
            journal.rebuild(self.root, 'S03E02')
