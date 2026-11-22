import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import wait_jigong_post_input as m

class WaitTests(unittest.TestCase):
    def test_quota_failure_still_observes_ready_input_without_inventing_balance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(m,'quota',side_effect=RuntimeError('TLS read failed')),patch.object(m,'master_ready',return_value=True):
                self.assertEqual(m.wait(root,'master'),0)
            state=json.loads((root/'post/managed-S03E02/progress.json').read_text())
            self.assertNotIn('quota_remaining_percent',state)
            self.assertTrue(state['input_wait']['ready'])
            self.assertIn('TLS',state['quota_read_error'])

    def test_low_real_quota_stops_before_input_access(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(m,'quota',return_value=4),patch.object(m,'master_ready') as ready:
            self.assertEqual(m.wait(Path(directory),'master'),75);ready.assert_not_called()

if __name__=='__main__':unittest.main()
