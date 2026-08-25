from __future__ import annotations

from datetime import datetime

from src.db.models import Kline
from src.indicators import compute_ema
from src.integrity import TIMEFRAME_DELTAS, floor_to_timeframe
from src.timeutil import utc_now

BTC_SYMBOL = "BTCUSDT"
REGIME_TIMEFRAME = "1d"
EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
# One more than EMA_SLOW_PERIOD so compute_ema produces a real EMA21 step
# (not just the seed SMA) at the last index.
REGIME_LOOKBACK = EMA_SLOW_PERIOD + 1


def _load_closed_btc_klines(session, before: datetime) -> list:
    rows = (
        session.query(Kline)
        .filter(
            Kline.symbol == BTC_SYMBOL,
            Kline.timeframe == REGIME_TIMEFRAME,
            Kline.open_time < before,
        )
        .order_by(Kline.open_time.desc())
        .limit(REGIME_LOOKBACK)
        .all()
    )
    rows.reverse()
    return rows


def compute_btc_regime(session, now: datetime = None):
    """BTC's 1d trend direction: "up" (EMA9 > EMA21), "down", or None if it
    can't be determined (insufficient, non-contiguous, stale, or
    anomaly-flagged data). None must block every symbol's scenario
    generation that run, not just BTC's — see the design spec.
    """
    now = now if now is not None else utc_now()
    current_boundary = floor_to_timeframe(now, REGIME_TIMEFRAME)
    klines = _load_closed_btc_klines(session, current_boundary)

    if len(klines) < REGIME_LOOKBACK:
        return None

    step = TIMEFRAME_DELTAS[REGIME_TIMEFRAME]
    newest = klines[-1].open_time
    if newest < current_boundary - step:
        return None
    if newest - klines[0].open_time != (len(klines) - 1) * step:
        return None
    if any(row.flagged for row in klines):
        return None

    closes = [row.close for row in klines]
    ema_fast = compute_ema(closes, EMA_FAST_PERIOD)
    ema_slow = compute_ema(closes, EMA_SLOW_PERIOD)
    if ema_fast[-1] is None or ema_slow[-1] is None:
        return None

    return "up" if ema_fast[-1] > ema_slow[-1] else "down"
