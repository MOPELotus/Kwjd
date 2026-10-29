"""CPU-only scheduling tests: no model, CUDA, FFmpeg or media access."""
import sys
from contextlib import contextmanager
from pathlib import Path
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from native_frame_queue import NativeFrameQueue, FrameQueueCancelled


class NativeFrameQueueTests(unittest.TestCase):
    def test_order_lookahead_and_exclusive_context(self):
        owner=[];calls=[]
        @contextmanager
        def context():
            owner.append(threading.get_ident())
            yield
            calls.append('exit')
        def read(index):
            self.assertEqual(threading.get_ident(),owner[0])
            calls.append(index)
            return index
        for capacity in (1,2):
            owner.clear();calls.clear()
            q=NativeFrameQueue(read,lambda value,index:(value,index),primary_count=4,
                               available=5,capacity=capacity,context=context)
            try:
                frames=[q.get() for _ in range(5)];q.finish()
                self.assertEqual([f.index for f in frames],list(range(5)))
                self.assertEqual([f.lookahead for f in frames],[False]*4+[True])
                self.assertEqual([f.value for f in frames],[(i,i) for i in range(5)])
                self.assertEqual(calls,[0,1,2,3,4,'exit'])
                self.assertEqual(q.stats['produced_primary'],4)
                self.assertEqual(q.stats['produced_lookahead'],1)
                with self.assertRaises(StopIteration):q.get()
            finally:q.close()
            self.assertFalse(q.thread.is_alive())

    def test_backpressure_is_bounded(self):
        for capacity in (1,2):
            reached=threading.Event();calls=[]
            def process(value,index):
                calls.append(index)
                if len(calls)==capacity+1:reached.set()
                return value
            q=NativeFrameQueue(lambda i:i,process,primary_count=10,available=10,capacity=capacity)
            try:
                self.assertTrue(reached.wait(2))
                # Producer has filled queue and owns at most one in-flight frame.
                self.assertEqual(len(calls),capacity+1)
                self.assertEqual(q.items.qsize(),capacity)
                self.assertLessEqual(q.stats['high_water_frames'],capacity)
            finally:q.close()
            self.assertFalse(q.thread.is_alive())

    def test_next_frame_processing_overlaps_consumer_and_final_eof_has_no_lookahead(self):
        processing=threading.Event();consumer_done=threading.Event();calls=[]
        def process(value,index):
            calls.append(index)
            if index==1:
                processing.set()
                if not consumer_done.wait(2):raise RuntimeError('consumer could not overlap')
            return value
        q=NativeFrameQueue(lambda i:i,process,primary_count=2,available=2)
        try:
            self.assertTrue(processing.wait(2))
            first=q.get()
            self.assertEqual(first.value,0)
            self.assertFalse(first.lookahead)
            consumer_done.set()
            self.assertEqual(q.get().value,1)
            q.finish()
            self.assertEqual(calls,[0,1])
            self.assertEqual(q.stats['produced_primary'],2)
            self.assertEqual(q.stats['produced_lookahead'],0)
            with self.assertRaises(StopIteration):q.get()
        finally:
            consumer_done.set();q.close()

    def test_error_propagates_after_ordered_prefix(self):
        failure=ValueError('fake processor failed')
        def process(value,index):
            if index==2:raise failure
            return value
        q=NativeFrameQueue(lambda i:i,process,primary_count=4,available=4)
        try:
            self.assertEqual([q.get().value for _ in range(2)],[0,1])
            with self.assertRaises(ValueError) as caught:q.get()
            self.assertIs(caught.exception,failure)
        finally:q.close()
        self.assertFalse(q.thread.is_alive())

    def test_reader_eof_and_context_exit_errors_propagate(self):
        def read(index):raise EOFError('short fake reader')
        q=NativeFrameQueue(read,lambda v,i:v,primary_count=1,available=1)
        try:
            with self.assertRaisesRegex(EOFError,'short fake reader'):q.get()
        finally:q.close()
        @contextmanager
        def context():
            yield
            raise RuntimeError('context exit')
        q=NativeFrameQueue(lambda i:i,lambda v,i:v,primary_count=1,available=1,context=context)
        try:
            q.get()
            with self.assertRaisesRegex(RuntimeError,'context exit'):q.finish()
        finally:q.close()

    def test_cancel_unblocks_blocked_reader_and_consumer(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def read(index):
            entered.set();release.wait(2)
            return index
        q=NativeFrameQueue(read,lambda v,i:v,primary_count=2,available=2,cancel_reader=release.set)
        def consume():
            try:q.get()
            except FrameQueueCancelled:errors.append('cancelled')
        consumer=threading.Thread(target=consume)
        consumer.start();self.assertTrue(entered.wait(2));q.close();consumer.join(2)
        self.assertFalse(consumer.is_alive());self.assertFalse(q.thread.is_alive())
        self.assertEqual(errors,['cancelled'])

    def test_configuration_is_strict(self):
        for depth in (0,3,True,1.0):
            with self.assertRaises(ValueError):
                NativeFrameQueue(None,None,primary_count=1,available=1,capacity=depth)
        with self.assertRaises(ValueError):
            NativeFrameQueue(None,None,primary_count=2,available=4)


class NativePipelineOptionsTests(unittest.TestCase):
    def test_defaults_and_explicit_limits(self):
        from native_face_pipeline import pipeline_options
        self.assertEqual(pipeline_options({}),dict(capacity=1,poll_seconds=1.5,thermal_timeout=120.))
        self.assertEqual(pipeline_options({'pipeline_prefetch_frames':2,
            'resource_wait_poll_seconds':2,'resource_wait_timeout_seconds':300})['thermal_timeout'],300.)

    def test_invalid_options_fail_before_model_imports(self):
        from native_face_pipeline import NativeFrames
        invalid=[{'pipeline_prefetch':1},{'resource_wait_in_process':'yes'},
                 {'benchmark_native_frame_sha256':1},{'pipeline_prefetch_frames':True},
                 {'resource_wait_poll_seconds':.99},{'resource_wait_poll_seconds':2.01},
                 {'resource_wait_poll_seconds':True},{'resource_wait_timeout_seconds':float('inf')},
                 {'resource_wait_timeout_seconds':float('nan')},{'resource_wait_timeout_seconds':0},
                 {'resource_wait_timeout_seconds':301}]
        with patch.dict(sys.modules,{'face_frame_processor':None,'cuda_trt':None}):
            for cfg in invalid:
                with self.subTest(cfg=cfg),self.assertRaises(ValueError):NativeFrames(cfg)

    def test_callbacks_cover_both_devices_and_restore_calling_context(self):
        from native_face_pipeline import NativeFrames
        calls=[];current=[7]
        class CUDA:
            def is_initialized(self):return True
            @contextmanager
            def device(self,index):
                previous=current[0];current[0]=index;calls.append(('enter',index))
                try:yield
                finally:current[0]=previous;calls.append(('exit',index))
            def synchronize(self,index):calls.append(('sync',index,current[0]))
            def empty_cache(self):calls.append(('empty',current[0]))
        restorer=NativeFrames.__new__(NativeFrames);restorer.torch=SimpleNamespace(cuda=CUDA())
        restorer.synchronize_devices();restorer.empty_device_caches()
        self.assertEqual([row for row in calls if row[0]=='sync'],[('sync',0,0),('sync',1,1)])
        self.assertEqual([row for row in calls if row[0]=='empty'],[('empty',0),('empty',1)])
        self.assertEqual(current[0],7)

    def test_callbacks_do_not_initialize_cuda_at_initial_admission(self):
        from native_face_pipeline import NativeFrames
        restorer=NativeFrames.__new__(NativeFrames)
        restorer.torch=SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda:False))
        restorer.synchronize_devices();restorer.empty_device_caches()


if __name__=='__main__':unittest.main()
