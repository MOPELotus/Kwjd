import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import hashlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_fixed_post import inputs,WaitInput,select_project,freeze_style


class FixedPostInputGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);folder=self.root/'post/incoming';folder.mkdir(parents=True)
        self.master=folder/'S03E02.clean-master.mov';self.master.write_bytes(b'synthetic master for gate test')
        handoffs=self.root/'post/handoffs';handoffs.mkdir()
        self.receipt=dict(episode='S03E02',transfer_verified=True,bytes=self.master.stat().st_size,
            sha256=hashlib.sha256(self.master.read_bytes()).hexdigest())
        self.receipt_path=handoffs/'S03E02.master-received.json';self.receipt_path.write_text(json.dumps(self.receipt))

    def test_unsealed_subtitle_never_launches_install_or_worker(self):
        with patch('run_fixed_post.subprocess.run') as launch:
            with self.assertRaises(WaitInput):inputs(self.root,self.master,self.root/'missing.ass')
        launch.assert_not_called()

    def test_filename_presence_or_modified_master_does_not_admit_production(self):
        self.master.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'receipt'):inputs(self.root,self.master,self.root/'missing.ass')

    def test_unadmitted_episode_is_not_added_to_queue(self):
        repo=Path(__file__).resolve().parents[1]
        self.assertEqual(select_project(repo,'S03E02').name,'jigong-s03.json')
        with self.assertRaisesRegex(ValueError,'admitted'):select_project(repo,'S03E03')

    def test_next_master_approval_does_not_rebind_an_active_episode_style(self):
        folder=self.root/'post/styles';folder.mkdir();template=folder/'template.ass'
        template.write_text('synthetic test style')
        marker=template.with_name(template.name+'.ready.json')
        approval=dict(approved=True,master_sha256='first-master',sha256=hashlib.sha256(template.read_bytes()).hexdigest())
        marker.write_text(json.dumps(approval))
        first=freeze_style(self.root,'S03E02',{'_selected_style':str(template)})
        first_marker=first.with_name(first.name+'.ready.json');original=first_marker.read_bytes()
        marker.write_text(json.dumps({**approval,'master_sha256':'next-master'}))
        freeze_style(self.root,'S03E03',{'_selected_style':str(template)})
        self.assertEqual(first_marker.read_bytes(),original)
        self.assertEqual(json.loads(first_marker.read_text())['master_sha256'],'first-master')


if __name__=='__main__':unittest.main()
