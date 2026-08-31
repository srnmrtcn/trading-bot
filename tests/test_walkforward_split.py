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
    mumlar = []
    for i in range(adet):
        open_time = baslangic + timedelta(hours=i)
        mumlar.append({
            'open_time': open_time,
            'open': close,
            'high': high,
            'low': low,
            'close': close,
            'volume': Decimal('1000')
        })
    return mumlar


def test_1_normal_senaryolar_puanlanir():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR - timedelta(hours=10), SINIR + timedelta(hours=10))
    d2 = _draft("ETH", SINIR - timedelta(hours=10), SINIR + timedelta(hours=10))
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None)
    assert len(result) == 1
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20)), ("ETH", SINIR, d2, _mumlar(SINIR, 20))], None, None)
    assert len(result) == 2


def test_2_egitim_sizintisi():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR - timedelta(hours=2), SINIR + timedelta(hours=10))
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None, until=SINIR)
    assert len(result) == 0


def test_3_until_sonuc_girer():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR - timedelta(hours=10), SINIR)
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None, until=SINIR)
    assert len(result) == 1


def test_4_until_sonuc_girer():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR - timedelta(hours=10), SINIR - timedelta(hours=1))
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None, until=SINIR)
    assert len(result) == 1


def test_5_start_after_sonuc_girmez():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR - timedelta(hours=10), SINIR + timedelta(hours=10))
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None, start_after=SINIR)
    assert len(result) == 0


def test_6_start_after_sonuc_girer():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR, SINIR + timedelta(hours=10))
    result = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None, start_after=SINIR)
    assert len(result) == 1


def test_7_egitim_test_orusme():
    SINIR = datetime(2026, 3, 1)
    d1 = _draft("BTC", SINIR - timedelta(hours=10), SINIR - timedelta(hours=1))
    d2 = _draft("ETH", SINIR, SINIR + timedelta(hours=10))
    result1 = score([("BTC", SINIR, d1, _mumlar(SINIR, 20))], None, None, until=SINIR)
    result2 = score([("ETH", SINIR, d2, _mumlar(SINIR, 20))], None, None, start_after=SINIR)
    assert len(result1) == 1
    assert len(result2) == 1
