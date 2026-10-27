from datetime import datetime,timezone
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import thermal_guard as guard


class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.identity={'episode':'S01E49','backend_pid':123,'worker_pid':456,
                       'backend_starttime':10,'worker_starttime':5,
                       'backend_cmdline':['python','segmented_backend.py','master'],
                       'worker_cmdline':['python','server_worker.py'],
                       'master_sha256':'a'*64,'backend_fingerprint':'b'*64}
        self.backend={'pid':123,'ppid':456,'starttime':10,'state':'T','argv':self.identity['backend_cmdline']}
        self.worker={'pid':456,'ppid':1,'starttime':5,'state':'S','argv':self.identity['worker_cmdline']}
        self.receipt={**self.identity,'owned_stop':True,'paused_at':datetime.now(timezone.utc).isoformat()}

    def temps(self,a=90,b=80):
        return {'0':{'current':a,'pause':97,'resume':93},'1':{'current':b,'pause':86,'resume':82}}

    def test_hysteresis_hardware_boundary_and_external_stops(self):
        self.assertEqual(guard.decision(self.temps(95,86),False,False),'STOP')
        self.assertEqual(guard.decision(self.temps(97,80),False,False),'STOP')
        self.assertEqual(guard.decision(self.temps(95,85),False,False),'RUN')
        self.assertEqual(guard.decision(self.temps(93,83),True,True),'WAIT')
        self.assertEqual(guard.decision(self.temps(94,82),True,True),'WAIT')
        self.assertEqual(guard.decision(self.temps(93,82),True,True),'CONT')
        self.assertEqual(guard.decision(self.temps(70,70),False,True),'EXTERNAL_STOP')
        self.assertEqual(guard.decision(self.temps(),True,False),'OWNERSHIP_LOST')

    def test_adoption_requires_exact_prior_stop_receipt(self):
        self.assertIsInstance(guard.adopt(self.receipt,self.identity,self.backend),float)
        for changes in ({'owned_stop':False},{'backend_starttime':11},{'worker_starttime':6},
                        {'master_sha256':'c'*64},{'backend_fingerprint':'d'*64},
                        {'backend_cmdline':['another']},{'backend_pid':124}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                guard.adopt({**self.receipt,**changes},self.identity,self.backend)
        missing={**self.receipt};missing.pop('backend_starttime')
        with self.assertRaises(ValueError):guard.adopt(missing,self.identity,self.backend)
        with self.assertRaises(ValueError):guard.adopt(self.receipt,self.identity,{**self.backend,'state':'S'})

    def test_recycled_pid_is_never_signalled(self):
        recycled={**self.backend,'starttime':11}
        with patch.object(guard,'process',side_effect=[recycled,self.worker]), \
             patch.object(guard.os,'pidfd_open') as opened, \
             patch.object(guard.signal,'pidfd_send_signal') as sent,self.assertRaises(ValueError):
            guard.send(self.identity,signal.SIGCONT)
        opened.assert_not_called();sent.assert_not_called()

    def test_recycle_after_pidfd_open_is_not_signalled(self):
        with patch.object(guard,'verify',side_effect=[self.backend,ValueError('recycled')]), \
             patch.object(guard.os,'pidfd_open',return_value=99),patch.object(guard.os,'close') as closed, \
             patch.object(guard.signal,'pidfd_send_signal') as sent,self.assertRaises(ValueError):
            guard.send(self.identity,signal.SIGCONT)
        sent.assert_not_called();closed.assert_called_once_with(99)

    def test_actual_temperature_thresholds_are_parsed_not_invented(self):
        def text(current,operating,slowdown,shutdown):
            return f'GPU Current Temp : {current} C\nGPU Max Operating Temp : {operating} C\nGPU Slowdown Temp : {slowdown} C\nGPU Shutdown Temp : {shutdown} C\n'
        with patch.object(guard.subprocess,'check_output',side_effect=[text(95,98,100,103),text(85,87,89,92)]):
            temps=guard.temperatures()
        self.assertEqual((temps['0']['pause'],temps['0']['resume']),(97,93))
        self.assertEqual((temps['1']['pause'],temps['1']['resume']),(86,82))
        with patch.object(guard.subprocess,'check_output',return_value='GPU Current Temp : N/A'),self.assertRaises(ValueError):
            guard.temperatures()

    def test_ended_owned_process_is_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp);(post/'reports').mkdir();(post/'logs').mkdir()
            path=post/'reports/thermal_pause.json';path.write_text(json.dumps(self.receipt))
            with patch.object(guard,'identify',return_value=(self.identity,self.backend)), \
                 patch.object(guard,'verify',side_effect=ProcessLookupError('ended')), \
                 patch.object(guard,'send') as sent:
                guard.run(post,'S01E49',123,456,True)
            sent.assert_not_called();record=json.loads(path.read_text())
            self.assertEqual(record['state'],'ENDED');self.assertTrue(record['owned_stop'])

    def test_restart_with_adopt_flag_can_watch_running_unowned_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp);(post/'reports').mkdir();(post/'logs').mkdir()
            path=post/'reports/thermal_pause.json'
            path.write_text(json.dumps({**self.receipt,'owned_stop':False}))
            running={**self.backend,'state':'R'}
            with patch.object(guard,'identify',return_value=(self.identity,running)), \
                 patch.object(guard,'verify',side_effect=ProcessLookupError('ended')), \
                 patch.object(guard,'send') as sent:
                guard.run(post,'S01E49',123,456,True)
            sent.assert_not_called()
            self.assertFalse(json.loads(path.read_text())['owned_stop'])

    def test_restart_never_adopts_unowned_external_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            post=Path(tmp);(post/'reports').mkdir();(post/'logs').mkdir()
            (post/'reports/thermal_pause.json').write_text(json.dumps({**self.receipt,'owned_stop':False}))
            with patch.object(guard,'identify',return_value=(self.identity,self.backend)), \
                 patch.object(guard,'send') as sent,self.assertRaisesRegex(ValueError,'external stop'):
                guard.run(post,'S01E49',123,456,True)
            sent.assert_not_called()


if __name__=='__main__':unittest.main()
