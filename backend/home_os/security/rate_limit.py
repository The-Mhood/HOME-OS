import threading
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        super().__init__("Rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class SlidingWindowRateLimiter:
    def __init__(self):
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._buckets[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                retry_after = max(1, int(window_seconds - (now - q[0])))
                raise RateLimitExceeded(retry_after_seconds=retry_after)
            q.append(now)
