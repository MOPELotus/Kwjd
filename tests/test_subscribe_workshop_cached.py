import importlib.util
from pathlib import Path
import unittest
from copy import deepcopy

spec = importlib.util.spec_from_file_location('cached', Path(__file__).parents[1]/'scripts/subscribe_workshop_cached.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CachedSubscriptionTests(unittest.TestCase):
    def test_wrong_app_rejected_before_network(self):
        with self.assertRaises(ValueError):
            m.approved_rows([{'publishedfileid':'1','result':1,'consumer_app_id':42}], ['1'])

    def test_interrupted_write_is_verified_before_replay(self):
        state = {'plan':[{'id':'1'}], 'results':{}}
        saved = []
        class Client:
            subscribed = False
            writes = 0
            def state(self, item):
                return 'subscribed' if self.subscribed else 'unsubscribed'
            def subscribe(self, item):
                self.writes += 1
                self.subscribed = True
                raise RuntimeError('connection lost after server accepted')
        c = Client()
        with self.assertRaises(RuntimeError):
            m.execute(c, state, lambda x:saved.append(deepcopy(x)), RuntimeError)
        self.assertEqual(saved[0]['results']['1']['status'], 'pending_verification')
        m.execute(c, state, lambda x:None, RuntimeError)
        self.assertEqual(c.writes, 1)
        self.assertEqual(state['status'], 'COMPLETED')

    def test_unknown_state_never_writes(self):
        class Client:
            def state(self, item): return 'unknown'
            def subscribe(self, item): raise AssertionError('must not write')
        state = {'plan':[{'id':'1'}], 'results':{}}
        m.execute(Client(), state, lambda x:None, RuntimeError)
        self.assertEqual(state['results']['1']['status'], 'skipped_unknown')


if __name__ == '__main__': unittest.main()
