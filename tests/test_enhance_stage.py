import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import enhance_stage as stage
import server_worker as worker


def media(fps=60, seconds=3):
    return {'format':{'duration':str(seconds)}, 'streams':[
        {'codec_type':'video','width':1920,'height':1080,
         'avg_frame_rate':f'{fps}/1','r_frame_rate':f'{fps}/1'}]}


class StageTests(unittest.TestCase):
    def test_checkpoint_reuse_and_changed_master_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master.mkv';out=root/'out.mkv';master.write_bytes(b'original')
            def encode(args, **kwargs):Path(args[2]).write_bytes(b'enhanced-video')
            with patch.object(stage,'probe',side_effect=lambda p:media(25 if Path(p)==master else 60)), \
                 patch.object(stage.subprocess,'check_output',return_value=json.dumps({'additional_peak_bytes':10,'backend_fingerprint':'modelsha'})), \
                 patch.object(stage,'check_space'),patch.object(stage.subprocess,'run',side_effect=encode) as run:
                receipt=stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(receipt['stage'],'ENHANCED_READY');self.assertEqual(run.call_count,1)
                stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(run.call_count,1)
                master.write_bytes(b'different')
                with self.assertRaisesRegex(ValueError,'hash differs'):
                    stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
                self.assertEqual(out.read_bytes(),b'enhanced-video')

    def test_orphan_output_never_trusted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);master=root/'master';out=root/'out';master.write_bytes(b'x');out.write_bytes(b'orphan')
            with patch.object(stage,'probe',return_value=media()),self.assertRaisesRegex(ValueError,'no verified checkpoint'):
                stage.ensure_enhanced(master,out,root/'backend','S01E49',root/'log')
            self.assertEqual(out.read_bytes(),b'orphan')

    def test_reserve_and_wrong_fps(self):
        with patch.object(stage.shutil,'disk_usage',return_value=Mock(free=30*stage.GIB)):
            with self.assertRaises(stage.WaitDisk):stage.check_space(Path('/tmp'),6*stage.GIB,25)
        with self.assertRaises(ValueError):stage.validate_video(media(),media(25))

    def test_enhance_only_needs_no_srt_style_or_fonts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';post.mkdir();backend=post/'backend';backend.write_text('#!/bin/sh');backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49','master':'incoming/S01E49.clean-master.mkv','style':'styles/missing.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='enhance'), \
                 patch.object(worker,'ready',return_value=True),patch.object(worker,'ensure_enhanced',return_value={'sha256':'validated'}) as enhance:
                worker.do_job(job)
                self.assertEqual(json.loads(job.read_text())['state'],'ENHANCED_READY')
                enhance.assert_called_once()
                self.assertFalse((root/'output').exists())

    def test_all_mode_enhances_before_waiting_for_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';post.mkdir();(root/'output').mkdir();srt=root/'output/S01E49.zh.srt';srt.write_text('reviewed')
            srt.with_name(srt.name+'.ready.json').write_text(json.dumps({'reviewed':True,'sha256':stage.sha256(srt)}))
            backend=post/'backend';backend.touch();backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49','master':'incoming/master.mkv','style':'styles/missing.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='all'), \
                 patch.object(worker,'ready',return_value=True),patch.object(worker,'ensure_enhanced',return_value={'sha256':'validated'}) as enhance:
                worker.do_job(job)
                self.assertEqual(json.loads(job.read_text())['state'],'WAIT_STYLE')
                enhance.assert_called_once()

    def test_template_presence_does_not_authorize_burn_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);post=root/'post';(post/'styles').mkdir(parents=True)
            (root/'output').mkdir();srt=root/'output/S01E49.zh.srt';srt.write_text('reviewed')
            srt.with_name(srt.name+'.ready.json').write_text(json.dumps({'reviewed':True,'sha256':stage.sha256(srt)}))
            (post/'styles/template.ass').write_text('not approved')
            backend=post/'backend';backend.touch();backend.chmod(0o700)
            job=post/'job.json';job.write_text(json.dumps({'schema_version':1,'episode':'S01E49','master':'incoming/master.mkv','style':'styles/template.ass','output':'final/out.mkv'}))
            with patch.multiple(worker,ROOT=root,POST=post,BACKEND=backend,STAGE='all'), \
                 patch.object(worker,'ready',return_value=True),patch.object(worker,'ensure_enhanced',return_value={'sha256':'validated'}), \
                 patch.object(worker,'command') as command:
                worker.do_job(job)
                self.assertEqual(json.loads(job.read_text())['state'],'WAIT_STYLE_APPROVAL')
                command.assert_not_called()


if __name__=='__main__':unittest.main()
