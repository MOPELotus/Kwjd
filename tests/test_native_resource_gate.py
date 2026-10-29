import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from native_resource_gate import (GPUReading, NativeResourceGate, ResourceGateCancelled,
                                  ResourceWaitGPU, physical_snapshot)
from enhance_stage import WaitGPU


def snapshot(t0=90, t1=80, f0=6000, f1=4000):
    return [GPUReading(0, 'GPU-A', 'A4000', t0, f0),
            GPUReading(1, 'GPU-B', 'Tesla T10', t1, f1)]


class FakeClock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now

    def wait(self, condition, seconds):
        if seconds is None:
            raise AssertionError('Deterministic single-thread test unexpectedly blocked')
        self.now += seconds


class NativeResourceGateTests(unittest.TestCase):
    def make(self, rows, **kwargs):
        clock = FakeClock()
        values = iter(rows)
        gate = NativeResourceGate(lambda: next(values), clock=clock, wait=clock.wait, **kwargs)
        return gate, clock

    def test_reachable_thermal_resume_retains_caller_state_and_counts_wall_once(self):
        states = []
        gate, clock = self.make([snapshot(95, 85), snapshot(94, 84), snapshot(93, 84)],
                                synchronize=lambda: states.append('sync'))
        model, current_frame, encoder = object(), object(), object()
        retained = model, current_frame, encoder
        with gate.work('face'):
            states.append('progress')
            self.assertEqual(retained, (model, current_frame, encoder))
        gate.check(stage='rife')
        metrics = gate.summary()
        self.assertEqual(states, ['sync', 'progress'])
        self.assertEqual(metrics['thermal_waits'], 1)
        self.assertEqual(metrics['thermal_resumes'], 1)
        self.assertEqual(metrics['thermal_wait_seconds'], 3.)
        self.assertEqual(metrics['telemetry_samples'], 3)
        self.assertEqual(clock.now, 3.)

    def test_unreachable_loaded_idle_floor_has_finite_release_fallback(self):
        gate, clock = self.make([snapshot(95, 85)] * 5, thermal_timeout=4)
        with self.assertRaises(ResourceWaitGPU) as raised:
            gate.check(stage='native-rife')
        failure = raised.exception
        self.assertIsInstance(failure, WaitGPU)
        self.assertEqual(failure.reason, 'thermal_timeout')
        self.assertTrue(failure.preserve_verified_segments)
        self.assertTrue(failure.release_required)
        self.assertEqual(failure.max_redo_native_frames, 250)
        self.assertEqual(failure.max_redo_source_seconds, 10)
        self.assertEqual(clock.now, 4)
        self.assertEqual(gate.summary()['thermal_wait_seconds'], 4)
        with self.assertRaises(ResourceWaitGPU):
            gate.check()
        self.assertEqual(gate.summary()['release_counts'], {'thermal_timeout': 1})

    def test_exact_memory_bounds_and_initial_cached_check(self):
        for initial, free in ((False, (3072, 1024)), (True, (5120, 3072))):
            gate, _ = self.make([snapshot(94, 84, *free)])
            gate.check(initial=initial)
            for device in (0, 1):
                bad = list(free)
                bad[device] -= 1
                gate, _ = self.make([snapshot(94, 84, *bad)])
                with self.assertRaises(ResourceWaitGPU) as raised:
                    gate.check(initial=initial)
                self.assertEqual(raised.exception.reason, 'shared_memory')
        gate, _ = self.make([snapshot(f0=4000)])
        gate.check()
        with self.assertRaises(ResourceWaitGPU) as raised:
            gate.check(initial=True)
        self.assertEqual(raised.exception.reason, 'shared_memory')
        self.assertEqual(gate.summary()['telemetry_samples'], 1)

    def test_shared_memory_during_heat_wait_requires_release(self):
        gate, _ = self.make([snapshot(95, 85), snapshot(93, 84, f1=1023)])
        with self.assertRaises(ResourceWaitGPU) as raised:
            gate.check()
        self.assertEqual(raised.exception.reason, 'shared_memory')
        self.assertEqual(gate.summary()['thermal_wait_seconds'], 1.5)

    def test_malformed_failed_or_changed_telemetry_releases(self):
        invalid = [[], [snapshot()[0]], [GPUReading(0, 'wrong', 'A', 90, 6000), snapshot()[1]],
                   [snapshot()[0], snapshot()[0]], snapshot(t0=-1), snapshot(f0=-1),
                   [GPUReading(0, 'GPU-A', '', 90, 6000), snapshot()[1]]]
        for rows in invalid:
            with self.subTest(rows=rows):
                gate, _ = self.make([rows])
                with self.assertRaises(ResourceWaitGPU) as raised:
                    gate.check()
                self.assertEqual(raised.exception.reason, 'telemetry')
        gate, clock = self.make([snapshot(), [snapshot()[0], GPUReading(1, 'GPU-C', 'Tesla T10', 80, 4000)]])
        gate.check()
        clock.now += 1.5
        with self.assertRaises(ResourceWaitGPU) as raised:
            gate.check()
        self.assertEqual(raised.exception.reason, 'telemetry')
        gate = NativeResourceGate(lambda: (_ for _ in ()).throw(TimeoutError('query timeout')))
        with self.assertRaises(ResourceWaitGPU):
            gate.check()

    def test_expected_device_uuid_pin_and_sampling_rate(self):
        gate, _ = self.make([snapshot()], expected_devices={0: 'GPU-A', 1: 'GPU-C'})
        with self.assertRaises(ResourceWaitGPU):
            gate.check()
        gate, clock = self.make([snapshot(), snapshot()])
        for _ in range(100):
            gate.check()
        self.assertEqual(gate.summary()['telemetry_samples'], 1)
        clock.now += 1.5
        gate.check()
        self.assertEqual(gate.summary()['telemetry_samples'], 2)
        for value in (.9, 2.1):
            with self.assertRaises(ValueError):
                NativeResourceGate(poll_seconds=value)
        for value in (0, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                NativeResourceGate(thermal_timeout=value)

    def test_oom_translation_and_non_oom_programming_errors(self):
        callbacks = []
        gate, _ = self.make([snapshot()], synchronize=lambda: callbacks.append('sync'),
                            empty_cache=lambda: callbacks.append('empty'))
        with gate.work():
            pass
        with self.assertRaises(ResourceWaitGPU) as raised:
            gate.oom(RuntimeError('CUDA out of memory. allocation failed'), 'native-face')
        self.assertEqual(raised.exception.reason, 'oom')
        self.assertEqual(callbacks, ['sync', 'empty'])
        self.assertEqual(gate.summary()['release_counts'], {'oom': 1})
        bug = RuntimeError('tensor shape mismatch')
        with self.assertRaises(RuntimeError) as raised:
            gate.oom(bug)
        self.assertIs(raised.exception, bug)
        with self.assertRaises(RuntimeError):
            gate.reset_cancel()

    def test_default_query_is_single_bounded_physical_snapshot(self):
        with patch('native_resource_gate.subprocess.check_output', return_value=(
                '0, GPU-A, NVIDIA RTX A4000, 93, 6000\n1, GPU-B, Tesla T10, 84, 4000\n')) as query:
            rows = physical_snapshot()
        self.assertEqual(len(rows), 2)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args.kwargs['timeout'], 5)
        self.assertIn('index,uuid,name,temperature.gpu,memory.free', query.call_args.args[0][1])

    def test_two_threads_pause_at_boundaries_share_sampler_and_wait_metric(self):
        # A virtual clock makes the next sample due while producer owns a lease.
        clock = FakeClock()
        cold, hot, cool = snapshot(), snapshot(95, 85), snapshot(93, 84)
        values = iter([cold, hot, cool])
        hot_seen, release, synced = threading.Event(), threading.Event(), threading.Event()
        def sample():
            value = next(values)
            if value is hot:
                hot_seen.set()
            return value
        gate = NativeResourceGate(sample, clock=clock, wait=None, synchronize=synced.set)
        errors = []
        def producer():
            try:
                with gate.work('producer'):
                    release.wait(2)
                gate.check(stage='producer')
            except BaseException as error:
                errors.append(error)
        producer_thread = threading.Thread(target=producer)
        producer_thread.start()
        deadline = time.monotonic() + 2
        while gate.summary()['active_gpu_sections'] != 1 and time.monotonic() < deadline:
            time.sleep(.001)
        clock.now = 1.5
        consumer = threading.Thread(target=lambda: self.capture(errors, lambda: gate.check(stage='consumer')))
        consumer.start()
        self.assertTrue(hot_seen.wait(2))
        self.assertFalse(synced.is_set())
        clock.now = 3
        release.set()
        producer_thread.join(2)
        consumer.join(2)
        self.assertFalse(producer_thread.is_alive())
        self.assertFalse(consumer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(synced.is_set())
        self.assertEqual(gate.summary()['telemetry_samples'], 3)
        self.assertEqual(gate.summary()['thermal_waits'], 1)
        self.assertEqual(gate.summary()['thermal_wait_seconds'], 1.5)

    @staticmethod
    def capture(errors, action):
        try:
            action()
        except BaseException as error:
            errors.append(error)

    def test_cancel_wakes_leader_and_follower_and_can_reset(self):
        entered = threading.Event()
        def sample():
            entered.set()
            return snapshot(95, 85)
        gate = NativeResourceGate(sample)
        errors = []
        threads = [threading.Thread(target=lambda: self.capture(errors, gate.check)) for _ in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(2))
        gate.cancel()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(error, ResourceGateCancelled) for error in errors))
        self.assertEqual(gate.summary()['cancellations'], 1)
        # A cancellation alone should be resettable; no release-required failure.
        gate.reset_cancel()
        self.assertFalse(gate.summary()['cancelled'])
        fresh, _ = self.make([snapshot()])
        fresh.cancel()
        fresh.reset_cancel()
        fresh.check()

    def test_per_run_event_wakes_both_threads_without_poisoning_next_segment(self):
        sampled = threading.Event()
        def hot():
            sampled.set()
            return snapshot(95, 85)
        gate = NativeResourceGate(hot)
        stop = threading.Event()
        errors = []
        threads = [threading.Thread(target=lambda: self.capture(
            errors, lambda: gate.check(cancel=stop))) for _ in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(sampled.wait(2))
        stop.set()
        gate.wake()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(error, ResourceGateCancelled) for error in errors))
        self.assertFalse(gate.summary()['cancelled'])
        self.assertEqual(gate.summary()['release_counts'], {})
        # The next run cannot enter with the previous leader's cached hot reading.
        # Inject a monotonic wait only after all old threads have joined.
        clock = FakeClock()
        gate._clock = clock
        gate._sampled_at = 0.
        gate._wait_fn = clock.wait
        gate._telemetry = lambda: snapshot(93, 84)
        with gate.work('next-segment', cancel=threading.Event()):
            self.assertEqual(gate.summary()['active_gpu_sections'], 1)
        self.assertEqual(gate.summary()['thermal_resumes'], 1)
        self.assertEqual(clock.now, 1.5)

    def test_late_cool_sample_cannot_resume_after_retention_deadline(self):
        clock = FakeClock()
        calls = []
        def query():
            calls.append(1)
            if len(calls) == 1:
                return snapshot(95, 85)
            clock.now += 3
            return snapshot(93, 84)
        gate = NativeResourceGate(query, clock=clock, wait=clock.wait, thermal_timeout=2)
        with self.assertRaises(ResourceWaitGPU) as raised:
            gate.check()
        self.assertEqual(raised.exception.reason, 'thermal_timeout')
        self.assertEqual(gate.summary()['thermal_resumes'], 0)
        self.assertEqual(gate.summary()['thermal_wait_seconds'], 4.5)
        self.assertEqual(gate.summary()['telemetry_seconds'], 3.)

    def test_telemetry_query_duration_is_reported_including_failure(self):
        clock = FakeClock()
        def query():
            clock.now += 1.7
            return snapshot()
        gate = NativeResourceGate(query, clock=clock, wait=clock.wait)
        gate.check()
        self.assertAlmostEqual(gate.summary()['telemetry_seconds'], 1.7)
        clock.now += 1.5
        def broken():
            clock.now += 2.1
            raise TimeoutError('bounded query failed')
        gate._telemetry = broken
        with self.assertRaises(ResourceWaitGPU):
            gate.check()
        self.assertEqual(gate.summary()['telemetry_samples'], 2)
        self.assertEqual(gate.summary()['telemetry_failures'], 1)
        self.assertAlmostEqual(gate.summary()['telemetry_seconds'], 3.8)

    def test_oom_release_callbacks_wait_for_other_active_stage(self):
        gate = NativeResourceGate(lambda: snapshot(), thermal_timeout=2)
        active, release, cleaned = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def other_stage():
            with gate.work('other-stage'):
                active.set()
                release.wait(2)
        gate._empty = cleaned.set
        worker = threading.Thread(target=other_stage)
        worker.start()
        self.assertTrue(active.wait(2))
        reporter = threading.Thread(target=lambda: self.capture(
            errors, lambda: gate.oom(RuntimeError('CUDA out of memory'), 'face')))
        reporter.start()
        deadline = time.monotonic() + 2
        while not gate.summary()['release_counts'] and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertFalse(cleaned.is_set())
        with self.assertRaises(ResourceWaitGPU):
            gate.check()
        release.set()
        reporter.join(2)
        worker.join(2)
        self.assertFalse(reporter.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertTrue(cleaned.is_set())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].reason, 'oom')

    def test_callback_can_read_summary_outside_lock_and_bug_propagates(self):
        def sync():
            observed = []
            helper = threading.Thread(target=lambda: observed.append(gate.summary()))
            helper.start()
            helper.join(1)
            self.assertFalse(helper.is_alive())
            raise ValueError('synchronizer bug')
        gate, _ = self.make([snapshot(95, 85)], synchronize=sync)
        with self.assertRaisesRegex(ValueError, 'synchronizer bug'):
            gate.check()


if __name__ == '__main__':
    unittest.main()
