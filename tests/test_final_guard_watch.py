import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import final_guard_watch as watch


class StopLoop(Exception):pass


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name);(self.post/'reports').mkdir();(self.post/'jobs').mkdir()
        self.worker={'pid':77,'ppid':1,'starttime':99,'state':'S',
                     'argv':['python','-u',str(Path(watch.__file__).resolve().with_name('server_worker.py')),
                             '--loop','--stage','all','--episode','S01E49']}
        self.set_job('PROCESSING')

    def set_job(self,state):
        (self.post/'jobs/S01E49.json').write_text(json.dumps({'episode':'S01E49','state':state}))

    def status(self):return json.loads((self.post/'reports/final_guard_watch.json').read_text())

    def test_reused_parent_is_rejected_before_any_child_scan(self):
        recycled={**self.worker,'starttime':100}
        with patch.object(watch.guard.thermal,'process',side_effect=[self.worker,recycled]), \
             patch.object(watch,'direct_children') as scan,patch.object(watch.guard,'run') as attach:
            watch.run(self.post,'S01E49',77,99)
        scan.assert_not_called();attach.assert_not_called();self.assertEqual(self.status()['state'],'ENDED')

    def test_exited_parent_is_never_replaced_or_attached(self):
        with patch.object(watch.guard.thermal,'process',side_effect=ProcessLookupError('exited')), \
             patch.object(watch,'direct_children') as scan,patch.object(watch.guard,'run') as attach:
            watch.run(self.post,'S01E49',77,99)
        scan.assert_not_called();attach.assert_not_called();self.assertEqual(self.status()['state'],'ENDED')

    def test_no_children_scan_before_processing(self):
        self.set_job('ENHANCING')
        with patch.object(watch.guard.thermal,'process',return_value=self.worker), \
             patch.object(watch,'direct_children') as scan,patch.object(watch.guard,'run') as attach, \
             patch.object(watch.time,'sleep',side_effect=StopLoop),self.assertRaises(StopLoop):
            watch.run(self.post,'S01E49',77,99)
        scan.assert_not_called();attach.assert_not_called();self.assertEqual(self.status()['state'],'WAITING')

    def test_preflight_and_other_children_rejected_unique_final_handed_to_guard(self):
        identity={'backend_starttime':200,'master_sha256':'a'*64,'backend_fingerprint':'b'*64}
        def identify(post,episode,child,worker):
            if child==11:raise ValueError('Font preflight argv is not final')
            if child==12:raise ValueError('Unrelated child')
            return identity,{'pid':13}
        def attached(*args,**kwargs):
            # Watcher has not fabricated a RUNNING thermal receipt or seal.
            self.assertEqual(self.status()['state'],'ATTACHING')
            self.assertFalse(self.status()['final_quality_accepted'])
        with patch.object(watch.guard.thermal,'process',return_value=self.worker), \
             patch.object(watch,'direct_children',return_value=[11,12,13]), \
             patch.object(watch.guard,'identify',side_effect=identify) as identify_mock, \
             patch.object(watch.guard,'run',side_effect=attached) as attach:
            watch.run(self.post,'S01E49',77,99)
        self.assertEqual(identify_mock.call_count,3)
        attach.assert_called_once_with(self.post,'S01E49',13,77,adopt_stop=False)
        self.assertEqual(self.status()['state'],'ENDED');self.assertFalse(self.status()['final_quality_accepted'])

    def test_multiple_matching_children_never_attached(self):
        with patch.object(watch.guard.thermal,'process',return_value=self.worker), \
             patch.object(watch,'direct_children',return_value=[11,12]), \
             patch.object(watch.guard,'identify',return_value=({'backend_starttime':200},{})), \
             patch.object(watch.guard,'run') as attach,patch.object(watch.time,'sleep',side_effect=StopLoop), \
             self.assertRaises(StopLoop):
            watch.run(self.post,'S01E49',77,99)
        attach.assert_not_called();self.assertEqual(self.status()['matching_target_pids'],[11,12])
        self.assertIn('Multiple',self.status()['reason'])

    def test_ready_without_target_ends_without_quality_claim(self):
        self.set_job('READY')
        with patch.object(watch.guard.thermal,'process',return_value=self.worker), \
             patch.object(watch,'direct_children') as scan,patch.object(watch.guard,'run') as attach:
            watch.run(self.post,'S01E49',77,99)
        scan.assert_not_called();attach.assert_not_called();self.assertEqual(self.status()['state'],'ENDED')
        self.assertFalse(self.status()['final_quality_accepted'])

    def test_inner_guard_rechecks_and_can_reject_changed_target(self):
        with patch.object(watch.guard.thermal,'process',return_value=self.worker), \
             patch.object(watch,'direct_children',return_value=[13]), \
             patch.object(watch.guard,'identify',return_value=({'backend_starttime':200},{})), \
             patch.object(watch.guard,'run',side_effect=ValueError('target changed after candidate inspection')):
            watch.run(self.post,'S01E49',77,99)
        self.assertEqual(self.status()['state'],'ENDED');self.assertIn('target changed',self.status()['reason'])

    def test_wrong_episode_or_changed_full_worker_argv_is_rejected(self):
        for argv in (self.worker['argv'][:-1]+['S01E50'],self.worker['argv']+['--episode','S01E49']):
            with self.subTest(argv=argv),patch.object(watch.guard.thermal,'process',return_value={**self.worker,'argv':argv}), \
                 self.assertRaises(ValueError):watch.worker_identity(77,99,'S01E49')
        with patch.object(watch.guard.thermal,'process',return_value={**self.worker,'argv':self.worker['argv']+['--poll','1']}), \
             self.assertRaises(ValueError):watch.worker_identity(77,99,'S01E49',self.worker['argv'])


if __name__=='__main__':unittest.main()
