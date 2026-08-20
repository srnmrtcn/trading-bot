import pytest

from src.rate_limit import RateLimitBackoff


class _RateLimitError(Exception):
    def __init__(self, status_code, response=None):
        super().__init__("rate limited")
        self.status_code = status_code
        self.response = response


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def test_backoff_retries_then_succeeds_with_exponential_delay():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _RateLimitError(status_code=429)
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(max_retries=3, base_delay=1.0, sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert calls["count"] == 2
    assert sleeps == [1.0]


def test_backoff_honors_retry_after_header():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _RateLimitError(status_code=429, response=_FakeResponse({"Retry-After": "2"}))
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert sleeps == [2.0]


def test_backoff_raises_immediately_for_non_rate_limit_errors():
    def always_fails():
        raise _RateLimitError(status_code=500)

    backoff = RateLimitBackoff(sleep_fn=lambda seconds: None)
    with pytest.raises(_RateLimitError):
        backoff.call(always_fails)


def test_backoff_gives_up_after_max_retries():
    def always_rate_limited():
        raise _RateLimitError(status_code=429)

    backoff = RateLimitBackoff(max_retries=2, sleep_fn=lambda seconds: None)
    with pytest.raises(_RateLimitError):
        backoff.call(always_rate_limited)
