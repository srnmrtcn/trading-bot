from datetime import datetime, timedelta
from decimal import Decimal

from src.btc_regime import REGIME_LOOKBACK, compute_btc_regime
from src.db.models import Kline
from src.indicators import compute_ema

# floor_to_timeframe("1d") zeroes out time-of-day, so NOW's boundary is
# midnight of the same calendar day.
NOW = datetime(2026, 8, 21, 5, 0)
DAY_BOUNDARY = datetime(2026, 8, 21)


def _daily_kline(open_time, close, flagged=False):
    return Kline(
        symbol="BTCUSDT", timeframe="1d", open_time=open_time,
        open=close, high=close, low=close, close=close, volume=Decimal("1000"),
        flagged=flagged,
    )


def _seed_daily_closes(db_session, closes, end_boundary=DAY_BOUNDARY, flag_index=None, drop_index=None):
    """`len(closes)` contiguous daily candles, the last one closing right
    before `end_boundary`."""
    rows = []
    for i, close in enumerate(closes):
        open_time = end_boundary - timedelta(days=len(closes) - i)
        rows.append(_daily_kline(open_time, close))
    if flag_index is not None:
        rows[flag_index].flagged = True
    if drop_index is not None:
        del rows[drop_index]
    for row in rows:
        db_session.add(row)
    db_session.commit()


def test_compute_btc_regime_returns_up_for_a_rising_series(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK)]
    _seed_daily_closes(db_session, closes)

    assert compute_btc_regime(db_session, now=NOW) == "up"


def test_compute_btc_regime_returns_down_for_a_falling_series(db_session):
    closes = [Decimal(200 - i) for i in range(REGIME_LOOKBACK)]
    _seed_daily_closes(db_session, closes)

    assert compute_btc_regime(db_session, now=NOW) == "down"


def test_regime_uses_eighty_closed_daily_candles():
    assert REGIME_LOOKBACK == 80


def test_twenty_two_candles_are_not_enough_for_regime(db_session):
    _seed_daily_closes(db_session, [Decimal(100 + i) for i in range(22)])
    assert compute_btc_regime(db_session, now=NOW) is None


def test_eighty_candle_ema21_converges_to_long_history():
    closes = [Decimal(index) for index in range(1, 201)]
    full = compute_ema(closes, 21)[-1]
    truncated = compute_ema(closes[-80:], 21)[-1]

    assert abs(truncated - full) / full < Decimal("0.005")


def test_compute_btc_regime_returns_none_with_insufficient_candles(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK - 1)]
    _seed_daily_closes(db_session, closes)

    assert compute_btc_regime(db_session, now=NOW) is None


def test_compute_btc_regime_returns_none_for_a_non_contiguous_window(db_session):
    # One extra candle before the drop so REGIME_LOOKBACK rows still remain
    # afterwards -- otherwise this would hit the "insufficient candles"
    # branch instead of the contiguity check it's meant to exercise.
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK + 1)]
    _seed_daily_closes(db_session, closes, drop_index=10)

    assert compute_btc_regime(db_session, now=NOW) is None


def test_compute_btc_regime_returns_none_for_stale_data(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK)]
    # The newest candle ends 3 days before the boundary instead of 1.
    _seed_daily_closes(db_session, closes, end_boundary=DAY_BOUNDARY - timedelta(days=2))

    assert compute_btc_regime(db_session, now=NOW) is None


def test_compute_btc_regime_returns_none_for_a_flagged_candle(db_session):
    closes = [Decimal(100 + i) for i in range(REGIME_LOOKBACK)]
    _seed_daily_closes(db_session, closes, flag_index=REGIME_LOOKBACK - 1)

    assert compute_btc_regime(db_session, now=NOW) is None
