from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from upload_validated_master import sftp_upload_line


class SftpStart(unittest.TestCase):
    def test_fresh_target_is_created_not_stat_required(self):
        self.assertEqual(sftp_upload_line(Path('master.mov'),'/incoming/master.uploading',0),
                         'put "master.mov" "/incoming/master.uploading"\n')

    def test_existing_verified_prefix_resumes(self):
        self.assertTrue(sftp_upload_line(Path('master.mov'),'/incoming/master.uploading',1024).startswith('reput '))


if __name__=='__main__':unittest.main()
