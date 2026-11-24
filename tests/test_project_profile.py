import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ProfileTests(unittest.TestCase):
    def run_profile(self,profile,code):
        env=dict(os.environ)
        env.pop('KWJD_PROJECT',None)
        if profile is not None:env['KWJD_PROJECT']=profile
        return subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True)

    def test_default_is_s01(self):
        result=self.run_profile(None,"import kwjd; print(kwjd.episode(2))")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'S01E02')

    def test_s03_worker_accepts_only_exact_output_path(self):
        code="""import sys;sys.path.insert(0,'scripts')
import kwjd,server_worker
assert kwjd.episode(2)=='S03E02'
assert kwjd.PROJECT['managed_post_episodes']==[f'S03E{i:02d}' for i in range(2,15)]
server_worker.check_rel('../output/S03E02.zh.srt')
for bad in ['../output/S01E02.zh.srt','../output/S03E02/../../x.zh.srt','../output/S03E02evil.zh.srt']:
    try:server_worker.check_rel(bad)
    except ValueError:pass
    else:raise AssertionError(bad)
"""
        result=self.run_profile('config/projects/jigong-s03.json',code)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_profile_cannot_escape_versioned_config(self):
        result=self.run_profile('/etc/passwd','import kwjd')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('versioned under repository config',result.stderr)

if __name__=='__main__':unittest.main()
