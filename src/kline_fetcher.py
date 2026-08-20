from __future__ import annotations

from dataclasses import dataclass

from src.integrity import flag_anomalies
from src.storage import upsert_klines


@dataclass
class FetchResult:
    symbol: str
    timeframe: str
    fetched: int
    inserted: int
    updated: int
    flagged: int
    error: str = None


def fetch_and_store(session, binance_client, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> FetchResult:
    try:
        rows = binance_client.get_klines(symbol, timeframe, start_ms, end_ms)
        flagged_rows = flag_anomalies(rows)
        upsert_result = upsert_klines(session, symbol, timeframe, flagged_rows)
        flagged_count = sum(1 for row in flagged_rows if row["flagged"])
        return FetchResult(
            symbol=symbol, timeframe=timeframe, fetched=len(rows),
            inserted=upsert_result.inserted, updated=upsert_result.updated,
            flagged=flagged_count, error=None,
        )
    except Exception as exc:
        return FetchResult(symbol=symbol, timeframe=timeframe, fetched=0, inserted=0, updated=0, flagged=0, error=str(exc))
