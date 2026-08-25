"""Pull a sample of liquid USDT pairs into a local SQLite for offline replay.

Deliberately a *sample*, not all 484 symbols: the funnel question is about
rates, and 25 symbols x 90 days is ~54k candle evaluations — far more than
enough to locate which gate closes. Uses only Binance's public API (no key)
and the production storage layer, so the stored rows are bit-identical to what
the live service writes.

    PYTHONPATH=. python3 scripts/fetch_research_data.py
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta

from src.binance_client import BinanceClient
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.kline_fetcher import process_symbol_timeframe
from src.timeutil import to_epoch_ms, utc_now

RESEARCH_DB_URL = "sqlite:///data/research.db"
TIMEFRAME = "1h"
DAYS = 90
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "LTCUSDT", "TRXUSDT", "ATOMUSDT", "UNIUSDT", "ETCUSDT",
    "XLMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT",
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # SQLite will not create the directory for us, and `data/` is gitignored,
    # so a fresh clone has none.
    os.makedirs("data", exist_ok=True)
    engine = make_engine(RESEARCH_DB_URL)
    create_all_tables(engine)
    session = make_session_factory(engine)()
    client = BinanceClient()

    end = utc_now()
    start = end - timedelta(days=DAYS)
    failed = []
    try:
        for symbol in SYMBOLS:
            result = process_symbol_timeframe(
                session, client, symbol, TIMEFRAME,
                start_ms=to_epoch_ms(start), end_ms=to_epoch_ms(end),
            )
            if result.error:
                logging.warning("%-10s FAILED: %s", symbol, result.error)
                failed.append(symbol)
            else:
                logging.info(
                    "%-10s %5d candles (%d new, %d flagged)",
                    symbol, result.fetched, result.inserted, result.flagged,
                )
    finally:
        session.close()

    logging.info("\nDone: %d ok, %d failed -> %s", len(SYMBOLS) - len(failed), len(failed), RESEARCH_DB_URL)
    # Non-zero when nothing landed: a chained analysis must not mistake an
    # empty or stale database for fresh data.
    return 1 if len(failed) == len(SYMBOLS) else 0


if __name__ == "__main__":
    sys.exit(main())
