import sys
from pathlib import Path
import types
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from build_rife_sm86 import make_allocator


class Memory:
    def __init__(self):self.free=10000;self.live={};self.counter=256;self.copies=[];self.external_drop=0
    def free_bytes(self):return self.free
    def malloc(self,size):
        pointer=self.counter;self.counter+=2**20;self.live[pointer]=size
        self.free-=size+self.external_drop;return pointer
    def release(self,pointer):self.free+=self.live.pop(pointer)
    def copy(self,dst,src,size):self.copies.append((dst,src,size))


class BudgetTests(unittest.TestCase):
    def allocator(self,memory):return make_allocator(types.SimpleNamespace(IGpuAllocator=object),memory,4000)

    def test_reserve_denial_does_not_allocate_or_release_other_memory(self):
        m=Memory();a=self.allocator(m)
        self.assertIsNone(a.allocate(6001,256,0));self.assertEqual(m.live,{})
        self.assertFalse(a.deallocate(987654));self.assertEqual(m.free,10000)

    def test_post_allocation_external_drop_releases_only_owned_block(self):
        m=Memory();m.external_drop=2000;a=self.allocator(m)
        self.assertIsNone(a.allocate(5000,256,0))
        self.assertEqual(m.live,{});self.assertEqual(m.free,8000)
        self.assertTrue(a.denied)

    def test_async_allocation_and_reallocation_preserve_size_and_accounting(self):
        m=Memory();a=self.allocator(m)
        first=a.allocate_async(1000,256,0,0)
        second=a.reallocate(first,256,2000)
        self.assertNotEqual(first,second);self.assertEqual(m.copies,[(second,first,1000)])
        self.assertEqual(a.live,{second:2000});self.assertEqual(a.peak_bytes,3000)
        self.assertTrue(a.deallocate_async(second,0));self.assertEqual(m.free,10000)

    def test_failed_growth_keeps_original_allocation(self):
        m=Memory();a=self.allocator(m);first=a.allocate(4000,256,0)
        self.assertIsNone(a.reallocate(first,256,3000+4000))
        self.assertEqual(a.live,{first:4000});self.assertEqual(m.live,{first:4000})

    def test_large_alignment_tracks_raw_allocation_for_safe_release(self):
        m=Memory();a=self.allocator(m);pointer=a.allocate(1024,512,0)
        self.assertEqual(pointer,512);self.assertIn(256,m.live)
        self.assertEqual(a.allocated[pointer],1535)
        self.assertTrue(a.deallocate(pointer));self.assertEqual(m.live,{})
        self.assertEqual(m.free,10000)

    def test_ambiguous_free_error_does_not_retry_release(self):
        m=Memory();m.external_drop=2000;a=self.allocator(m);calls=[];original=m.release
        def ambiguous(pointer):
            calls.append(pointer);original(pointer);raise RuntimeError('async CUDA error after free')
        m.release=ambiguous
        self.assertIsNone(a.allocate(5000,256,0));self.assertEqual(len(calls),1)
        self.assertEqual(m.live,{});self.assertTrue(a.errors)

    def test_shrinking_honors_stronger_alignment(self):
        m=Memory();a=self.allocator(m);first=a.allocate(1000,256,0)
        second=a.reallocate(first,512,500)
        self.assertEqual(second%512,0);self.assertNotEqual(second,first)
        self.assertEqual(m.copies,[(second,first,500)])
        self.assertNotIn(first,a.live)

    def test_dynamic_future_budget_is_rechecked(self):
        m=Memory();limit=[4000];a=make_allocator(types.SimpleNamespace(IGpuAllocator=object),m,lambda:limit[0])
        first=a.allocate(1000,256,0);limit[0]=9000
        self.assertIsNone(a.allocate(1,256,0));self.assertEqual(a.live,{first:1000})


if __name__=='__main__':unittest.main()
