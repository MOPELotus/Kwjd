import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError

spec=importlib.util.spec_from_file_location('music_review_server',Path(__file__).resolve().parents[1]/'scripts/music_review_server.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class MusicReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.directory=self.root/'.kwjd/mihoyo-music/selection';self.directory.mkdir(parents=True)
        audio=self.root/'music/miHoYo/library/Genshin/a.flac';audio.parent.mkdir(parents=True);audio.write_bytes(b'fLaC0123456789')
        self.row=dict(key='a',game='Genshin',title='test',album='album',duration=30,sha256='abc',mood='舒缓候选',file=str(audio))
        (self.directory/'catalog.json').write_text(json.dumps(dict(tracks=[self.row])),encoding='utf8')
        self.library=m.Library(self.root);self.server=m.Server(('127.0.0.1',0),self.library)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.temp.cleanup()

    def post(self,body,headers=None):
        return urlopen(Request(self.url+'/api/review/a',data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**(headers or {})}))

    def test_default_and_unknown_mapping(self):
        self.assertEqual(self.library.track('a')['label'],'calm');self.assertFalse(self.library.track('a')['reviewed'])
        self.assertIsNone(m.MODEL_LABELS.get('混合/待复核'));self.assertIsNone(m.MODEL_LABELS.get('边界不清/待复核'))
        self.assertEqual(m.MODEL_LABELS['紧张候选/暂不入视频'],'intense')

    def test_review_persists_and_other_is_supported(self):
        with self.post(dict(label='other',source_sha256='abc',expected_revision=0)) as response:self.assertEqual(response.status,200)
        recovered=m.Library(self.root).track('a');self.assertEqual(recovered['label'],'other');self.assertTrue(recovered['reviewed'])
        self.assertEqual(self.row['mood'],'舒缓候选')

    def test_clear_stays_unselected_after_restart(self):
        self.library.save('a',dict(label=None,source_sha256='abc',expected_revision=0))
        self.assertIsNone(m.Library(self.root).track('a')['label'])

    def test_stale_revision_or_audio_change_does_not_overwrite(self):
        self.library.save('a',dict(label='calm',source_sha256='abc',expected_revision=0))
        with self.assertRaises(m.Conflict):self.library.save('a',dict(label='other',source_sha256='abc',expected_revision=0))
        with self.assertRaises(m.Conflict):self.library.save('a',dict(label='other',source_sha256='changed',expected_revision=1))

    def test_audio_ranges_and_seek(self):
        with urlopen(Request(self.url+'/audio/a',headers={'Range':'bytes=4-7'})) as response:
            self.assertEqual(response.status,206);self.assertEqual(response.read(),b'0123');self.assertEqual(response.headers['Content-Range'],'bytes 4-7/14')
        self.assertEqual(m.byte_range('bytes=-4',14),(10,13,True))
        with self.assertRaises(HTTPError) as e:urlopen(Request(self.url+'/audio/a',headers={'Range':'bytes=99-'}))
        self.assertEqual(e.exception.code,416)

    def test_audio_path_allowlist(self):
        self.library.rows['a']['file']=str(self.root/'outside.flac')
        with self.assertRaises(ValueError):self.library.audio('a')

    def test_cross_origin_and_host_rejected(self):
        with self.assertRaises(HTTPError) as e:self.post(dict(label='other',source_sha256='abc',expected_revision=0),{'Origin':'https://outside.example'})
        self.assertEqual(e.exception.code,403)
        with self.assertRaises(HTTPError) as e:urlopen(Request(self.url+'/api/health',headers={'Host':'outside.example:'+str(self.server.server_port)}))
        self.assertEqual(e.exception.code,403)

    def test_journal_recovers_without_snapshot(self):
        self.library.save('a',dict(label='cheerful',source_sha256='abc',expected_revision=0))
        self.library.state.unlink();self.assertEqual(m.Library(self.root).track('a')['label'],'cheerful')


if __name__=='__main__':unittest.main()
