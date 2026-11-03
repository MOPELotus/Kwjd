import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import refine_mihoyo_music as refine


class RefinementTests(unittest.TestCase):
    def score(self,winner,other=None):
        s=dict.fromkeys(refine.PROMPTS,.1)
        s[winner]=.5
        if other:s[other]=.49
        return s

    def test_calm_cheerful_switch_is_not_cross_family_conflict(self):
        review=refine.quick_decision([self.score('calm'),self.score('cheerful'),self.score('calm')])
        self.assertEqual(review['category'],'舒缓候选')

    def test_intense_tense_switch_merges_into_excluded_family(self):
        review=refine.quick_decision([self.score('intense'),self.score('tense'),self.score('intense')])
        self.assertIn('暂不入视频',review['category'])

    def test_real_cross_family_transition_needs_resampling(self):
        self.assertIsNone(refine.quick_decision([self.score('calm'),self.score('intense'),self.score('intense')]))

    def test_small_isolated_subtype_disagreement_does_not_veto_dominant_family(self):
        nearly=self.score('tense');nearly['calm']=.48
        self.assertEqual(refine.quick_decision([self.score('calm'),self.score('calm'),nearly])['category'],'舒缓候选')

    def test_five_window_average_does_not_require_identical_winners(self):
        scores=[self.score(k) for k in ['calm','cheerful','calm','cheerful','calm']]
        self.assertEqual(refine.resampled_decision(scores,[])['category'],'舒缓候选')

    def test_strong_quiet_action_transition_stays_uncertain(self):
        scores=[self.score(k) for k in ['calm','calm','calm','intense','intense']]
        # Keep the average family scores close to represent a genuine mixed track.
        scores[-1]['intense']=.65;scores[-2]['intense']=.65
        self.assertEqual(refine.resampled_decision(scores,[])['category'],'混合/待复核')

    def test_vocal_is_not_mislabeled_as_calm_because_of_slow_rhythm(self):
        result=refine.resampled_decision([self.score('vocal')]*5,[dict(onsets_per_second=0,pulse_strength=0)])
        self.assertIn('人声',result['category'])

    def test_exact_cross_family_tie_is_not_forced(self):
        result=refine.resampled_decision([dict(calm=.4,cheerful=.2,intense=.4,tense=.2,vocal=.1)]*5,[])
        self.assertEqual(result['category'],'混合/待复核')


if __name__=='__main__':unittest.main()
