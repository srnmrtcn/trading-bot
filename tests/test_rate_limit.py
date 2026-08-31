import logging

import pytest
import requests
from binance.exceptions import BinanceAPIException

from src.rate_limit import NETWORK_MAX_RETRIES, RETRY_AFTER_CAP_SECONDS, RateLimitBackoff


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


def _api_error(status_code, headers=None):
    # A real BinanceAPIException, not a stand-in: the backoff reads
    # `.status_code` and `.response.headers`, and a library upgrade that
    # renames either must fail here rather than in production.
    return BinanceAPIException(_FakeResponse(headers or {}), status_code, '{"code":-1003,"msg":"limited"}')


def test_retry_after_is_capped_so_a_long_header_cannot_freeze_the_hourly_job():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _api_error(429, {"Retry-After": "7200"})
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert sleeps == [float(RETRY_AFTER_CAP_SECONDS)]


def test_418_ip_ban_is_raised_immediately_without_sleeping():
    # A 418 bans the IP, not the symbol; retrying per symbol only stacks
    # sleeps while the hourly job starves. Fail fast, let the next hour try.
    calls = {"count": 0}

    def banned():
        calls["count"] += 1
        raise _api_error(418, {"Retry-After": "60"})

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    with pytest.raises(BinanceAPIException):
        backoff.call(banned)
    assert calls["count"] == 1
    assert sleeps == []


def test_network_errors_are_retried_a_bounded_number_of_times():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] <= NETWORK_MAX_RETRIES:
            raise requests.exceptions.ReadTimeout("read timed out")
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(base_delay=1.0, sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert calls["count"] == NETWORK_MAX_RETRIES + 1
    assert sleeps == [1.0, 2.0]


def test_network_errors_give_up_after_the_bound():
    def always_times_out():
        raise requests.exceptions.ConnectionError("unreachable")

    sleeps = []
    backoff = RateLimitBackoff(sleep_fn=sleeps.append)
    with pytest.raises(requests.exceptions.ConnectionError):
        backoff.call(always_times_out)
    assert len(sleeps) == NETWORK_MAX_RETRIES


def test_5xx_is_treated_like_a_network_error():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _api_error(503)
        return "ok"

    sleeps = []
    backoff = RateLimitBackoff(base_delay=1.0, sleep_fn=sleeps.append)
    assert backoff.call(flaky) == "ok"
    assert sleeps == [1.0]


def test_rate_limit_retries_are_still_bounded_by_max_retries_with_real_exception():
    def always_rate_limited():
        raise _api_error(429)

    backoff = RateLimitBackoff(max_retries=2, sleep_fn=lambda seconds: None)
    with pytest.raises(BinanceAPIException):
        backoff.call(always_rate_limited)


def test_every_retry_is_logged_so_a_slow_hour_is_explainable(caplog):
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise _api_error(429, {"Retry-After": "3"})
        return "ok"

    backoff = RateLimitBackoff(sleep_fn=lambda seconds: None)
    with caplog.at_level(logging.WARNING, logger="rate_limit"):
        backoff.call(flaky)
    assert any("429" in record.getMessage() for record in caplog.records)
