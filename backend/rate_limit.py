"""Sliding-window rate limiting, in process memory.

Purpose: stop a single client (or a runaway script) from saturating the
generation queue of a CPU-only backend and making the service unusable for the
whole hospital. This limiter is deliberately process-local — see the README for
the multi-replica variant (reverse proxy or Redis).
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("max_requests and window_seconds must be strictly positive.")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

    def _drop_expired(self, hits: deque[float], cutoff: float) -> None:
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def _cleanup(self, now: float) -> None:
        """Evict idle identities so the dictionary does not leak."""
        if now - self._last_cleanup < self.window_seconds:
            return
        cutoff = now - self.window_seconds
        for key in [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
        self._last_cleanup = now

    def check(self, identity: str) -> tuple[bool, int, int]:
        """Record a call. Returns (allowed, remaining requests, retry_after seconds)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            self._cleanup(now)
            hits = self._hits.setdefault(identity, deque())
            self._drop_expired(hits, cutoff)

            if len(hits) >= self.max_requests:
                retry_after = max(1, int(hits[0] + self.window_seconds - now) + 1)
                return False, 0, retry_after

            hits.append(now)
            return True, self.max_requests - len(hits), 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._last_cleanup = 0.0
