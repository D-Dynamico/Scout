"""A shared rate limiter and a retry, so running apps in parallel does not
just move the failure from slow to throttled.

Both limiters are module level on purpose. Every worker thread shares one, so
concurrency 8 still means one global request rate, not eight of them.
"""

import random
import threading
import time


class Limiter:
    """Minimum interval between calls, enforced across all threads."""

    def __init__(self, calls_per_second):
        self.min_interval = 1.0 / float(calls_per_second)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_at - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_at = now + self.min_interval


# Composio does the fetching, the model does the thinking. They throttle
# differently, so they get separate budgets.
composio_limiter = Limiter(calls_per_second=4)
llm_limiter = Limiter(calls_per_second=2)


def with_retry(fn, limiter=None, attempts=6, base_delay=2.0):
    """Run fn, retrying on anything that looks like throttling or a blip.

    A 429 is not a failure, it is a request to wait. Anything that is clearly
    our fault, like a bad key or a missing scope, is raised immediately rather
    than retried four times.
    """
    last = None
    for attempt in range(attempts):
        if limiter:
            limiter.wait()
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - we re-raise below
            last = exc
            text = str(exc).lower()
            permanent = any(s in text for s in (
                "invalid api key", "permissions required", "authentication",
                "not found for user", "unknown request url",
            ))
            if permanent or attempt == attempts - 1:
                raise
            # exponential backoff with jitter, so parallel workers that all hit
            # the wall do not all come back at the same instant
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
    raise last
