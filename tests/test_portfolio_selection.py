from datetime import date, datetime, timedelta
from decimal import Decimal

from src.portfolio.selection import book_for, build_scores, closes_by_day, is_liquid


def _bars(days, drift, dollar=Decimal(10000000)):
    out = []
    price = Decimal(100)
    for i in range(days):
        out.append({'open_time': datetime(2026, 1, 1) + timedelta(days=i),
                    'close': price, 'volume': dollar / price})
        price = price * (Decimal(1) + Decimal(str(drift)))
    return out


AS_OF = date(2026, 2, 9)   # 2026-01-01 + 39 gun, yani 40 mumun sonuncusu


def test_closes_by_day():
    bars = _bars(3, 0)
    result = closes_by_day(bars)
    expected = {date(2026,1,1): Decimal(100), date(2026,1,2): Decimal(100), date(2026,1,3): Decimal(100)}
    assert result == expected


def test_closes_by_day_empty():
    assert closes_by_day([]) == {}


def test_is_liquid_above_floor():
    assert is_liquid(_bars(40, 0), AS_OF, 30, Decimal(5000000)) is True


def test_is_liquid_below_floor():
    assert is_liquid(_bars(40, 0, Decimal(1000000)), AS_OF, 30, Decimal(5000000)) is False


def test_is_liquid_less_than_half_window():
    bars = _bars(40, 0)
    assert is_liquid(bars[-10:], AS_OF, 30, Decimal(5000000)) is False


def test_build_scores_high_score_first():
    veri = {'A': _bars(40, 0.010), 'B': _bars(40, 0.008), 'C': _bars(40, 0.006)}
    scores = build_scores(veri, AS_OF, (7, 14), 1)
    assert scores['A'] > scores['B'] > scores['C']
    assert len(scores) == 3
    assert 'A' in scores and 'B' in scores and 'C' in scores


def test_build_scores_short_history_skipped():
    veri = {'A': _bars(40, 0.010), 'KISA': _bars(40, 0.01)[-5:]}
    scores = build_scores(veri, AS_OF, (7, 14), 1)
    assert 'KISA' not in scores
    assert 'A' in scores


def test_book_for_top_and_bottom_selected():
    veri = {'A': _bars(40,0.010), 'B': _bars(40,0.008), 'C': _bars(40,0.006), 'D': _bars(40,0.004), 'E': _bars(40,0.002), 'F': _bars(40,0.000)}
    longs, shorts, prices = book_for(veri, AS_OF, (7,14), 1, Decimal('0.34'), 30, Decimal(5000000), 4)
    assert longs == ['A','B']
    assert shorts == ['F','E']


def test_book_for_illiquid_symbol_not_selected():
    veri = {'A': _bars(40,0.010), 'B': _bars(40,0.008), 'C': _bars(40,0.006), 'D': _bars(40,0.004), 'E': _bars(40,0.002), 'F': _bars(40,0.000)}
    veri['ILLIQ'] = _bars(40, 0.02, Decimal(1000))
    longs, shorts, prices = book_for(veri, AS_OF, (7,14), 1, Decimal('0.34'), 30, Decimal(5000000), 4)
    assert longs == ['A','B']
    assert 'ILLIQ' not in longs
    assert 'ILLIQ' not in shorts


def test_book_for_prices_correct():
    veri = {'A': _bars(40,0.010), 'B': _bars(40,0.008), 'C': _bars(40,0.006), 'D': _bars(40,0.004)}
    longs, shorts, prices = book_for(veri, AS_OF, (7,14), 1, Decimal('0.34'), 30, Decimal(5000000), 4)
    assert len(prices) == 4
    for symbol in longs + shorts:
        assert symbol in prices
        assert prices[symbol] == _bars(40, 0.010)[39]['close']


def test_book_for_small_universe_returns_empty():
    veri = {'A': _bars(40,0.010), 'B': _bars(40,0.008), 'C': _bars(40,0.006), 'D': _bars(40,0.004)}
    longs, shorts, prices = book_for(veri, AS_OF, (7,14), 1, Decimal('0.34'), 30, Decimal(5000000), 10)
    assert longs == []
    assert shorts == []
    assert prices == {}
