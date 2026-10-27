from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import final_encode_guard as guard


class StopLoop(Exception):pass


class FinalGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name)
        for name in ('jobs','backend','reports','logs','styles/fonts','cache/S01E49','final'):
            (self.post/name).mkdir(parents=True)
        self.directory=self.post/'cache/S01E49'
        for name in ('S01E49.enhanced.mkv','S01E49.mixed.flac','S01E49.ass'):
            (self.directory/name).write_bytes(b'fixture-'+name.encode())
        self.job={'episode':'S01E49','state':'PROCESSING','master_sha256':'a'*64,
                  'output':'final/S01E49.final-1080p60.mkv','style':'styles/template.ass'}
        self.checkpoint={'episode':'S01E49','master_sha256':'a'*64,'backend_fingerprint':'b'*64,
                         'sha256':guard.sha(self.directory/'S01E49.enhanced.mkv')}
        self.bundle={'schema_version':1,'episode':'S01E49','master_sha256':'a'*64,
                     'enhanced_checkpoint':self.checkpoint,'enhanced_sha256':self.checkpoint['sha256'],
                     'audio_sha256':guard.sha(self.directory/'S01E49.mixed.flac'),
                     'decoded_pcm_sha256':'c'*64,'enhanced_decode_checked':True,'pcm_equivalent':True,
                     'audio_offset_seconds':0.0,'source_probe':{'streams':[{'codec_type':'audio','sample_rate':'48000'}]}}
        self.write('jobs/S01E49.json',self.job)
        self.write('backend/production-ready.json',{'master_sha256':'a'*64,'backend_fingerprint':'b'*64})
        self.write('cache/S01E49/S01E49.source.ready.json',self.bundle)
        self.write('cache/S01E49/S01E49.enhanced.mkv.ready.json',self.checkpoint)
        style=self.post/'styles/template.ass';style.write_bytes(b'approved style')
        font=self.post/'styles/fonts/approved.ttf';font.write_bytes(b'approved font')
        self.write('styles/template.ass.ready.json',{'approved':True,'master_sha256':'a'*64,
                   'sha256':guard.sha(style),'fonts':{'approved.ttf':guard.sha(font)}})
        binding=guard.input_binding(self.post,'S01E49')
        self.child={'pid':123,'ppid':456,'starttime':10,'state':'S',
                    'argv':guard.expected_command(self.post,'S01E49',binding)}
        self.worker={'pid':456,'ppid':1,'starttime':5,'state':'S',
                     'argv':['python',str(Path(guard.__file__).resolve().with_name('server_worker.py')),'--loop']}
        self.exe=str(Path(shutil.which('ffmpeg')).resolve())

    def write(self,path,value):(self.post/path).write_text(json.dumps(value))

    def identity(self):
        with patch.object(guard.thermal,'process',side_effect=lambda pid:self.child if pid==123 else self.worker), \
             patch.object(guard,'executable',return_value=self.exe):
            return guard.identify(self.post,'S01E49',123,456)[0]

    def temps(self,t10=80,a4000=90):
        return {'0':{'current':a4000,'pause':97,'resume':93},
                '1':{'current':t10,'pause':86,'resume':82}}

    def test_exact_owned_child_can_attach_after_master_was_released(self):
        self.assertFalse((self.post/'incoming/S01E49.clean-master.mov').exists())
        identity=self.identity()
        self.assertEqual(identity['stage'],'FINAL_ENCODE');self.assertEqual(identity['target_pid'],123)
        self.assertEqual(identity['master_sha256'],'a'*64)
        self.assertIn('not full media hashes',identity['media_hash_policy'])
        guard.verify_inputs(self.post,identity)

    def test_wrong_parent_argv_executable_and_uid_are_rejected(self):
        original={**self.child,'argv':list(self.child['argv'])}
        for change in ('parent','gpu','output','font','exe','uid'):
            with self.subTest(change=change):
                self.child={**original,'argv':list(original['argv'])}
                exe=self.exe
                if change=='parent':self.child['ppid']=999
                if change=='gpu':self.child['argv'][self.child['argv'].index('-gpu')+1]='0'
                if change=='output':self.child['argv'][-1]=str(self.post/'final/other.partial.mkv')
                if change=='font':self.child['argv'][self.child['argv'].index('-vf')+1]='ass=other.ass'
                if change=='exe':exe='/usr/bin/unrelated'
                def process(pid):
                    if change=='uid':raise ValueError('Process belongs to another uid')
                    return self.child if pid==123 else self.worker
                with patch.object(guard.thermal,'process',side_effect=process), \
                     patch.object(guard,'executable',return_value=exe),self.assertRaises(ValueError):
                    guard.identify(self.post,'S01E49',123,456)

    def test_receipt_ass_and_font_changes_reject_existing_binding(self):
        identity=self.identity()
        ass=self.directory/'S01E49.ass';original=ass.read_bytes();ass.write_bytes(b'different ASS')
        with self.assertRaises(ValueError):guard.verify_inputs(self.post,identity)
        ass.write_bytes(original)
        font=self.post/'styles/fonts/approved.ttf';font.write_bytes(b'different font')
        with self.assertRaisesRegex(ValueError,'font changed'):guard.input_binding(self.post,'S01E49')

    def test_recycled_pid_is_ended_without_any_signal_or_quality_claim(self):
        identity=self.identity()
        with patch.object(guard,'identify',return_value=(identity,self.child)), \
             patch.object(guard,'verify',side_effect=ValueError('starttime changed')), \
             patch.object(guard.thermal,'send') as send:
            guard.run(self.post,'S01E49',123,456)
        send.assert_not_called();state=json.loads((self.post/'reports/final_encode_thermal_pause.json').read_text())
        self.assertEqual(state['state'],'ENDED');self.assertFalse(state['final_quality_accepted'])

    def test_unknown_external_stop_is_never_resumed(self):
        identity=self.identity();self.child['state']='T'
        with patch.object(guard,'identify',return_value=(identity,self.child)), \
             patch.object(guard,'verify',return_value=self.child), \
             patch.object(guard.thermal,'temperatures',return_value=self.temps()), \
             patch.object(guard.thermal,'send') as send,patch.object(guard.time,'sleep',side_effect=StopLoop), \
             self.assertRaises(StopLoop):
            guard.run(self.post,'S01E49',123,456)
        send.assert_not_called();state=json.loads((self.post/'reports/final_encode_thermal_pause.json').read_text())
        self.assertEqual(state['state'],'UNOWNED_STOP');self.assertFalse(state['owned_stop'])

    def test_owned_hot_pause_then_hysteresis_and_cool_resume(self):
        identity=self.identity();signals=[];sleeps=[]
        def send(binding,signum):
            signals.append(signum);self.child['state']='T' if signum==signal.SIGSTOP else 'S'
        def sleep(_):
            sleeps.append(1)
            if len(sleeps)==3:raise StopLoop
        with patch.object(guard,'identify',return_value=(identity,self.child)), \
             patch.object(guard,'verify',side_effect=lambda _:self.child), \
             patch.object(guard.thermal,'temperatures',side_effect=[self.temps(86),self.temps(85),self.temps(82)]), \
             patch.object(guard.thermal,'send',side_effect=send),patch.object(guard.time,'sleep',side_effect=sleep), \
             self.assertRaises(StopLoop):
            guard.run(self.post,'S01E49',123,456)
        self.assertEqual(signals,[signal.SIGSTOP,signal.SIGCONT])
        state=json.loads((self.post/'reports/final_encode_thermal_pause.json').read_text())
        self.assertEqual(state['state'],'RUNNING');self.assertFalse(state['owned_stop'])
        self.assertFalse(state['final_quality_accepted'])

    def test_input_authority_failure_holds_stop_even_after_cooling(self):
        identity=self.identity();signals=[];sleeps=[]
        def send(binding,signum):
            signals.append(signum);self.child['state']='T'
        def sleep(_):
            sleeps.append(1)
            if len(sleeps)==2:raise StopLoop
        with patch.object(guard,'identify',return_value=(identity,self.child)), \
             patch.object(guard,'verify',side_effect=lambda _:self.child), \
             patch.object(guard,'verify_inputs',side_effect=[ValueError('receipt unavailable'),None]), \
             patch.object(guard.thermal,'temperatures',return_value=self.temps()), \
             patch.object(guard.thermal,'send',side_effect=send),patch.object(guard.time,'sleep',side_effect=sleep), \
             self.assertRaises(StopLoop):
            guard.run(self.post,'S01E49',123,456)
        self.assertEqual(signals,[signal.SIGSTOP])
        state=json.loads((self.post/'reports/final_encode_thermal_pause.json').read_text())
        self.assertTrue(state['owned_stop']);self.assertIn('re-adoption',state['reason'])

    def test_adoption_is_bound_to_final_source_ass_and_process(self):
        identity=self.identity();self.child['state']='T'
        prior={**identity,'owned_stop':True,'paused_at':datetime.now(timezone.utc).isoformat()}
        self.assertIsInstance(guard.adopt(prior,identity,self.child),float)
        for key in ('ass_sha256','source_bundle_sha256','backend_starttime','backend_cmdline'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                guard.adopt({**prior,key:'replacement'},identity,self.child)

    def test_temperature_command_timeout_pauses_then_recovers_only_owned_stop(self):
        identity=self.identity();signals=[];sleeps=[]
        def send(binding,signum):
            signals.append(signum);self.child['state']='T' if signum==signal.SIGSTOP else 'S'
        def sleep(_):
            sleeps.append(1)
            if len(sleeps)==2:raise StopLoop
        with patch.object(guard,'identify',return_value=(identity,self.child)), \
             patch.object(guard,'verify',side_effect=lambda _:self.child), \
             patch.object(guard.thermal,'temperatures',side_effect=[subprocess.TimeoutExpired('nvidia-smi',10),self.temps()]), \
             patch.object(guard.thermal,'send',side_effect=send),patch.object(guard.time,'sleep',side_effect=sleep), \
             self.assertRaises(StopLoop):
            guard.run(self.post,'S01E49',123,456)
        self.assertEqual(signals,[signal.SIGSTOP,signal.SIGCONT])


if __name__=='__main__':unittest.main()
