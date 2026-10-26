import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from enhance_stage import sha256
from subtitle_timing import read_mapping, apply_offset

ASS = ('[Script Info]\r\nTitle: preserve\r\n[V4+ Styles]\r\nStyle: original\r\n'
       '[Events]\r\nFormat: Layer, Start, End, Style, Text\r\n'
       'Dialogue: 0,0:00:01.00,0:00:02.00,Default,原 文,带逗号\\N第二行\r\n'
       'Comment: 0,0:00:01.00,0:00:02.00,Default,unchanged\r\n')


class SubtitleTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'receipts').mkdir()
        (self.root / 'reports').mkdir()
        self.evidence = self.root / 'reports/sync.json'
        self.evidence.write_text('{"visual_checked":true}')
        self.receipt = self.root / 'receipts/S01E49.subtitle-timing.json'
        self.data = dict(schema_version=1, reviewed=True, episode='S01E49',
            master_sha256='a'*64, srt_sha256='b'*64, offset_ms=-99,
            evidence_path='reports/sync.json', evidence_sha256=sha256(self.evidence))
        self.ass = self.root / 'derived.ass'
        self.ass.write_bytes(ASS.encode())

    def write_receipt(self):
        self.receipt.write_text(json.dumps(self.data))

    def read(self):
        return read_mapping(self.root, 'S01E49', 'a'*64, 'b'*64)

    def test_absent_mapping_defaults_and_reviewed_mapping_binds_hashes(self):
        self.assertEqual(self.read(), {'offset_ms': 0, 'receipt_sha256': None})
        self.write_receipt()
        result = self.read()
        self.assertEqual(result['offset_ms'], -99)
        self.assertEqual(result['receipt_sha256'], sha256(self.receipt))
        self.evidence.write_text('changed')
        with self.assertRaises(ValueError): self.read()

    def test_stale_binding_unreviewed_and_noninteger_offsets_rejected(self):
        original = self.data.copy()
        for key, value in [('master_sha256', 'c'*64), ('srt_sha256', 'd'*64),
                ('episode', 'S01E50'), ('reviewed', False), ('schema_version', True),
                ('offset_ms', True), ('offset_ms', 1001), ('offset_ms', -1001)]:
            self.data = original.copy()
            self.data[key] = value
            self.write_receipt()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.read()

    def test_evidence_escape_and_internal_symlink_rejected(self):
        for path in ('../outside.json', 'receipts/not-report.json'):
            self.data['evidence_path'] = path
            self.write_receipt()
            with self.assertRaises(ValueError): self.read()
        link = self.root / 'reports/link.json'
        link.symlink_to(self.evidence)
        self.data['evidence_path'] = 'reports/link.json'
        self.write_receipt()
        with self.assertRaises(ValueError): self.read()

    def test_offset_preserves_text_styles_comments_and_records_edits(self):
        audit = apply_offset(self.ass, -99)
        expected = ASS.replace('Dialogue: 0,0:00:01.00,0:00:02.00',
                               'Dialogue: 0,0:00:00.90,0:00:01.90')
        self.assertEqual(self.ass.read_bytes(), expected.encode())
        self.assertEqual(audit['changed_count'], 1)
        self.assertEqual(audit['edits'][0]['before'], ['0:00:01.00', '0:00:02.00'])
        apply_offset(self.ass, 5)
        self.assertIn(b'0:00:00.91,0:00:01.91', self.ass.read_bytes())

    def test_invalid_interval_or_negative_offset_fails_without_partial_write(self):
        for content, offset in [(ASS, -1000),
                (ASS.replace('0:00:02.00', 'invalid', 1), 0),
                (ASS.replace('0:00:02.00', '0:00:00.50', 1), 0)]:
            # First example starts at zero, so negative mapping must fail.
            if offset == -1000:
                content = content.replace('0:00:01.00', '0:00:00.00', 1)
            self.ass.write_bytes(content.encode())
            before = self.ass.read_bytes()
            with self.assertRaises(ValueError): apply_offset(self.ass, offset)
            self.assertEqual(self.ass.read_bytes(), before)


if __name__ == '__main__': unittest.main()
