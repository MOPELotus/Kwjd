import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from upload_validated_master import wait_for_admission


class CapacityAdmission(unittest.TestCase):
    def record(self, **updates):
        value = dict(episode='S03E08', expected_master_sha256='a' * 64,
                     expected_master_bytes=123, checked_at=1000, admitted=True,
                     assessment={'allowed': True})
        value.update(updates)
        return value

    def exercise(self, record, waiting=False):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(remote_root='/data/E08', receipt=Path(folder) / 'receipt.json',
                                   validation=Path(folder) / 'validation.json')
            response = SimpleNamespace(returncode=0, stdout=json.dumps(record))
            with patch('upload_validated_master.remote', return_value=response), \
                    patch('upload_validated_master.time.time', return_value=1000), \
                    patch('upload_validated_master.time.sleep', side_effect=RuntimeError('waiting')):
                if waiting:
                    with self.assertRaisesRegex(RuntimeError, 'waiting'):
                        wait_for_admission(args, [], 'S03E08', 'a' * 64, 123)
                    state = json.loads(args.receipt.with_suffix('.transfer-state.json').read_text())
                    self.assertEqual(state['stage'], 'WAIT_REMOTE_CAPACITY')
                else:
                    wait_for_admission(args, [], 'S03E08', 'a' * 64, 123)

    def test_fresh_matching_budget_allows(self):
        self.exercise(self.record())

    def test_stale_budget_cannot_start_upload(self):
        self.exercise(self.record(checked_at=800), waiting=True)

    def test_full_disk_cannot_start_upload(self):
        self.exercise(self.record(admitted=False, assessment={'allowed': False}), waiting=True)

    def test_other_master_cannot_authorize_upload(self):
        with self.assertRaisesRegex(ValueError, 'different master'):
            self.exercise(self.record(expected_master_sha256='b' * 64))


if __name__ == '__main__':
    unittest.main()
