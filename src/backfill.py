from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db.models import Kline
from src.integrity import detect_gaps
from src.kline_fetcher import fetch_and_store


def _to_epoch_ms(naive_utc_dt: datetime) -> int:
    return int(naive_utc_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def run_initial_backfill(session, binance_client, symbols: list, timeframes: list, since_days: int = 730) -> list:
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=since_days)
    results = []
    for symbol in symbols:
        for timeframe in timeframes:
            result = fetch_and_store(
                session, binance_client, symbol, timeframe,
                start_ms=_to_epoch_ms(start),
                end_ms=_to_epoch_ms(end),
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

    results = []
    for gap in gaps:
        result = fetch_and_store(
            session, binance_client, symbol, timeframe,
            start_ms=_to_epoch_ms(gap.start),
            end_ms=_to_epoch_ms(gap.end),
        )
        results.append(result)
    return results
