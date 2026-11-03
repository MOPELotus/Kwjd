import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import music_metadata_sources as m

class MetadataTests(unittest.TestCase):
    def test_nested_references_and_booleans(self):
        r=m.LiteralReader('$R[0]={a:$R[1]=["夜晚",!0],b:$R[1],none:void 0}')
        self.assertEqual(r.read(),dict(a=['夜晚',True],b=['夜晚',True],none=None))

    def test_code_is_never_executed(self):
        for text in ['process.exit(1)','(()=>42)()','new Function("return 1")']:
            with self.assertRaises(ValueError):m.LiteralReader(text).read()

    def test_quoted_escape_and_html(self):
        self.assertEqual(m.bootstrap('<script>prefix;$R[0]={name:"a\\\"b",jp:"\\x41"};</script>'),dict(name='a"b',jp='A'))

if __name__=='__main__':unittest.main()
