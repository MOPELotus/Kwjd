import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from chunked_sftp_download import assemble, exclusive, fetch_chunk, ranges
from episode_lifecycle import save


class ChunkTests(unittest.TestCase):
    def test_prefix_and_tail_ranges_cover_once(self):
        self.assertEqual(ranges(3, 12, 4), [{'offset':3,'bytes':4},{'offset':7,'bytes':4},{'offset':11,'bytes':1}])

    def test_unordered_chunks_assemble_without_replacing_until_sha_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);part=root/'file.downloading';part.write_bytes(b'prefix')
            one=root/'one';one.write_bytes(b'ABC');two=root/'two';two.write_bytes(b'DE')
            proofs=[dict(offset=9,bytes=2,path=str(two)),dict(offset=6,bytes=3,path=str(one))]
            with self.assertRaisesRegex(ValueError,'Whole SHA'):
                assemble(part,6,proofs,'0'*64,11)
            self.assertEqual(part.read_bytes(),b'prefix')
            self.assertTrue(one.exists() and two.exists())
            assemble(part,6,proofs,hashlib.sha256(b'prefixABCDE').hexdigest(),11)
            self.assertEqual(part.read_bytes(),b'prefixABCDE')

    def test_gap_retains_original_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);part=root/'file';part.write_bytes(b'ABC');chunk=root/'chunk';chunk.write_bytes(b'X')
            with self.assertRaisesRegex(ValueError,'Gap'):
                assemble(part,3,[dict(offset=4,bytes=1,path=str(chunk))],'0'*64,5)
            self.assertEqual(part.read_bytes(),b'ABC')

    def test_exclusive_lock_rejects_second_writer_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'lock'
            with exclusive(path):
                with self.assertRaises(OSError):
                    with exclusive(path):pass
            with exclusive(path):pass

    def test_verified_chunk_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'000000000004-6.chunk';target.write_bytes(b'CCDDEE')
            proof=dict(offset=4,bytes=6,path=str(target),sha256=hashlib.sha256(b'CCDDEE').hexdigest())
            save(root/'000000000004-6.json',proof)
            with patch('chunked_sftp_download.subprocess.Popen') as launch:
                self.assertEqual(fetch_chunk(root,dict(offset=4,bytes=6),'safe',[]),proof)
                launch.assert_not_called()

    def test_failed_range_resumes_its_partial_without_repeating_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'000000000004-6.partial').write_bytes(b'CC')
            original_launch=subprocess.Popen;calls=[];source=b'AABBCCDDEEFF'
            def launch(command,**kwargs):
                offset=int(command[command.index('--offset')+1]);length=int(command[command.index('--count')+1])
                calls.append((offset,length));fail=len(calls)==1
                payload=source[offset:offset+(2 if fail else length)]
                code=f'import sys;sys.stdout.buffer.write(bytes.fromhex("{payload.hex()}"));sys.exit({7 if fail else 0})'
                return original_launch([sys.executable,'-c',code],**kwargs)
            with patch('chunked_sftp_download.subprocess.Popen',side_effect=launch):
                result=fetch_chunk(root,dict(offset=4,bytes=6),'safe',[])
            self.assertEqual(calls,[(6,4),(8,2)])
            self.assertEqual(Path(result['path']).read_bytes(),b'CCDDEE')


if __name__=='__main__':unittest.main()
