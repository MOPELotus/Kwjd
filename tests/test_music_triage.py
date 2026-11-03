import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('triage',Path(__file__).parents[1]/'scripts/triage_mihoyo_music.py')
triage=importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)


class MusicTriageTests(unittest.TestCase):
    def score(self, winner, second=None):
        result=dict.fromkeys(triage.PROMPTS,.1)
        result[winner]=.4
        if second: result[second]=.395
        return result

    def test_contradictory_excerpts_remain_pending(self):
        label,_=triage.decide([self.score('calm'),self.score('intense')])
        self.assertEqual(label,'混合/待复核')

    def test_ambiguous_cosine_is_not_confident(self):
        label,_=triage.decide([self.score('calm','cheerful')]*3)
        self.assertEqual(label,'边界不清/待复核')

    def test_intense_is_excluded_candidate(self):
        label,_=triage.decide([self.score('intense')]*3)
        self.assertIn('暂不入视频',label)

    def test_locations_do_not_cross_game(self):
        self.assertEqual(triage.locations('StarRail','Mondstadt 夜景'),[])
        self.assertEqual(triage.locations('Genshin','Qingce Night')[0]['place'],'轻策庄')

    def test_unknown_location_not_invented(self):
        self.assertEqual(triage.locations('Genshin','Pure Sky'),[])

    def test_album_context_is_separate_from_track_location(self):
        context=triage.album_context('Genshin','原神-智妙明论之林 Forest of Jnana and Vidya')
        self.assertEqual(context[0]['region'],'须弥')
        self.assertTrue(context[0]['source'].startswith('https://'))
        self.assertEqual(triage.locations('Genshin','Gilded Runner'),[])
        self.assertEqual(triage.album_context('ZZZ','智妙明论之林'),[])

    def test_day_night_hints_remain_literal(self):
        self.assertEqual(triage.time_hint('龙脊雪山'), '时段待核')
        self.assertEqual(triage.time_hint('Qingce Night'), '夜晚')
        self.assertEqual(triage.time_hint('Dragonspine day'), '白天')
        self.assertEqual(triage.time_hint('昼夜变化'), '昼夜变化')

    def test_originals_available_but_excluded_assets_do_not_imply_ready(self):
        self.assertTrue(triage.available_asset(dict(status='原视频直接通过（抽样）')))
        self.assertFalse(triage.available_asset(dict(status='排除/仅备用')))
        self.assertFalse(triage.pending_video_asset(dict(status='仅静态或引擎场景/无原视频')))
        self.assertFalse(triage.pending_video_asset(dict(status='排除/仅备用')))


if __name__=='__main__': unittest.main()
