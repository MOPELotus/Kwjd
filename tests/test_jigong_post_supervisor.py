import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
SPEC=importlib.util.spec_from_file_location('post_supervisor',Path(__file__).resolve().parents[1]/'scripts/jigong_post_supervisor.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

class SupervisorTests(unittest.TestCase):
    def test_no_final_is_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:self.assertFalse(m.complete(Path(directory)))

    def test_receipts_bind_media_quality_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);post=root/'post';post.mkdir();(post/'handoffs').mkdir()
            media=post/'final.mkv';media.write_bytes(b'fixture')
            evidence=post/'quality.json';evidence.write_text('{}')
            report=post/'report.json'
            data=dict(episode=m.EPISODE,master_sha256=m.MASTER,sha256=m.sha(media),final_decode_checked=True,final_pcm_equivalent=True,final_video_frame_count=m.EXPECTED_FRAMES)
            report.write_text(json.dumps(data))
            manifest=dict(episode=m.EPISODE,master_sha256=m.MASTER,final_video_frame_count=m.EXPECTED_FRAMES,technical_checked=True,server_quality_checked=True,final_path=str(media),sha256=m.sha(media),report_path=str(report),report_sha256=m.sha(report),quality_evidence_path=str(evidence),quality_evidence_sha256=m.sha(evidence))
            target=post/'handoffs'/f'{m.EPISODE}.cloud-final.json';target.write_text(json.dumps(manifest))
            self.assertTrue(m.complete(root))
            manifest['server_quality_checked']=False;target.write_text(json.dumps(manifest));self.assertFalse(m.complete(root))
            manifest['server_quality_checked']=True;target.write_text(json.dumps(manifest));media.write_bytes(b'changed');self.assertFalse(m.complete(root))

if __name__=='__main__':unittest.main()
