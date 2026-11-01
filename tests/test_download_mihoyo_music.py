import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('download',Path(__file__).parents[1]/'scripts/download_mihoyo_music.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class AudioAdmissionTests(unittest.TestCase):
    def test_encrypted_and_short_trials_rejected(self):
        base={'available':True,'url':'https://example.invalid/a','format':'flac','duration_ms':180000}
        for changes in ({'format':'nac'},{'duration_ms':30000},{'extensions':{'trial':True}}):
            self.assertFalse(m.native_candidate(dict(base,**changes),180000))
        self.assertTrue(m.native_candidate(base,180000))

    def test_lossless_preferred_over_larger_lossy_bitrate(self):
        self.assertGreater(m.candidate_score({'format':'flac','bitrate':700000}),m.candidate_score({'format':'mp3','bitrate':900000}))

if __name__=='__main__':unittest.main()
