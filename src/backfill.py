from __future__ import annotations

from datetime import datetime, timedelta

from src.db.models import Kline
from src.integrity import TIMEFRAME_DELTAS, detect_gaps
from src.kline_fetcher import process_symbol_timeframe
from src.timeutil import DEFAULT_BACKFILL_DAYS, to_epoch_ms, utc_now


def run_initial_backfill(session, binance_client, symbols: list, timeframes: list, since_days: int = DEFAULT_BACKFILL_DAYS) -> list:
    end = utc_now()
    start = end - timedelta(days=since_days)
    results = []
    for symbol in symbols:
        for timeframe in timeframes:
            # process_symbol_timeframe rolls the session back on failure, so a
            # single symbol's DB error cannot abort the rest of the batch.
            result = process_symbol_timeframe(
                session, binance_client, symbol, timeframe,
                start_ms=to_epoch_ms(start),
                end_ms=to_epoch_ms(end),
            )
            results.append(result)
    return results


def run_gap_backfill(session, binance_client, symbol: str, timeframe: str, range_start: datetime, range_end: datetime) -> list:
    existing_times = [
        row.open_time for row in
        session.query(Kline.open_time)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe,
                Kline.open_time >= range_start, Kline.open_time <= range_end)
        .all()
    ]
    gaps = detect_gaps(existing_times, timeframe, range_start, range_end)

    step = TIMEFRAME_DELTAS[timeframe]
    results = []
    for gap in gaps:
        # `gap.end` is the open_time of the last missing candle, and
        # BinanceClient's pagination loop runs while `cursor < end_ms`. Step one
        # timeframe past the gap so its final candle is actually fetched — a
        # single-candle gap (start == end) would otherwise fetch nothing.
        result = process_symbol_timeframe(
            session, binance_client, symbol, timeframe,
            start_ms=to_epoch_ms(gap.start),
            end_ms=to_epoch_ms(gap.end + step),
        )
        results.append(result)
    return results
