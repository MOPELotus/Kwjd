import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
from subtitle_review_supervisor import validate_episode,sha,save

class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.episode="S03E02"
        for name in ["input","output/drafts","ocr/subtitle-review/reports","ocr/subtitle-review/evidence"]:
            (self.root/name).mkdir(parents=True,exist_ok=True)
        self.source=self.root/"input/S03E02.mp4";self.source.write_bytes(b"synthetic source")
        self.draft=self.root/"output/drafts/S03E02.zh.srt"
        self.draft.write_text("1\n00:00:01,000 --> 00:00:02,000\nsynthetic\n",encoding="utf8")
        self.final=self.root/"output/S03E02.zh.srt";self.final.write_bytes(self.draft.read_bytes())
        self.seal=self.final.with_suffix(".srt.ready.json")
        save(self.seal,dict(reviewed=True,sha256=sha(self.final)))
        evidence=self.root/"ocr/subtitle-review/evidence/test.json"
        save(evidence,dict(scope="synthetic test index, not production review"))
        self.report=dict(complete=True,source_sha256=sha(self.source),draft_sha256=sha(self.draft),
            final_srt_sha256=sha(self.final),reviewed_original_cue_ids=[1],gap_audit_complete=True,
            boundary_audit_complete=True,short_line_audit_complete=True,two_line_audit_complete=True,
            open_issues=[],evidence=[dict(path=str(evidence),sha256=sha(evidence))],
            model_review_sessions=["synthetic-test-session"])
        self.path=self.root/"ocr/subtitle-review/reports/S03E02.json"

    def check(self):
        save(self.path,self.report)
        return validate_episode(self.root,self.episode)

    def test_only_consistent_schema_passes(self):
        self.assertTrue(self.check())
        self.report["reviewed_original_cue_ids"]=[]
        self.assertFalse(self.check())

    def test_matching_srt_and_seal_do_not_replace_review(self):
        self.assertFalse(validate_episode(self.root,self.episode))
        self.report["evidence"]=[]
        self.assertFalse(self.check())

    def test_unresolved_items_and_unchecked_short_lines_block(self):
        self.report["open_issues"]=["unclear"]
        self.assertFalse(self.check())
        self.report["open_issues"]=[]
        self.report["short_line_audit_complete"]=False
        self.assertFalse(self.check())

    def test_stale_source_and_stale_final_block(self):
        self.source.write_bytes(b"changed")
        self.assertFalse(self.check())
        self.report["source_sha256"]=sha(self.source)
        self.assertTrue(self.check())
        self.final.write_text("1\n00:00:01,000 --> 00:00:02,000\nchanged\n")
        self.assertFalse(self.check())

    def test_evidence_must_remain_inside_review_root(self):
        outside=self.root/"outside.json";outside.write_text("{}")
        self.report["evidence"]=[dict(path=str(outside),sha256=sha(outside))]
        self.assertFalse(self.check())

    def focused_report(self):
        for key in ['gap_audit_complete','boundary_audit_complete',
                    'short_line_audit_complete','two_line_audit_complete']:
            self.report.pop(key)
        self.report.update(review_policy='focused-v1',text_review_complete=True,
            timing_review_complete=True,targeted_review_complete=True,
            omitted_audits=['whole_episode_gap_allframes','whole_episode_short_allframes',
                'whole_episode_fullframe_sampling','independent_all_boundary_second_pass',
                'unchanged_text_second_pass'])

    def test_focused_review_accepts_explicit_user_waiver_without_fake_old_audits(self):
        self.focused_report()
        self.assertTrue(self.check())
        self.report['reviewed_original_cue_ids']=[]
        self.assertFalse(self.check())

    def test_focused_review_still_requires_timing_targeted_checks_and_resolved_issues(self):
        self.focused_report()
        for key in ['timing_review_complete','targeted_review_complete','text_review_complete']:
            self.report[key]=False;self.assertFalse(self.check());self.report[key]=True
        self.report['open_issues']=['known unresolved glyph']
        self.assertFalse(self.check())

    def test_unknown_policy_and_undisclosed_or_malformed_waivers_are_rejected(self):
        self.focused_report();self.report['review_policy']='skip-everything'
        self.assertFalse(self.check())
        self.report['review_policy']='focused-v1';self.report['omitted_audits'].pop()
        self.assertFalse(self.check())
        self.report['omitted_audits']=[{}]
        self.assertFalse(self.check())

if __name__=="__main__":
    unittest.main()

