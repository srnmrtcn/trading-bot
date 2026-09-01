from datetime import date, datetime, timedelta
from decimal import Decimal
from src.portfolio.universe import daily_dollar_volume, lookback_return, median_value

def test_daily_dollar_volume_one_day():
    klines = [
        {'open_time': datetime(2026, 3, 1, 0), 'close': Decimal(10), 'volume': Decimal(2)},
        {'open_time': datetime(2026, 3, 1, 1), 'close': Decimal(20), 'volume': Decimal(3)}
    ]
    result = daily_dollar_volume(klines)
    expected = {date(2026, 3, 1): Decimal(80)}  # (10 * 2) + (20 * 3) = 20 + 60 = 80
    assert result == expected

def test_daily_dollar_volume_multiple_days():
    klines = [
        {'open_time': datetime(2026, 3, 1, 23), 'close': Decimal(10), 'volume': Decimal(1)},
        {'open_time': datetime(2026, 3, 2, 0), 'close': Decimal(10), 'volume': Decimal(2)},
        {'open_time': datetime(2026, 3, 2, 5), 'close': Decimal(10), 'volume': Decimal(3)}
    ]
    result = daily_dollar_volume(klines)
    expected = {date(2026, 3, 1): Decimal(10), date(2026, 3, 2): Decimal(50)}
    assert result == expected

def test_daily_dollar_volume_empty():
    result = daily_dollar_volume([])
    expected = {}
    assert result == expected

def test_median_value_odd_count():
    values = [Decimal(1), Decimal(3), Decimal(2)]
    result = median_value(values)
    expected = Decimal(2)
    assert result == expected

def test_median_value_even_count():
    values = [Decimal(1), Decimal(2), Decimal(3), Decimal(10)]
    result = median_value(values)
    expected = Decimal('2.5')
    assert result == expected

def test_median_value_empty():
    result = median_value([])
    expected = None
    assert result == expected

def test_median_value_single_element():
    values = [Decimal(7)]
    result = median_value(values)
    expected = Decimal(7)
    assert result == expected

def test_median_value_does_not_modify_input():
    girdi = [Decimal(3), Decimal(1), Decimal(2)]
    original = list(girdi)
    median_value(girdi)
    assert girdi == original

def test_lookback_return_normal():
    closes = {date(2026, 3, 1): Decimal(100), date(2026, 3, 15): Decimal(120)}
    result = lookback_return(closes, date(2026, 3, 15), 14)
    expected = Decimal('0.2')
    assert result == expected

def test_lookback_return_negative_return():
    closes = {date(2026, 3, 1): Decimal(100), date(2026, 3, 15): Decimal(80)}
    result = lookback_return(closes, date(2026, 3, 15), 14)
    expected = Decimal('-0.2')
    assert result == expected

def test_lookback_return_base_day_missing():
    closes = {date(2026, 3, 15): Decimal(120)}
    result = lookback_return(closes, date(2026, 3, 15), 14)
    assert result is None

def test_lookback_return_end_day_missing():
    closes = {date(2026, 3, 1): Decimal(100)}
    result = lookback_return(closes, date(2026, 3, 15), 14)
    assert result is None

def test_lookback_return_base_day_zero():
    closes = {date(2026, 3, 1): Decimal(0), date(2026, 3, 15): Decimal(120)}
    result = lookback_return(closes, date(2026, 3, 15), 14)
    assert result is None
