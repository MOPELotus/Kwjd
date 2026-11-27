import json
from pathlib import Path
import unittest
from test_subtitle_review import ReviewGateTests
from subtitle_review_supervisor import validate_episode,save
from subtitle_review_cleanup import retire,plan


class LegacyPayloadRetirementTests(unittest.TestCase):
    def setUp(self):
        self.fixture=ReviewGateTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;save(f.path,f.report);self.root=f.root;self.review=f.root/'ocr/subtitle-review'
        bulk=self.review/'evidence/S03E02/gap-allframes-wide-v1';bulk.mkdir(parents=True)
        self.disposable=bulk/'synthetic-redundant.jpg';self.disposable.write_bytes(b'synthetic retired raster')
        self.protected=bulk/'synthetic-gap-recovery.png';self.protected.write_bytes(b'synthetic concrete correction evidence')
        decisions=self.review/'decisions/S03E02';decisions.mkdir(parents=True)
        save(decisions/'synthetic.json',{'frames_viewed':[str(self.protected)]})
        self.index=bulk/'evidence.json';self.index.write_text('{"historical_index": true}')
        self.active=self.review/'evidence/S03E10/current.jpg';self.active.parent.mkdir(parents=True);self.active.write_bytes(b'active evidence')

    def test_dry_run_does_not_delete_or_publish_retirement(self):
        result=retire(self.root,'S03E02')
        self.assertEqual(result['files'],1);self.assertTrue(self.disposable.exists())
        self.assertFalse((self.review/'retirements').exists())

    def test_only_redundant_rasters_removed_and_existing_seal_remains_valid(self):
        original=self.fixture.final.read_bytes();result=retire(self.root,'S03E02',True)
        self.assertEqual(result['files_removed'],1);self.assertFalse(self.disposable.exists())
        self.assertTrue(self.protected.exists());self.assertTrue(self.index.exists());self.assertTrue(self.active.exists())
        self.assertEqual(self.fixture.final.read_bytes(),original)
        self.assertTrue(validate_episode(self.root,'S03E02'))
        self.assertEqual(retire(self.root,'S03E02',True),result)

    def test_unsealed_review_and_current_episode_cannot_be_retired(self):
        self.fixture.seal.unlink()
        with self.assertRaisesRegex(ValueError,'sealed'):retire(self.root,'S03E02',True)
        self.assertTrue(self.disposable.exists())
        with self.assertRaises(ValueError):plan(self.root,'S03E10')

    def test_changed_retirement_manifest_invalidates_review(self):
        result=retire(self.root,'S03E02',True)
        Path(result['inventory']['path']).write_text('changed')
        self.assertFalse(validate_episode(self.root,'S03E02'))
