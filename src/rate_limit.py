from __future__ import annotations

import logging
import time

import requests
from binance.exceptions import BinanceAPIException

# Module level logger for rate limiting events
logger = logging.getLogger("rate_limit")

# Cap the retry-after delay to prevent long delays from freezing jobs
RETRY_AFTER_CAP_SECONDS = 120

# Maximum number of retries for network errors (5xx and requests exceptions)
NETWORK_MAX_RETRIES = 2


class RateLimitBackoff:
    def __init__(self, max_retries: int = 5, base_delay: float = 1.0, sleep_fn=time.sleep):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleep_fn = sleep_fn

    def call(self, func, *args, **kwargs):
        """Retry policy, in order of precedence:

        1. 418 (IP ban): raise at once — the ban is per IP, retrying per
           symbol only stacks sleeps.
        2. 429 (rate limit): honor Retry-After capped at
           RETRY_AFTER_CAP_SECONDS, else exponential backoff; at most
           `max_retries` retries.
        3. Network faults (requests exceptions, HTTP 5xx from Binance):
           exponential backoff, at most NETWORK_MAX_RETRIES retries.
        4. Anything else: raise at once.
        Every sleep is logged at WARNING with the reason.
        """
        # Separate counters for rate limit (429) and network errors (5xx, requests exceptions)
        rate_limit_retry_count = 0
        network_error_retry_count = 0

        while True:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)

                # Handle 418 - IP ban, fail immediately without sleeping
                if status_code == 418:
                    logger.warning("Binance returned 418 (IP ban); not retrying")
                    raise

                # Handle 429 - Rate limit exceeded
                elif status_code == 429:
                    if rate_limit_retry_count >= self.max_retries:
                        raise
                    delay = self._retry_after_seconds(exc)
                    if delay is None:
                        delay = self.base_delay * (2 ** rate_limit_retry_count)
                    # Cap the delay to prevent long freezes
                    delay = min(delay, float(RETRY_AFTER_CAP_SECONDS))
                    logger.warning("Binance returned 429; sleeping %.0fs before retry %d",
                                   delay, rate_limit_retry_count + 1)
                    self.sleep_fn(delay)
                    rate_limit_retry_count += 1
                    continue

                # Handle network errors (5xx or requests exceptions)
                elif isinstance(exc, requests.exceptions.RequestException) or (
                    isinstance(exc, BinanceAPIException) and getattr(exc, "status_code", 0) >= 500
                ):
                    if network_error_retry_count >= NETWORK_MAX_RETRIES:
                        raise
                    delay = self.base_delay * (2 ** network_error_retry_count)
                    logger.warning("Network error (%s); sleeping %.0fs before retry %d",
                                   exc, delay, network_error_retry_count + 1)
                    self.sleep_fn(delay)
                    network_error_retry_count += 1
                    continue

                # All other exceptions are raised immediately
                else:
                    raise

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
