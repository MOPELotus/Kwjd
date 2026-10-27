from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import post_dashboard as dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.post=Path(self.tmp.name);self.now=datetime(2026,10,27,1,tzinfo=timezone.utc)
        for directory in ('reports','jobs','backend','logs'):(self.post/directory).mkdir()
        self.job={'episode':'S01E49','state':'ENHANCING','master_sha256':'a'*64,
                  'updated_at':(self.now-timedelta(hours=1)).isoformat()}
        self.progress={'status':'known','source_frames_complete':750,'source_frames_total':1000,
                       'verified_segments':3,'percent':75,'backend_fingerprint':'b'*64}
        context=self.post/'cache/S01E49/S01E49.segments'/('a'*16+'-'+'b'*16)/'context.json'
        context.parent.mkdir(parents=True)
        context.write_text(json.dumps({'master_sha256':'a'*64,'backend_fingerprint':'b'*64}))
        self.progress['context']=str(context)
        self.snap={'captured_at':self.now.isoformat(),'jobs':[{'episode':'S01E49','segment_progress':self.progress}],
                   'gpu_csv_fields':'index,name,temperature.gpu,clocks.sm,utilization.gpu,memory.used,memory.free,power.draw,clocks_event_reasons.sw_thermal_slowdown',
                   'gpu_csv_rows':['1, Tesla T10, 88, 300, 21, 1083, 14848, 67.66, Active'],
                   'filesystems':{'/cache':{'free_bytes':30*1024**3}},'monitor_pid':123}
        self.eta={'episode':'S01E49','master_sha256':'a'*64,'backend_fingerprint':'b'*64,'worker_pid':456,
                  'estimated_hours_from_full_start_through_final_checks':[11,14],
                  'expected_completion_beijing_range':['2026-10-27T19:07:00+08:00','2026-10-27T22:07:00+08:00']}
        self.write('jobs/S01E49.json',self.job);self.write('reports/live_status.json',self.snap)
        self.write('reports/S01E49.eta.json',self.eta)
        self.write('backend/production-ready.json',{'master_sha256':'a'*64,'backend_fingerprint':'b'*64})

    def write(self,path,value):
        (self.post/path).write_text(json.dumps(value))

    def log(self,rows):
        path=self.post/'logs/S01E49.log';path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        os.utime(path,(self.now.timestamp(),self.now.timestamp()))

    def record(self,start,frames,wall,**changes):
        return {'cuda_pipeline':True,'episode':'S01E49','source_start':start,
                'input_frames':frames,'wall_seconds':wall,**changes}

    def test_recent_rate_is_weighted_and_uses_last_three_unique_segments(self):
        rows=[self.record(0,250,50),self.record(250,100,20),self.record(500,50,10),
              self.record(250,100,40),self.record(750,25,0),self.record(999,1,1,episode='S01E50')]
        rate=dashboard.recent_rate([json.dumps(x) for x in rows],'S01E49')
        self.assertEqual(rate['input_frames'],400);self.assertEqual(rate['wall_seconds'],100)
        self.assertEqual(rate['source_fps'],4);self.assertEqual(rate['segments'],3)

    def test_current_snapshot_renders_real_receipt_and_throughput(self):
        self.log([self.record(0,250,100),self.record(250,250,100),self.record(500,250,100)])
        view=dashboard.collect(self.post,'S01E49',self.now)
        with patch.object(dashboard,'process_status',return_value='存活（进程身份匹配）'):
            text='\n'.join(dashboard.render(view))
        for expected in ('真实 AI 修复与补帧','75.000%','750/1,000','00:00:30 / 00:00:40',
                         '2.500 源帧/秒','不含后续字幕烧录','11–14 小时','Tesla T10','Active','30.00 GiB',
                         '显存已用/空闲','1083/14848 MiB'):
            self.assertIn(expected,text)
        self.assertFalse(view['stale'])

    def test_expired_eta_is_not_reported_as_zero_remaining_or_completion(self):
        self.eta['expected_completion_beijing_range']=[
            (self.now-timedelta(hours=2)).isoformat(),(self.now-timedelta(hours=1)).isoformat()]
        self.write('reports/S01E49.eta.json',self.eta)
        text='\n'.join(dashboard.render(dashboard.collect(self.post,'S01E49',self.now)))
        self.assertIn('原 ETA 窗口已过',text)
        self.assertNotIn('预计剩余：00:00:00',text)

    def test_stale_snapshot_is_explicit_and_does_not_claim_live_throughput(self):
        self.log([self.record(500,250,100)])
        self.snap['captured_at']=(self.now-timedelta(seconds=121)).isoformat()
        self.write('reports/live_status.json',self.snap)
        view=dashboard.collect(self.post,'S01E49',self.now)
        self.assertTrue(view['stale']);self.assertIsNone(view['rate'])
        text='\n'.join(dashboard.render(view));self.assertIn('STALE',text)
        self.assertIn('吞吐：unknown',text)

    def test_old_source_or_backend_evidence_is_not_reused(self):
        self.progress['backend_fingerprint']='c'*64
        self.write('reports/live_status.json',self.snap)
        self.eta['master_sha256']='d'*64;self.write('reports/S01E49.eta.json',self.eta)
        view=dashboard.collect(self.post,'S01E49',self.now)
        self.assertEqual(view['progress']['status'],'unknown');self.assertEqual(view['eta'],{})
        self.assertIsNone(view['worker_pid'])
        self.assertIn('分段进度：unknown','\n'.join(dashboard.render(view)))

    def test_log_reader_is_bounded_and_latest_lines_survive(self):
        path=self.post/'logs/S01E49.log'
        path.write_bytes(b'OLD-SENTINEL\n'+b'x'*(dashboard.TAIL_BYTES*2)+b'\nnew line one\nnew line two\n')
        lines,_=dashboard.tail_log(path)
        self.assertEqual(lines,['new line one','new line two'])
        self.assertNotIn('OLD-SENTINEL',''.join(lines))

    def test_other_source_snapshot_with_same_backend_is_unknown(self):
        self.progress['context']=self.progress['context'].replace('a'*16,'c'*16)
        self.write('reports/live_status.json',self.snap)
        self.assertEqual(dashboard.collect(self.post,'S01E49',self.now)['progress']['status'],'unknown')

    def test_missing_or_partial_files_render_unknown_without_crash(self):
        for path in self.post.rglob('*.json'):path.write_text('{incomplete')
        view=dashboard.collect(self.post,'S01E49',self.now)
        text='\n'.join(dashboard.render(view))
        self.assertIn('STALE',text);self.assertIn('unknown',text)
        self.assertEqual(dashboard.tail_log(self.post/'missing'),([],None))

    def test_recycled_pid_and_small_screen_clipping(self):
        with patch.object(Path,'read_bytes',return_value=b'/usr/bin/unrelated-service\0'):
            self.assertIn('已复用',dashboard.process_status(123,'server_worker.py'))
        self.assertEqual(dashboard.clip('中文abc',5),'中文a')
        self.assertEqual(dashboard.clip('中文abc',1),'')
        self.assertNotIn('\x1b',dashboard.clip('\x1b[31mtext',20))


if __name__=='__main__':unittest.main()
