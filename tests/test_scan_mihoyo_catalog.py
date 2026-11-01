import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('scan',Path(__file__).parents[1]/'scripts/scan_mihoyo_catalog.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class PaginationTests(unittest.TestCase):
    def test_follows_upstream_offset_instead_of_requested_limit(self):
        self.assertEqual(m.next_offset({'meta':{'pagination':{'has_more':True,'next_offset':30}}},0),30)

    def test_repeated_or_missing_cursor_rejected(self):
        for value in (None,0,30):
            with self.assertRaises(ValueError):
                m.next_offset({'meta':{'pagination':{'has_more':True,'next_offset':value}}},30)

    def test_null_total_can_still_terminate(self):
        self.assertIsNone(m.next_offset({'meta':{'pagination':{'has_more':False,'total':None}}},100))

if __name__=='__main__':unittest.main()
