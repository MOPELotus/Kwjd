"""Coordinated native GPU admission; no model, decoder or checkpoint ownership.

Wrap each GPU section in ``work`` (never call ``check`` inside a lease). Thermal
waits retain caller state. ResourceWaitGPU asks the owner to release models and
retry only the unfinished segment; this module never edits checkpoint files.
All telemetry is for physical NVIDIA devices and is shared by both threads.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import math
import subprocess
import threading
import time

from enhance_stage import WaitGPU


@dataclass(frozen=True)
class GPUReading:
    index: int
    uuid: str
    name: str
    temperature: int
    free_mib: int


class ResourceWaitGPU(WaitGPU):
    """Explicit release fallback, preserving every verified segment."""
    def __init__(self, reason, stage='', detail=''):
        self.reason = reason
        self.stage = stage
        self.detail = detail
        self.release_required = True
        self.preserve_verified_segments = True
        self.max_redo_native_frames = 250
        self.max_redo_source_seconds = 10
        super().__init__(f'Native resource release ({reason}, stage={stage}): {detail}')


class ResourceGateCancelled(RuntimeError):
    pass


def physical_snapshot():
    """One bounded physical telemetry query for both devices, without CUDA init."""
    rows = subprocess.check_output([
        'nvidia-smi', '--query-gpu=index,uuid,name,temperature.gpu,memory.free',
        '--format=csv,noheader,nounits'], text=True, timeout=5)
    readings = []
    for line in rows.splitlines():
        fields = [part.strip() for part in line.split(',')]
        if len(fields) != 5:
            raise ValueError('Malformed physical GPU telemetry')
        index, uuid, name, temperature, free = fields
        readings.append(GPUReading(int(index), uuid, name, int(temperature), int(free)))
    return readings


class NativeResourceGate:
    """One sampler/thermal leader and condition-based GPU admission.

    ``synchronize`` must cover both owned CUDA devices/streams. It runs after all
    work leases return and outside the condition lock. ``empty_cache`` is an
    optional release hint, also outside the lock; the owner still tears down
    models on ResourceWaitGPU. Neither callback may wait for this gate's leases.
    poll_seconds is the minimum interval after a completed query; actual
    query duration is recorded separately, so slow telemetry is never reported
    as a measured 1–2s cadence. Thermal deadlines may overrun by one bounded
    query or synchronizer call, never by an unbounded polling loop.
    Telemetry injections must return promptly (the physical default has a 5s
    subprocess timeout). ``clock`` is monotonic; ``wait`` is an optional injected
    Condition.wait-compatible function for deterministic CPU tests.
    """
    def __init__(self, telemetry=None, *, expected_devices=None, poll_seconds=1.5,
                 thermal_timeout=120., synchronize=None, empty_cache=None,
                 clock=time.monotonic, wait=None):
        if not 1 <= poll_seconds <= 2:
            raise ValueError('Physical telemetry sampling must be 1–2 seconds')
        if not math.isfinite(thermal_timeout) or thermal_timeout <= 0:
            raise ValueError('Thermal timeout must be finite and positive')
        if expected_devices is not None and set(expected_devices) != {0, 1}:
            raise ValueError('Expected physical device UUIDs must cover GPU0 and GPU1')
        self._telemetry = telemetry or physical_snapshot
        self._expected = dict(expected_devices or {})
        self._identity = None
        self._poll = float(poll_seconds)
        self._timeout = float(thermal_timeout)
        self._sync = synchronize
        self._empty = empty_cache
        self._clock = clock
        self._wait_fn = wait
        self._cv = threading.Condition()
        self._busy = False
        self._active = 0
        self._cancelled = False
        self._failure = None
        self._snapshot = None
        self._sampled_at = -math.inf
        self._metrics = dict(telemetry_samples=0, telemetry_failures=0, telemetry_seconds=0., thermal_waits=0,
                             thermal_wait_seconds=0., thermal_resumes=0,
                             release_counts={}, cancellations=0)
        self._thermal_since = None

    def _wait(self, seconds=None):
        if self._wait_fn:
            self._wait_fn(self._cv, seconds)
        else:
            self._cv.wait(seconds)

    def _raise_state(self, cancel=None):
        if self._failure is not None:
            raise self._failure
        if self._cancelled or (cancel is not None and cancel.is_set()):
            raise ResourceGateCancelled('Native resource gate cancelled')

    def _validate(self, values):
        rows = list(values)
        found = {}
        for row in rows:
            if (not isinstance(row, GPUReading) or type(row.index) is not int
                    or row.index in found or not row.uuid.startswith('GPU-')
                    or not row.name or type(row.temperature) is not int
                    or not 0 <= row.temperature <= 125 or type(row.free_mib) is not int
                    or row.free_mib < 0):
                raise ValueError('Malformed physical GPU telemetry reading')
            found[row.index] = row
        if not {0, 1}.issubset(found):
            raise ValueError('Physical GPU0/GPU1 telemetry missing')
        identity = {i: (found[i].uuid, found[i].name) for i in (0, 1)}
        if identity[0][0] == identity[1][0]:
            raise ValueError('Duplicate physical GPU UUID')
        if any(found[i].uuid != uuid for i, uuid in self._expected.items()):
            raise ValueError('Physical GPU UUID differs from pinned device')
        if self._identity is not None and identity != self._identity:
            raise ValueError('Physical GPU identity changed')
        self._identity = identity
        return found

    def _sample(self, stage):
        began = self._clock()
        with self._cv:
            self._metrics['telemetry_samples'] += 1
        try:
            values = self._validate(self._telemetry())
        except Exception as error:
            with self._cv:
                self._metrics['telemetry_failures'] += 1
            raise ResourceWaitGPU('telemetry', stage, str(error)) from error
        finally:
            with self._cv:
                self._metrics['telemetry_seconds'] += self._clock() - began
        with self._cv:
            self._snapshot = values
            self._sampled_at = self._clock()
        return values

    @staticmethod
    def _pressure(rows, initial):
        limits = (5120, 3072) if initial else (3072, 1024)
        return any(rows[i].free_mib < limits[i] for i in (0, 1))

    @staticmethod
    def _hot(rows):
        return rows[0].temperature >= 95 or rows[1].temperature >= 85

    @staticmethod
    def _cool(rows):
        return rows[0].temperature <= 93 and rows[1].temperature <= 84

    def _finish_thermal(self):
        with self._cv:
            if self._thermal_since is not None:
                self._metrics['thermal_wait_seconds'] += self._clock() - self._thermal_since
                self._thermal_since = None

    def _quiesce(self, deadline=None, cancel=None):
        with self._cv:
            while self._active:
                self._raise_state(cancel)
                remaining = None if deadline is None else deadline - self._clock()
                if remaining is not None and remaining <= 0:
                    return False
                self._wait(min(remaining, .05) if cancel is not None and remaining is not None else
                           (.05 if cancel is not None else remaining))
            self._raise_state(cancel)
        return True

    def _lead(self, initial, stage, cancel=None):
        # A cancelled thermal leader may leave a fresh hot snapshot. Reuse it
        # for another leader without admitting hot work or oversampling.
        rows = self._snapshot if self._clock() - self._sampled_at < self._poll else self._sample(stage)
        if self._pressure(rows, initial):
            raise ResourceWaitGPU('shared_memory', stage, 'Physical GPU free VRAM below unchanged admission bound')
        if not self._hot(rows):
            return
        with self._cv:
            self._thermal_since = self._clock()
            self._metrics['thermal_waits'] += 1
            deadline = self._thermal_since + self._timeout
        try:
            if not self._quiesce(deadline, cancel):
                raise ResourceWaitGPU('thermal_timeout', stage, 'Active GPU work did not reach a safe boundary')
            if self._sync:
                self._sync()
            while True:
                with self._cv:
                    self._raise_state(cancel)
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise ResourceWaitGPU('thermal_timeout', stage, 'Loaded devices did not reach 93C/84C resume bounds')
                    delay = min(max(0., self._sampled_at + self._poll - self._clock()), remaining)
                    if delay:
                        self._wait(min(delay, .05) if cancel is not None else delay)
                        continue
                rows = self._sample(stage)
                with self._cv:
                    self._raise_state(cancel)
                if self._clock() >= deadline:
                    raise ResourceWaitGPU('thermal_timeout', stage, 'Cooling telemetry exceeded the retention deadline')
                if self._pressure(rows, initial):
                    raise ResourceWaitGPU('shared_memory', stage, 'Shared VRAM changed during thermal retention')
                if self._cool(rows):
                    with self._cv:
                        self._metrics['thermal_resumes'] += 1
                    return
        finally:
            self._finish_thermal()

    def _publish_failure(self, error):
        with self._cv:
            if isinstance(error, ResourceGateCancelled):
                self._cv.notify_all()
                return
            if self._failure is None:
                self._failure = error
                if isinstance(error, ResourceWaitGPU):
                    counts = self._metrics['release_counts']
                    counts[error.reason] = counts.get(error.reason, 0) + 1
            self._cv.notify_all()

    def _admit(self, initial, stage, lease, cancel=None):
        while True:
            with self._cv:
                self._raise_state(cancel)
                if self._busy:
                    self._wait(.05 if cancel is not None else None)
                    continue
                if self._snapshot is not None and self._clock() - self._sampled_at < self._poll:
                    if self._pressure(self._snapshot, initial):
                        # Re-evaluate stronger initial admission against the same snapshot.
                        error = ResourceWaitGPU('shared_memory', stage, 'Initial free VRAM below unchanged admission bound')
                    elif not self._hot(self._snapshot):
                        if lease:
                            self._active += 1
                        return
                    else:
                        error = None
                else:
                    error = None
                self._busy = True
            try:
                if error is not None:
                    raise error
                self._lead(initial, stage, cancel)
            except BaseException as failure:
                self._publish_failure(failure)
                raise
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def check(self, initial=False, stage='', cancel=None):
        self._admit(initial, stage, False, cancel)

    @contextmanager
    def work(self, stage='', initial=False, cancel=None):
        self._admit(initial, stage, True, cancel)
        try:
            yield
        finally:
            with self._cv:
                self._active -= 1
                self._cv.notify_all()

    def oom(self, error, stage=''):
        """Translate only explicit CUDA OOM; programming failures propagate."""
        message = str(error).lower()
        is_oom = (type(error).__name__ == 'OutOfMemoryError'
                  or (isinstance(error, RuntimeError) and 'cuda out of memory' in message))
        if not is_oom:
            raise error
        failure = ResourceWaitGPU('oom', stage, str(error))
        self._publish_failure(failure)
        # Caller must leave work() before invoking oom; release callbacks must
        # never race another thread's GPU operation.
        quiescent = self._quiesce_for_release()
        if quiescent:
            if self._sync:
                self._sync()
            if self._empty:
                self._empty()
        raise failure from error

    def _quiesce_for_release(self):
        deadline = self._clock() + self._timeout
        with self._cv:
            while self._active and not self._cancelled:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._wait(remaining)
            return not self._active

    def wake(self):
        """Wake gate waiters after a caller-owned per-run Event is set."""
        with self._cv:
            self._cv.notify_all()

    def cancel(self):
        with self._cv:
            if not self._cancelled:
                self._metrics['cancellations'] += 1
            self._cancelled = True
            self._cv.notify_all()

    def reset_cancel(self):
        with self._cv:
            if self._busy or self._active:
                raise RuntimeError('Cannot reset an active resource gate')
            if self._failure is not None:
                raise RuntimeError('Release-required or failed resource gate cannot be reused')
            self._cancelled = False

    def summary(self):
        with self._cv:
            result = dict(self._metrics)
            result['release_counts'] = dict(result['release_counts'])
            if self._thermal_since is not None:
                result['thermal_wait_seconds'] += self._clock() - self._thermal_since
            result.update(last_snapshot={i: dict(temperature_celsius=row.temperature, free_mib=row.free_mib)
                                              for i, row in (self._snapshot or {}).items()},
                          active_gpu_sections=self._active, cancelled=self._cancelled,
                          device_identity=dict(self._identity or {}),
                          sample_interval_seconds=self._poll,
                          thermal_timeout_seconds=self._timeout,
                          heat_abort_celsius={0: 95, 1: 85},
                          heat_resume_celsius={0: 93, 1: 84},
                          loaded_free_mib={0: 3072, 1: 1024},
                          initial_free_mib={0: 5120, 1: 3072})
            return result
