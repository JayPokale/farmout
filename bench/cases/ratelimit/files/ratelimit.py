"""Per-key token-bucket rate limiting, plus a retry helper for flaky calls."""
import random
import threading
import time


class TokenBucket:
    """Allows `rate` calls per second on average, with bursts of up to `capacity`."""

    def __init__(self, rate, capacity):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.time()
        self.lock = threading.Lock()

    def take(self, cost=1):
        with self.lock:
            now = time.time()
            elapsed = now - self.updated
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.updated = now
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


class Limiter:
    """One bucket per key (user id, API token, ...). Safe to share between threads."""

    def __init__(self, rate, capacity):
        self.rate = rate
        self.capacity = capacity
        self.buckets = {}

    def allow(self, key, cost=1):
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self.rate, self.capacity)
            self.buckets[key] = bucket
        return bucket.take(cost)


def retry(fn, retries=3, base_delay=0.5, max_delay=30, log=None):
    """Call fn(); if it raises, retry up to `retries` more times with jittered
    exponential backoff, then re-raise the last exception.
    """
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            delay = min(max_delay, base_delay * 2 ** attempt)
            delay *= random.uniform(0.5, 1.5)
            if log:
                log.warning("attempt %d failed (%s); retrying in %.1fs", attempt + 1, exc, delay)
            time.sleep(delay)
    if log:
        log.error("giving up after %d attempts", retries)
    return None
