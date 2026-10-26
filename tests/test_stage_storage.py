from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import stage_storage as storage
from enhance_stage import WaitDisk


class StorageTests(unittest.TestCase):
    def test_selected_directory_is_writable_and_budget_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate=Path(tmp)/'scratch'
            with patch.object(storage,'check_space',return_value=99) as check:
                selected,record=storage.choose_scratch(tmp,42,candidates=[candidate])
            self.assertEqual(selected,candidate);self.assertTrue(selected.is_dir())
            self.assertEqual(record['additional_peak_bytes'],42)
            check.assert_called_once_with(candidate,42,25)

    def test_two_directories_on_same_full_disk_are_not_two_capacities(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(storage,'check_space',side_effect=WaitDisk('full')) as check:
                with self.assertRaisesRegex(WaitDisk,'No user-owned'):
                    storage.choose_scratch(tmp,42,candidates=[Path(tmp)/'a',Path(tmp)/'b'])
            self.assertEqual(check.call_count,1)
            self.assertFalse((Path(tmp)/'a').exists())

    def test_configured_ram_scratch_is_not_durable_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(storage,'filesystem_kind',return_value='tmpfs'), \
                 patch.object(storage,'check_space') as check:
                with self.assertRaises(WaitDisk):storage.choose_scratch(tmp,42,candidates=[Path(tmp)/'ram'])
                check.assert_not_called()


if __name__=='__main__':unittest.main()
