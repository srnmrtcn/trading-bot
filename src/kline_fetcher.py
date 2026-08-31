from __future__ import annotations

from dataclasses import dataclass

from src.integrity import flag_anomalies
from src.storage import upsert_klines, get_last_close_before


@dataclass
class FetchResult:
    symbol: str
    timeframe: str
    fetched: int
    inserted: int
    updated: int
    flagged: int
    error: str = None


def fetch_and_store(session, binance_client, symbol: str, timeframe: str, start_ms: int, end_ms: int, market: str = "spot") -> FetchResult:
    try:
        if market == "spot":
            rows = binance_client.get_klines(symbol, timeframe, start_ms, end_ms)
        elif market == "futures":
            rows = binance_client.get_futures_klines(symbol, timeframe, start_ms, end_ms)
        else:
            raise ValueError(f"unknown market {market!r}")
        previous_close = None
        if rows and len(rows) > 0:
            # Get the close price before the first open_time
            previous_close = get_last_close_before(session, symbol, timeframe, rows[0]['open_time'])
        flagged_rows = flag_anomalies(rows, previous_close=previous_close)
        upsert_result = upsert_klines(session, symbol, timeframe, flagged_rows)
        flagged_count = sum(1 for row in flagged_rows if row["flagged"])
        return FetchResult(
            symbol=symbol, timeframe=timeframe, fetched=len(rows),
            inserted=upsert_result.inserted, updated=upsert_result.updated,
            flagged=flagged_count, error=None,
        )
    except Exception as exc:
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0, error=str(exc))


def process_symbol_timeframe(session, binance_client, symbol: str, timeframe: str, start_ms: int, end_ms: int, market: str = "spot") -> FetchResult:
    """Fetch-and-store one symbol/timeframe with session isolation.

    ``fetch_and_store`` may fail mid-flush/commit (e.g. a DB constraint
    violation in the storage layer), which leaves the SQLAlchemy session in a
    pending-rollback state where every later statement fails too. Rolling back
    here keeps one symbol's failure from poisoning the rest of the batch, so
    every caller inherits the same isolation policy.
    """
    result = fetch_and_store(session, binance_client, symbol, timeframe, start_ms, end_ms, market=market)
    if result.error:
        session.rollback()
    return result