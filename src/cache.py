"""Cache TTL thread-safe — classe TTLCache (extraída de gitlab_api.py)."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque


class _Flight:
    """Uma busca em curso — quem pede a mesma chave espera por ela."""
    __slots__ = ("event", "value", "error")

    def __init__(self):
        self.event = threading.Event()
        self.value = None
        self.error = None


class TTLCache:
    """Tiny thread-safe TTL cache.

    - Producers run *outside* the lock, so callers never serialise on a slow
      GitLab fetch; concurrent misses on the SAME key share one fetch
      (single-flight) instead of all hitting GitLab at once.
    - invalidate(prefix) only affects keys with that prefix: a fetch that was
      already running when a matching invalidation happened is returned to its
      callers but NOT cached (it may predate a write). Unrelated invalidations
      never block caching.
    - Bounded: expired entries are purged regularly and, above `max_entries`
      or `max_bytes`, the least recently used ones are evicted. A value's size is
      what `weigh(value)` says (get_or_set argument), else its `nbytes` attribute
      when it has one; a single value bigger than a quarter of the budget is
      returned but not cached."""

    _MAX_INVALIDATIONS = 512   # history kept to decide whether a fetch went stale
    _WAIT_TIMEOUT = 90         # s a caller waits for another caller's fetch

    def __init__(self, ttl: int, max_entries: int = 256,
                 max_bytes: int = 256 * 1024 * 1024):
        self.ttl = ttl
        self.max_entries = max(1, max_entries)
        self.max_bytes = max_bytes
        self._bytes = 0
        self._data: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._weights: dict[str, int] = {}
        self._inflight: dict[str, _Flight] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._invalidations: deque = deque(maxlen=self._MAX_INVALIDATIONS)
        self._last_purge = time.monotonic()

    def get_or_set(self, key, producer, weigh=None, wait=None):
        with self._lock:
            entry = self._data.get(key)
            if entry and time.monotonic() - entry[0] < self.ttl:
                self._data.move_to_end(key)
                return entry[1]
            flight = self._inflight.get(key)
            owner = flight is None
            if owner:
                flight = self._inflight[key] = _Flight()
                start_seq = self._seq

        if not owner:
            limit = self._WAIT_TIMEOUT if wait is None else max(0.0, min(self._WAIT_TIMEOUT, wait))
            if flight.event.wait(limit):
                if flight.error is not None:
                    raise flight.error
                return flight.value
            return producer()   # the other fetch is stuck (or our time is up)

        try:
            value = producer()   # runs outside the lock (slow GitLab call)
        except BaseException as e:
            flight.error = e
            with self._lock:
                if self._inflight.get(key) is flight:
                    del self._inflight[key]
            flight.event.set()
            raise

        with self._lock:
            if self._inflight.get(key) is flight:
                del self._inflight[key]
            weight = self._weight(value, weigh)
            if (not self._invalidated_since(key, start_seq)
                    and weight <= self.max_bytes // 4):
                self._remove(key)
                self._data[key] = (time.monotonic(), value)
                self._weights[key] = weight
                self._bytes += weight
                self._data.move_to_end(key)
                self._evict()
        flight.value = value
        flight.event.set()
        return value

    def invalidate(self, prefix: str = ""):
        with self._lock:
            self._invalidations.append((self._seq, prefix))
            self._seq += 1
            for k in [k for k in self._data if k.startswith(prefix)]:
                self._remove(k)
            # New callers must not join a fetch that started before this point.
            for k in [k for k in self._inflight if k.startswith(prefix)]:
                del self._inflight[k]

    def __len__(self):
        with self._lock:
            return len(self._data)

    # ---- internals (called with the lock held) ----------------------------------

    def _invalidated_since(self, key, start_seq):
        if self._seq == start_seq:
            return False
        if not self._invalidations or self._invalidations[0][0] > start_seq:
            return True   # history rotated past the fetch start — assume stale
        return any(seq >= start_seq and key.startswith(prefix)
                   for seq, prefix in self._invalidations)

    @staticmethod
    def _weight(value, weigh=None):
        try:
            w = weigh(value) if weigh else getattr(value, "nbytes", 0)
            return max(0, int(w or 0))
        except (TypeError, ValueError):
            return 0

    def _remove(self, key):
        if self._data.pop(key, None) is not None:
            self._bytes -= self._weights.pop(key, 0)

    def _evict(self):
        now = time.monotonic()
        if (len(self._data) > self.max_entries or self._bytes > self.max_bytes
                or now - self._last_purge >= self.ttl):
            self._last_purge = now
            for k in [k for k, (ts, _) in self._data.items() if now - ts >= self.ttl]:
                self._remove(k)
        while self._data and (len(self._data) > self.max_entries
                              or self._bytes > self.max_bytes):
            self._remove(next(iter(self._data)))
