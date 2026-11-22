import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from subtitle_review_ass import style_assets,WaitStyle,WaitReview,ensure_ass,sha
from subtitle_review_asr import validate_interval

class ReviewStyleContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.post=self.root/"post"
        self.styles=self.post/"styles";(self.styles/"fonts").mkdir(parents=True)
        self.template=self.styles/"template.ass";self.template.write_text("synthetic contract fixture")
        self.font=self.styles/"fonts/test.ttf";self.font.write_bytes(b"synthetic font fixture, not glyph proof")
        self.selection=self.post/"font-selection.json";self.selection.write_text("{}")
        self.preview=self.post/"preview.png";self.preview.write_bytes(b"synthetic preview contract")
        self.proof=dict(schema_version=1,technical_checked=True,visual_checked=True,
            template_path=str(self.template),template_sha256=sha(self.template),
            fonts={"test.ttf":sha(self.font)},font_selection_evidence_path=str(self.selection),
            font_selection_evidence_sha256=sha(self.selection),preview_evidence_path=str(self.preview),
            preview_evidence_sha256=sha(self.preview),master_sha256="a"*64,
            authorized_design=dict(family="MiSans Subtitle",size=84,variation_weight=450,
                                   outline=3.5,shadow=1,margin_v=54))
        self.path=self.styles/"style-preflight.json"
    def write(self):
        self.path.write_text(json.dumps(self.proof))
    def test_missing_and_unverified_proof_waits(self):
        with self.assertRaises(WaitStyle):style_assets(self.post)
        self.proof["visual_checked"]=False;self.write()
        with self.assertRaises(WaitStyle):style_assets(self.post)
    def test_schema_binding_not_actual_font_render_test(self):
        self.write();template,_,_=style_assets(self.post)
        self.assertEqual(template,self.template)
    def test_wrong_authorized_design_rejected(self):
        self.proof["authorized_design"]["size"]=85;self.write()
        with self.assertRaises(ValueError):style_assets(self.post)
    def test_changed_font_and_evidence_rejected(self):
        self.write();self.font.write_bytes(b"changed")
        with self.assertRaises(ValueError):style_assets(self.post)
        self.proof["fonts"]["test.ttf"]=sha(self.font);self.write()
        self.preview.write_bytes(b"changed")
        with self.assertRaises(ValueError):style_assets(self.post)
    def test_no_real_review_never_produces_ass(self):
        self.write()
        with self.assertRaises(WaitReview):ensure_ass(self.root,"S03E02")
        self.assertFalse((self.root/"output/ass/S03E02.ass").exists())

class AudioAuxiliaryBoundsTests(unittest.TestCase):
    def test_valid_bounded_interval(self):
        validate_interval(9,20);validate_interval(0,120)
    def test_bad_or_unbounded_intervals_rejected(self):
        for start,end in [(0,121),(-1,2),(2,2),(3,2),(float("nan"),1),(0,float("inf"))]:
            with self.subTest(interval=(start,end)),self.assertRaises(ValueError):
                validate_interval(start,end)

if __name__=="__main__":unittest.main()

