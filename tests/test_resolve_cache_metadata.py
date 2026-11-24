import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from resolve_cache import snapshots_match


class CacheMetadata(unittest.TestCase):
    def baseline(self):
        return dict(exists=True,entries=[
            dict(path='.',directory=True,bytes=0,mtime_ns=1),
            dict(path='project/Info.txt',directory=False,bytes=74,mtime_ns=1,content_sha256='a'*64),
            dict(path='project/frame.dvcc',directory=False,bytes=100,mtime_ns=1)])

    def test_same_metadata_content_does_not_block_release(self):
        old=self.baseline();new=copy.deepcopy(old)
        new['entries'][0]['mtime_ns']=3;new['entries'][1]['mtime_ns']=3
        self.assertTrue(snapshots_match(old,new))

    def test_modified_media_or_metadata_still_blocks(self):
        for index,key,value in [(2,'mtime_ns',3),(2,'bytes',101),(1,'content_sha256','b'*64)]:
            old=self.baseline();new=copy.deepcopy(old);new['entries'][index][key]=value
            self.assertFalse(snapshots_match(old,new))
        old=self.baseline();new=copy.deepcopy(old);new['entries'][0]['mtime_ns']=2
        self.assertFalse(snapshots_match(old,new))

    def test_addition_removal_and_legacy_metadata_change_block(self):
        old=self.baseline();new=copy.deepcopy(old);new['entries'].pop()
        self.assertFalse(snapshots_match(old,new))
        self.assertFalse(snapshots_match(new,old))
        old=self.baseline();old['entries'][1].pop('content_sha256')
        new=copy.deepcopy(old);new['entries'][1]['mtime_ns']=2
        self.assertFalse(snapshots_match(old,new))


if __name__=='__main__':unittest.main()
