from datetime import datetime, timedelta
from decimal import Decimal

from scripts.run_walkforward import score
from src.scenario_builder import ScenarioDraft


def _draft(symbol, created_at, expires_at, direction='long', entry=Decimal('100'), stop=Decimal('90'), target=Decimal('110')):
    return ScenarioDraft(
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        target_price=target,
        stop_price=stop,
        expected_return_pct=Decimal('0.1'),
        confidence_score=Decimal('0.8'),
        created_at=created_at,
        expires_at=expires_at
    )


def _mumlar(baslangic, adet, high=Decimal('115'), low=Decimal('95'), close=Decimal('105')):
    step = timedelta(hours=1)
    return [
        {
            'open_time': baslangic + i * step,
            'open': close,
            'high': high,
            'low': low,
            'close': close,
            'volume': Decimal('1000')
        }
        for i in range(adet)
    ]


def _kayit(d):
    saat = int((d.expires_at - d.created_at).total_seconds() // 3600)
    return (d.symbol, d.created_at, d, _mumlar(d.created_at, saat + 3))


def test_1_until_none_and_start_after_none_all_scenarios_scored():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft('BTC', SINIR - timedelta(hours=2), SINIR + timedelta(hours=10))
    d2 = _draft('ETH', SINIR - timedelta(hours=2), SINIR + timedelta(hours=10))
    result = score([_kayit(d1), _kayit(d2)], None, None)
    assert len(result) == 2


def test_2_training_leakage_scenario_not_scored():
    SINIR = datetime(2026, 3, 1)
    d = _draft('BTC', SINIR - timedelta(hours=2), SINIR + timedelta(hours=10))
    result = score([_kayit(d)], None, None, until=SINIR)
    assert len(result) == 0


def test_3_expires_at_equals_until_scenario_scored():
    SINIR = datetime(2026, 3, 1)
    d = _draft('BTC', SINIR - timedelta(hours=2), SINIR)
    result = score([_kayit(d)], None, None, until=SINIR)
    assert len(result) == 1


def test_4_expires_at_one_hour_before_until_scenario_scored():
    SINIR = datetime(2026, 3, 1)
    d = _draft('BTC', SINIR - timedelta(hours=2), SINIR - timedelta(hours=1))
    result = score([_kayit(d)], None, None, until=SINIR)
    assert len(result) == 1


def test_5_start_after_equals_until_scenario_not_scored():
    SINIR = datetime(2026, 3, 1)
    d = _draft('BTC', SINIR - timedelta(hours=2), SINIR + timedelta(hours=10))
    result = score([_kayit(d)], None, None, start_after=SINIR)
    assert len(result) == 0


def test_6_start_after_equals_created_at_scenario_scored():
    SINIR = datetime(2026, 3, 1)
    d = _draft('BTC', SINIR, SINIR + timedelta(hours=10))
    result = score([_kayit(d)], None, None, start_after=SINIR)
    assert len(result) == 1


def test_7_training_and_test_periods_do_not_overlap():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft('BTC', SINIR - timedelta(hours=12), SINIR - timedelta(hours=2))
    d2 = _draft('ETH', SINIR + timedelta(hours=2), SINIR + timedelta(hours=12))
    result = score([_kayit(d1), _kayit(d2)], None, None, until=SINIR, start_after=SINIR)
    assert len(result) == 2
