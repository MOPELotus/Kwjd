import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from jigong_master_restart import OLD_JIGONG_MASTERS, enforce


class JigongRestart(unittest.TestCase):
    def test_every_known_old_master_is_rejected_even_if_rate_claim_changes(self):
        for episode, digest in OLD_JIGONG_MASTERS.items():
            with self.subTest(episode=episode), self.assertRaisesRegex(ValueError, 'Superseded'):
                enforce(episode, digest, '25/1')

    def test_new_cfr30_jigong_master_is_not_a_replacement(self):
        with self.assertRaisesRegex(ValueError, 'native25'):
            enforce('S03E02', 'a' * 64, '30/1')

    def test_new_native25_identity_is_eligible_for_remaining_checks(self):
        enforce('S03E02', 'a' * 64, '25/1')

    def test_other_story_policy_is_not_changed(self):
        enforce('S01E49', 'a' * 64, '30/1')


if __name__ == '__main__':
    unittest.main()
