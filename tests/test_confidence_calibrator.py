from decimal import Decimal

from src.confidence_calibrator import MIN_SAMPLES, compute_success_rates, confidence_bucket


def test_confidence_bucket_lower_bound_values():
    assert confidence_bucket(Decimal("0.0")) == Decimal("0.0")
    assert confidence_bucket(Decimal("0.05")) == Decimal("0.0")
    assert confidence_bucket(Decimal("0.15")) == Decimal("0.1")
    assert confidence_bucket(Decimal("0.65")) == Decimal("0.6")
    assert confidence_bucket(Decimal("0.99")) == Decimal("0.9")


def test_confidence_bucket_exactly_one_joins_last_bucket():
    assert confidence_bucket(Decimal("1.0")) == Decimal("0.9")


def test_compute_success_rates_below_min_samples_returns_none():
    records = [("long", Decimal("0.65"), "hit_target")] * (MIN_SAMPLES - 1)
    rates = compute_success_rates(records)
    rate, count = rates[("long", Decimal("0.6"))]
    assert rate is None
    assert count == MIN_SAMPLES - 1


def test_compute_success_rates_at_min_samples_computes_real_rate():
    hits = [("long", Decimal("0.65"), "hit_target")] * 12
    stops = [("long", Decimal("0.65"), "hit_stop")] * 6
    expired = [("long", Decimal("0.65"), "expired")] * 2
    records = hits + stops + expired  # 20 total, 12 hit_target
    rates = compute_success_rates(records)
    rate, count = rates[("long", Decimal("0.6"))]
    assert count == 20
    assert rate == Decimal("12") / Decimal("20")


def test_compute_success_rates_expired_counts_as_failure():
    records = [("long", Decimal("0.65"), "hit_target")] * 10 + [("long", Decimal("0.65"), "expired")] * 10
    rates = compute_success_rates(records)
    rate, count = rates[("long", Decimal("0.6"))]
    assert count == 20
    assert rate == Decimal("10") / Decimal("20")


def test_compute_success_rates_keeps_direction_and_bucket_separate():
    records = (
        [("long", Decimal("0.65"), "hit_target")] * 20
        + [("short", Decimal("0.65"), "hit_stop")] * 20
        + [("long", Decimal("0.25"), "hit_stop")] * 20
    )
    rates = compute_success_rates(records)
    assert rates[("long", Decimal("0.6"))][0] == Decimal("1")
    assert rates[("short", Decimal("0.6"))][0] == Decimal("0")
    assert rates[("long", Decimal("0.2"))][0] == Decimal("0")
