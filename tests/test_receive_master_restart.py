"""Reject retired upload preflights before touching any receiving state."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from jigong_master_restart import OLD_JIGONG_MASTERS
from receive_master_handoff import main


class ReceiveRestartTests(unittest.TestCase):
    def invoke(self,root,digest,rate):
        args=['receive_master_handoff','--root',str(root),'--episode','S03E02',
              '--sha256',digest,'--bytes','123','--expected-duration','60',
              '--expected-frame-rate',rate,'--partial-info']
        with patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):main()

    def test_old_sha_rejects_even_a_native25_claim_before_creating_incoming(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaisesRegex(ValueError,'Superseded'):
                self.invoke(root,OLD_JIGONG_MASTERS['S03E02'],'25/1')
            self.assertFalse((root/'post').exists())

    def test_new30_upload_preflight_cannot_create_partial_or_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaisesRegex(ValueError,'native25'):self.invoke(root,'a'*64,'30/1')
            self.assertFalse((root/'post').exists())

    def test_new25_only_reaches_normal_missing_partial_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(SystemExit) as error:self.invoke(root,'a'*64,'25/1')
            self.assertEqual(error.exception.code,44)
            self.assertFalse((root/'post/handoffs').exists())


if __name__=='__main__':unittest.main()
