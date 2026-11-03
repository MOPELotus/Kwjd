import importlib.util
from pathlib import Path
import unittest
import json
import io
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

    def test_actual_codec_controls_lossless_receipt(self):
        self.assertFalse(m.receipt_is_lossless({'actual_quality':'lossless','streams':[{'codec_name':'vorbis'}]}))
        self.assertTrue(m.receipt_is_lossless({'streams':[{'codec_name':'flac'}]}))
        self.assertTrue(m.lossless_codec('pcm_s24le'))

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

class ProviderFallbackTests(unittest.TestCase):
    def test_qq_gateway_uses_netease_and_circuits_qq(self):
        args=SimpleNamespace(base='http://localhost',qq_fallback=True)
        group={'sources':[{'ref':'qq:a','duration_ms':10000}]}
        alternate={'ref':'netease:b','duration_ms':10000}
        data={'available':True,'url':'https://secret.invalid/media','format':'flac','duration_ms':10000}
        blocked={}
        def request(url,timeout):
            if '/qq:' in url: raise HTTPError('https://secret.invalid/signed',502,'gateway',None,None)
            return io.StringIO(json.dumps({'data':data}))
        with patch.object(m.time,'sleep'),patch.object(m.urllib.request,'urlopen',side_effect=request) as call:
            candidates,attempts,sources=m.collect_candidates(args,group,blocked,{'qq:a':alternate})
            self.assertEqual(call.call_count,4)
            self.assertEqual(candidates[0][1]['ref'],'netease:b')
            self.assertNotIn('secret.invalid',json.dumps(attempts))
            m.collect_candidates(args,group,blocked,{'qq:a':alternate})
            self.assertEqual(call.call_count,7) # QQ is not hammered again.
            candidates,attempts,_=m.collect_candidates(args,group,blocked,{})
            self.assertEqual(candidates,[])
            self.assertEqual(call.call_count,7)

    def test_netease_gateway_and_rate_limit_still_stop(self):
        args=SimpleNamespace(base='http://localhost',qq_fallback=True)
        for ref,code in [('netease:b',502),('qq:a',429)]:
            with self.subTest(ref=ref,code=code),patch.object(m.time,'sleep'),patch.object(m.urllib.request,'urlopen',side_effect=HTTPError('https://secret.invalid',code,'error',None,None)):
                with self.assertRaises(m.StopBatch):
                    m.collect_candidates(args,{'sources':[{'ref':ref}]},{},{})

class ProviderRotationTests(unittest.TestCase):
    def test_netease_failure_falls_back_to_recovered_qq(self):
        args=SimpleNamespace(base='http://localhost',provider_rotation=True,provider_retry_seconds=600)
        group={'sources':[{'ref':'netease:b','duration_ms':10000},{'ref':'qq:a','duration_ms':10000}]}
        blocked={'qq':{'reason':'HTTP 502','until':0}}
        data={'available':True,'url':'https://secret.invalid/media','format':'flac','duration_ms':10000}
        def request(url,timeout):
            if '/netease:' in url: raise HTTPError('https://secret.invalid',502,'gateway',None,None)
            return io.StringIO(json.dumps({'data':data}))
        with patch.object(m.time,'sleep'),patch.object(m.urllib.request,'urlopen',side_effect=request) as call:
            candidates,attempts,_=m.collect_candidates(args,group,blocked,{})
        self.assertEqual(call.call_count,4)
        self.assertEqual(candidates[0][1]['ref'],'qq:a')
        self.assertIn('netease',blocked)
        self.assertNotIn('qq',blocked)

    def test_both_providers_wait_then_retry_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            args=SimpleNamespace(evidence=Path(directory),provider_rotation=True,provider_retry_seconds=600)
            pending={'deferred':2,'verified':5,'failed':0}
            done={'deferred':0,'verified':7,'failed':0}
            with patch.object(m,'run_pass',side_effect=[pending,done]) as run_pass,patch.object(m.time,'sleep') as sleep:
                m.run(args)
            self.assertEqual(run_pass.call_count,2)
            sleep.assert_called_once_with(600)
            status=json.loads((args.evidence/'status.json').read_text(encoding='utf8'))
            self.assertEqual(status['status'],'WAITING_PROVIDER_RETRY')

if __name__=='__main__':unittest.main()
