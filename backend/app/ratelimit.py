"""In-process sliding-window limiter (single-instance demo protection; no external store)."""

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 3600.0, clock=time.monotonic):
        self.limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Record an event and return True, or return False if the window is full."""
        now = self._clock()
        with self._lock:
            while self._events and now - self._events[0] > self._window:
                self._events.popleft()
            if len(self._events) >= self.limit:
                return False
            self._events.append(now)
            return True


class KeyedLimiter:
    """Sliding windows per key (IP, email, user). Bounded memory: oldest keys are evicted."""

    def __init__(self, limit: int, window_seconds: float, max_keys: int = 10_000, clock=time.monotonic):
        self.limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _window_for(self, key: str, now: float) -> deque[float]:
        events = self._events.pop(key, None) or deque()
        while events and now - events[0] > self._window:
            events.popleft()
        self._events[key] = events  # re-insert: most recently used last
        while len(self._events) > self._max_keys:
            self._events.pop(next(iter(self._events)))
        return events

    def allow(self, key: str) -> bool:
        """Record an event for `key` and return True, or return False if its window is full."""
        now = self._clock()
        with self._lock:
            events = self._window_for(key, now)
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True

    def blocked(self, key: str) -> bool:
        """True if `key` has reached the limit (does not record an event)."""
        now = self._clock()
        with self._lock:
            return len(self._window_for(key, now)) >= self.limit

    def record(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._window_for(key, now).append(now)
