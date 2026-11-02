import importlib.util
from pathlib import Path
import unittest
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
spec=importlib.util.spec_from_file_location('download',Path(__file__).parents[1]/'scripts/download_mihoyo_music.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class AudioAdmissionTests(unittest.TestCase):
    def test_encrypted_and_short_trials_rejected(self):
        base={'available':True,'url':'https://example.invalid/a','format':'flac','duration_ms':180000}
        for changes in ({'format':'nac'},{'duration_ms':30000},{'extensions':{'trial':True}}):
            self.assertFalse(m.native_candidate(dict(base,**changes),180000))
        self.assertTrue(m.native_candidate(base,180000))

    def test_lossless_preferred_over_larger_lossy_bitrate(self):
        self.assertGreater(m.candidate_score({'format':'flac','bitrate':700000}),m.candidate_score({'format':'mp3','bitrate':900000}))

    def test_gateway_failure_stops_before_next_song(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plan=root/'plan.json'
            group={'title':'test','sources':[{'ref':'qq:test','duration_ms':10000}]}
            plan.write_text(json.dumps({'groups':[group,dict(group,title='next')]}),encoding='utf8')
            args=SimpleNamespace(plan=plan,evidence=root/'evidence',output=root,minimum_free_gib=0,base='http://localhost',limit=0)
            error=HTTPError('https://secret.invalid/signed',502,'gateway',None,None)
            with patch.object(m.time,'sleep'),patch.object(m.urllib.request,'urlopen',side_effect=error) as request:
                with self.assertRaises(m.StopBatch):m.run(args)
            self.assertEqual(request.call_count,1)
            status=json.loads((args.evidence/'status.json').read_text(encoding='utf8'))
            self.assertEqual(status['status'],'STOPPED_REQUIRES_REVIEW')
            self.assertEqual(status['index'],0)
            self.assertEqual(status['failed'],0)
            self.assertNotIn('secret.invalid',json.dumps(status))

if __name__=='__main__':unittest.main()
