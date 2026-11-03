import hashlib
import sys
import tempfile
from pathlib import Path
import subprocess
import unittest
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import audit_music_duplicates as audit


class DuplicateTests(unittest.TestCase):
    def test_native_format_is_part_of_identity(self):
        a=dict(sample_rate=44100,channels=2,pcm_sha256='same')
        b=dict(a,sample_rate=48000)
        self.assertNotEqual(audit.pcm_identity(a),audit.pcm_identity(b))
        self.assertNotEqual(audit.pcm_identity(a),audit.pcm_identity(dict(a,channels=1)))

    @unittest.skipUnless(Path('D:/Apps/ffmpeg/ffmpeg.exe').exists(),'local ffmpeg unavailable')
    def test_metadata_variants_have_identical_full_pcm_but_different_file_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'a.wav'
            with wave.open(str(source),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
                f.writeframes(b''.join(int((i%100)*100-5000).to_bytes(2,'little',signed=True) for i in range(16000)))
            ffmpeg='D:/Apps/ffmpeg/ffmpeg.exe';results=[];hashes=[]
            for title in ['one','two']:
                dest=root/(title+'.flac')
                subprocess.run([ffmpeg,'-v','error','-i',str(source),'-metadata','title='+title,str(dest)],check=True)
                digest=audit.sha(dest);hashes.append(digest)
                results.append(audit.decode((dict(file=str(dest),sha256=digest,duration=1),dict(streams=[dict(sample_rate=16000,channels=1)]),ffmpeg)))
            self.assertNotEqual(*hashes)
            self.assertEqual(audit.pcm_identity(results[0]),audit.pcm_identity(results[1]))
            with self.assertRaises(ValueError):
                audit.decode((dict(file=str(dest),sha256='wrong',duration=1),dict(streams=[]),ffmpeg))


if __name__=='__main__':unittest.main()
