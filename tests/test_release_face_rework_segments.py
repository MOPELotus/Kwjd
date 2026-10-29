"""Destructive stage-release boundaries; no model, GPU or real media needed."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import release_face_rework_segments as subject


class SegmentReleaseBoundaries(unittest.TestCase):
    def fixture(self, root):
        work = root / 'post-home/experiments/rework'
        for path in (work / 'segments', root / 'post/jobs', work / 'audit-complete-0814'):
            path.mkdir(parents=True)
        final = root / 'retained-final.mkv'
        final.write_bytes(b'never delete the full final')
        (work / 'server_acceptance.json').write_text('{}')
        members = []
        for index in range(352):
            path = work / 'segments' / f'segment-{index:05d}.mkv'
            path.write_bytes(b'verified encoding segment')
            members.append({'name': path.name, 'sha256': subject.digest(path),
                            'bytes': path.stat().st_size})
        plan = {'final_sha256': 'verified-final', 'members': members,
                'acceptance_sha256': subject.digest(work / 'server_acceptance.json'),
                'total_bytes': sum(x['bytes'] for x in members)}
        (work / 'segment-release-plan.json').write_text(json.dumps(plan))
        audit = {'segments': [{'segment': Path(x['name']).stem,
                              'media_sha256': x['sha256']} for x in members]}
        (work / 'audit-complete-0814/checkpoint-audit.json').write_text(json.dumps(audit))
        return work, final, plan

    def test_live_worker_prevents_any_publication_or_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'post-home/experiments/rework'
            with patch.object(subject.subprocess, 'check_output', return_value='ActiveState=active\nMainPID=123\n'), \
                    patch.object(subject, 'publish') as publisher:
                with self.assertRaisesRegex(ValueError, 'terminal'):
                    subject.release(root, work, True)
                publisher.assert_not_called()

    def reject(self, change, message):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, final, plan = self.fixture(root)
            change(work, plan)
            with patch.object(subject.subprocess, 'check_output', return_value='ActiveState=inactive\nMainPID=0\n'), \
                    patch.object(subject.subprocess, 'run') as systemctl, \
                    patch.object(subject, 'publish', return_value={'path': str(final), 'sha256': 'verified-final'}), \
                    patch.object(subject, 'release_verified_file') as unlinker:
                systemctl.return_value.stdout = 'disabled\n'
                with self.assertRaisesRegex(ValueError, message):
                    subject.release(root, work, True)
                unlinker.assert_not_called()
            self.assertEqual(final.read_bytes(), b'never delete the full final')

    def test_tampered_plan_cannot_escape_segment_directory(self):
        def change(work, plan):
            plan['members'][0]['name'] = '../../retained-final.mkv'
            (work / 'segment-release-plan.json').write_text(json.dumps(plan))
        self.reject(change, 'cleanup member')

    def test_changed_segment_is_preserved(self):
        def change(work, plan):
            (work / 'segments' / plan['members'][0]['name']).write_bytes(b'replaced since audit')
        self.reject(change, 'Segment changed')


if __name__ == '__main__':
    unittest.main()
