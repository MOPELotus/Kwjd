"""Bounded ordered CPU-frame handoff; no CUDA dependencies or implicit buffering."""
from dataclasses import dataclass
import queue
import threading
import time


class FrameQueueCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeFrame:
    index: int
    lookahead: bool
    value: object


class _FrameBuffer(queue.Queue):
    def __init__(self,capacity,stats):
        self.stats=stats
        super().__init__(maxsize=capacity)

    def _put(self,item):
        super()._put(item)
        # Queue mutex is held: a consumer cannot hide a just-reached peak.
        self.stats['high_water_frames']=max(self.stats['high_water_frames'],len(self.queue))


class NativeFrameQueue:
    """One producer owns reader/processor state until close() has joined it.

    Capacity bounds queued frames; at most one extra frame is being processed.
    cancel_reader must unblock a blocking reader, e.g. terminate its decoder.
    Processor/context code must finish or honor the supplied stop event.
    """
    def __init__(self, reader, processor, *, primary_count, available, capacity=1,
                 context=None, cancel_reader=None, stop=None):
        if type(capacity) is not int or capacity not in (1, 2):
            raise ValueError('Native prefetch capacity must be 1 or 2')
        if not 0 < primary_count <= available <= primary_count+1:
            raise ValueError('Invalid primary/lookahead counts')
        self.reader, self.processor = reader, processor
        self.primary_count, self.available = primary_count, available
        self.context, self.cancel_reader = context, cancel_reader
        self.stop = stop if stop is not None else threading.Event()
        self.done = threading.Event()
        self.error = None
        self.next_index = 0
        self.stats = dict(capacity=capacity, high_water_frames=0, produced_primary=0,
                          produced_lookahead=0, producer_queue_wait=0., consumer_queue_wait=0.)
        self.items = _FrameBuffer(capacity,self.stats)
        self.thread = threading.Thread(target=self._produce, name='native-face-producer', daemon=False)
        self.thread.start()

    def _produce_frames(self):
        for index in range(self.available):
            if self.stop.is_set():
                return
            raw = self.reader(index)
            if self.stop.is_set():
                return
            frame = NativeFrame(index, index >= self.primary_count, self.processor(raw, index))
            start = time.monotonic()
            while not self.stop.is_set():
                try:
                    self.items.put(frame, timeout=.05)
                    self.stats['produced_lookahead' if frame.lookahead else 'produced_primary'] += 1
                    break
                except queue.Full:
                    pass
            self.stats['producer_queue_wait'] += time.monotonic()-start

    def _produce(self):
        try:
            if self.context is None:
                self._produce_frames()
            else:
                with self.context():
                    self._produce_frames()
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def get(self):
        if self.next_index == self.available:
            if self.error is not None:
                raise self.error
            raise StopIteration
        start = time.monotonic()
        try:
            while True:
                if self.stop.is_set():
                    raise FrameQueueCancelled('Native frame handoff cancelled')
                try:
                    frame = self.items.get(timeout=.05)
                except queue.Empty:
                    if self.stop.is_set():
                        raise FrameQueueCancelled('Native frame handoff cancelled')
                    if self.done.is_set():
                        if self.error is not None:
                            raise self.error
                        raise EOFError('Native producer ended before all frames')
                    continue
                if frame.index != self.next_index:
                    raise RuntimeError('Native frame handoff order differs')
                self.next_index += 1
                return frame
        finally:
            self.stats['consumer_queue_wait'] += time.monotonic()-start

    def finish(self):
        """Join after consuming the declared frames and surface context-exit errors."""
        if self.next_index != self.available:
            raise RuntimeError('Consume all declared native frames before finish')
        self.thread.join()
        if self.error is not None:
            raise self.error

    def close(self):
        self.stop.set()
        if self.thread.is_alive() and self.cancel_reader is not None:
            self.cancel_reader()
        # Never allow model/helper reuse while the old producer still owns it.
        self.thread.join()
        while True:
            try:
                self.items.get_nowait()
            except queue.Empty:
                break
