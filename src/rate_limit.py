from __future__ import annotations

import time


class RateLimitBackoff:
    def __init__(self, max_retries: int = 5, base_delay: float = 1.0, sleep_fn=time.sleep):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleep_fn = sleep_fn

    def call(self, func, *args, **kwargs):
        attempt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code not in (429, 418) or attempt >= self.max_retries:
                    raise
                delay = self._retry_after_seconds(exc)
                if delay is None:
                    delay = self.base_delay * (2 ** attempt)
                self.sleep_fn(delay)
                attempt += 1

    @staticmethod
    def _retry_after_seconds(exc):
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
        if not headers:
            return None
        value = headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None
