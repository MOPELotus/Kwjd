from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from receive_master_handoff import incoming_budget


class IncomingBudget(unittest.TestCase):
    def test_receive_does_not_charge_other_filesystems_outputs(self):
        gib=1024**3
        result=incoming_budget(Path('.'),8*gib,free_bytes=14*gib)
        self.assertEqual(result['remaining_master_bytes'],8*gib)
        with self.assertRaises(RuntimeError):
            incoming_budget(Path('.'),8*gib,free_bytes=12*gib)

    def test_resume_only_reserves_untransferred_bytes(self):
        gib=1024**3
        with tempfile.TemporaryDirectory() as tmp:
            partial=Path(tmp)/'partial'
            partial.write_bytes(b'x'*1024)
            result=incoming_budget(Path(tmp),2048,partial,free_bytes=5*gib+1024)
            self.assertEqual(result['remaining_master_bytes'],1024)
            with self.assertRaises(ValueError):
                incoming_budget(Path(tmp),512,partial,free_bytes=20*gib)


if __name__=='__main__':unittest.main()
