import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_guard_watch as guard


class NativeGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.post = Path(self.tmp.name); self.ep = 'S01E50'
        for name in ['jobs', 'backend/native-profiles', 'reports']:
            (self.post / name).mkdir(parents=True, exist_ok=True)
        self.job = dict(episode=self.ep, state='ENHANCING', master_sha256='a'*64,
                       master='incoming/S01E50.clean-master.mov',
                       picture_pipeline='native_light_gfpgan_rife_ass_one_encode')
        self.write('jobs/S01E50.json', self.job)
        self.write('backend/production-ready.json', dict(master_sha256='a'*64, backend_fingerprint='b'*64))
        config = self.write('backend/runtime.native-active.json', dict(restoration_backend='cuda_gfpgan_fused'))
        raw = config.read_bytes()
        (self.post/'backend/native-profiles'/(hashlib.sha256(raw).hexdigest()+'.json')).write_bytes(raw)
        self.write('cache/S01E50/S01E50.segments/'+('a'*16+'-'+'b'*16)+'/context.json',
                   dict(episode=self.ep, master_sha256='a'*64, backend_fingerprint='b'*64))
        self.worker = dict(starttime=10, argv=['python', 'server_worker.py'], state='S')
        self.backend = dict(starttime=20, ppid=100, state='R', argv=[
            str(self.post/'face-venv/bin/python'), str(Path(guard.__file__).with_name('segmented_backend.py')),
            str(self.post/'incoming/S01E50.clean-master.mov'),
            str(self.post/'cache/S01E50/S01E50.enhanced.partial.mkv'), self.ep,
            '--config', str(config)])

    def write(self, relative, value):
        p=self.post/relative; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value)); return p

    def identify(self, backend=None):
        with patch.object(guard, 'worker_identity', return_value=self.worker) as checked, \
             patch.object(guard.thermal, 'process', return_value=backend or self.backend):
            result=guard.identify(self.post, self.ep, 200, 100, 10)
            checked.assert_called_once_with(100, 10, self.ep)
            return result

    def test_exact_scoped_native_child_is_admitted(self):
        identity, backend=self.identify()
        self.assertEqual(identity['backend_starttime'], 20)
        self.assertEqual(identity['worker_starttime'], 10)
        self.assertEqual(identity['backend_cmdline'], self.backend['argv'])

    def test_plan_other_episode_and_other_parent_are_refused(self):
        cases=[dict(self.backend, ppid=999),
               dict(self.backend, argv=self.backend['argv'][:2]+['--plan']+self.backend['argv'][2:]),
               dict(self.backend, argv=[x.replace('S01E50','S01E51') for x in self.backend['argv']])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError): self.identify(case)

    def test_worker_starttime_check_cannot_be_bypassed(self):
        with patch.object(guard, 'worker_identity', side_effect=ValueError('PID reused')), \
             patch.object(guard.thermal, 'process') as process, self.assertRaises(ValueError):
            guard.identify(self.post, self.ep, 200, 100, 11)
        process.assert_not_called()

    def test_wrong_production_and_changed_profile_are_refused(self):
        self.write('backend/production-ready.json',dict(master_sha256='c'*64,backend_fingerprint='b'*64))
        with self.assertRaises(ValueError): self.identify()
        self.write('backend/production-ready.json',dict(master_sha256='a'*64,backend_fingerprint='b'*64))
        self.write('backend/runtime.native-active.json',dict(restoration_backend='other'))
        with self.assertRaises((ValueError,FileNotFoundError)): self.identify()

    def test_pause_precedes_native_abort_and_hardware_limits(self):
        def reader():
            return {'0':dict(current=93,pause=97,resume=93),
                    '1':dict(current=83,pause=86,resume=82)}
        temps=guard.proactive_temperatures(reader)
        self.assertEqual([(temps[x]['pause'],temps[x]['resume']) for x in ['0','1']],[(93,87),(83,77)])
        self.assertEqual(guard.thermal.decision(temps,False,False),'STOP')
        temps['0']['current']=87;temps['1']['current']=78
        self.assertEqual(guard.thermal.decision(temps,True,True),'WAIT')
        temps['1']['current']=77
        self.assertEqual(guard.thermal.decision(temps,True,True),'CONT')
        self.assertEqual(guard.thermal.decision(temps,False,True),'EXTERNAL_STOP')

    def test_lower_hardware_limit_is_never_raised(self):
        temps=guard.proactive_temperatures(lambda:{'0':dict(pause=90), '1':dict(pause=80)})
        self.assertEqual(temps['0']['pause'],90);self.assertEqual(temps['1']['pause'],80)

    def limits(self):
        return {'0':dict(pause=97,resume=93,shutdown=103),
                '1':dict(pause=86,resume=82,shutdown=92)}

    def test_cached_limits_and_lightweight_device_identity(self):
        query=lambda:'0, GPU-a, NVIDIA RTX A4000, 91\n1, GPU-b, Tesla T10, 82\n'
        reader=guard.fast_temperature_reader(self.limits,query)
        self.assertEqual(reader()['1']['pause'],83)
        self.assertEqual(reader()['0']['uuid'],'GPU-a')

    def test_device_replacement_and_incomplete_telemetry_refused(self):
        good='0, GPU-a, NVIDIA RTX A4000, 91\n1, GPU-b, Tesla T10, 82\n'
        for bad in [good.replace('GPU-b','GPU-c'),good.splitlines()[0],
                    good.replace('Tesla T10','software'),good.replace('82','N/A')]:
            with self.subTest(bad=bad):
                samples=iter([good,bad]);reader=guard.fast_temperature_reader(self.limits,lambda:next(samples))
                with self.assertRaises(ValueError):reader()


if __name__ == '__main__': unittest.main()
