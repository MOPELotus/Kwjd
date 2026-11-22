"""Calibration may exercise the real chain without minting production seals."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from enhance_stage import sha256
from native_face_recipe import validate_recipe,check_calibration_run
from segmented_backend import context


class TechnicalCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.post=Path(self.temp.name)/'post'
        self.master='a'*64
        self.area=self.post/'reports/calibration/S03E02'/self.master/'trial1'
        self.area.mkdir(parents=True)
        fonts=self.post/'styles/fonts';fonts.mkdir(parents=True)
        (fonts/'font.ttf').write_bytes(b'font')
        self.data=dict(schema_version=1,scope='technical_calibration',episode='S03E02',
            master_sha256=self.master,post_root=str(self.post),fonts_path=str(fonts),
            fonts={'font.ttf':sha256(fonts/'font.ttf')},production_eligible=False,
            source_start=1500,source_frames=900,program_origin_seconds=0,
            calibration_directory=str(self.area))
        for key in ['srt','ass','style']:
            path=self.area/(key+'.txt');path.write_text('temporary preview')
            self.data[key+'_path']=str(path);self.data[key+'_sha256']=sha256(path)
        evidence=self.area/'pixels.json'
        evidence.write_text(json.dumps(dict(master_sha256=self.master,pixel_checked=True,temporary_not_final=True)))
        self.data.update(pixel_review_evidence_path=str(evidence),pixel_review_evidence_sha256=sha256(evidence))
        self.path=self.area/'recipe.json'

    def write(self):
        self.path.write_text(json.dumps(self.data));return sha256(self.path)

    def test_no_seals_created_and_segmented_production_refuses(self):
        digest=self.write();data=validate_recipe(self.path,digest)
        check_calibration_run(data,self.area/'sample.mkv',1500,900)
        self.assertFalse(list(self.post.rglob('*.ready.json')))
        cfg=dict(restoration_backend='cuda_gfpgan_fused',native_recipe=str(self.path),native_recipe_sha256=digest)
        with self.assertRaisesRegex(ValueError,'cannot enter segmented production'):
            context(self.post/'absent-master',self.post/'final.mkv','S03E02',cfg)

    def test_output_and_frame_bounds_enforced_before_gpu_work(self):
        data=validate_recipe(self.path,self.write())
        for output,start,count in [(self.post/'final.mkv',1500,900),(self.area/'x.mkv',1505,900),
                                   (self.area/'x.mkv',1500,None),(self.area/'x.mkv',1500,901)]:
            with self.assertRaises(ValueError):check_calibration_run(data,output,start,count)
        for value in [0,1801,True,None]:
            self.data['source_frames']=value
            with self.assertRaises(ValueError):validate_recipe(self.path,self.write())

    def test_no_scope_upgrade_or_changed_pixel_evidence(self):
        self.data['scope']='production'
        with self.assertRaises(FileNotFoundError):validate_recipe(self.path,self.write())
        self.data['scope']='technical_calibration';self.data['pixel_review_evidence_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'pixel review'):validate_recipe(self.path,self.write())


if __name__=='__main__':unittest.main()
