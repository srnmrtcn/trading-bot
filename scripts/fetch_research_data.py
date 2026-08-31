"""Pull a liquidity-ranked slice of USDT pairs into a local SQLite for replay.

    PYTHONPATH=. python3 scripts/fetch_research_data.py
    PYTHONPATH=. python3 scripts/fetch_research_data.py --symbols 150 --days 730

Uses only Binance's public API (no key) and the production storage layer, so
the stored rows are bit-identical to what the live service writes.

SURVIVORSHIP BIAS — read before trusting any number derived from this data.
The universe is ranked by *today's* 24h quote volume, so it is a sample of the
pairs that are liquid now. Coins that were listed, traded badly and were
delisted inside the window are absent, and coins that pumped into the top of
the list are over-represented. Binance's public API exposes no point-in-time
universe, so this is not fixable here — it means measured expectancy is
optimistic, and a rule that only just breaks even on this sample is losing on
the real one.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta

from binance.client import Client

from src.binance_client import REQUEST_TIMEOUT_SECONDS, BinanceClient
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.kline_fetcher import process_symbol_timeframe
from src.timeutil import to_epoch_ms, utc_now

RESEARCH_DB_URL = "sqlite:///data/research.db"
TIMEFRAME = "1h"

# The BTC regime reads 22 daily candles, and the earliest evaluable 1h window
# sits ~4 days into the sample, so the daily series has to start well before
# the hourly one.
REGIME_SYMBOL = "BTCUSDT"
REGIME_TIMEFRAME = "1d"
REGIME_PADDING_DAYS = 60

STABLECOIN_BASES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDD", "EUR", "AEUR"}

def most_liquid_usdt_symbols(limit: int) -> list:
    """Active USDT spot pairs, most-traded first. See the module docstring."""
    client = Client(api_key="", api_secret="", requests_params={"timeout": REQUEST_TIMEOUT_SECONDS})
    tradable = {
        entry["symbol"] for entry in client.get_exchange_info()["symbols"]
        if entry["status"] == "TRADING" and entry["quoteAsset"] == "USDT"
    }
    ranked = sorted(
        (t for t in client.get_ticker() if t["symbol"] in tradable),
        key=lambda t: float(t["quoteVolume"]), reverse=True,
    )
    return [t["symbol"] for t in ranked[:limit]]


def most_liquid_futures_symbols(exchange_info: dict, tickers: list, limit: int) -> list:
    """Active USDT perpetual futures pairs, most-traded first.

    SAF function - no network calls, only processes given data.
    """
    raise NotImplementedError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=int, default=150)
    parser.add_argument("--days", type=int, default=730)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # SQLite will not create the directory for us, and `data/` is gitignored,
    # so a fresh clone has none.
    os.makedirs("data", exist_ok=True)
    engine = make_engine(RESEARCH_DB_URL)
    create_all_tables(engine)
    session = make_session_factory(engine)()
    client = BinanceClient()

    end = utc_now()
    start = end - timedelta(days=args.days)
    symbols = most_liquid_usdt_symbols(args.symbols)
    logging.info("%d symbols, %d days -> %s\n", len(symbols), args.days, RESEARCH_DB_URL)

    failed = []
    try:
        for index, symbol in enumerate(symbols, 1):
            result = process_symbol_timeframe(
                session, client, symbol, TIMEFRAME,
                start_ms=to_epoch_ms(start), end_ms=to_epoch_ms(end),
            )
            if result.error:
                logging.warning("[%3d/%d] %-12s FAILED: %s", index, len(symbols), symbol, result.error)
                failed.append(symbol)
            else:
                logging.info("[%3d/%d] %-12s %6d candles (%d new)",
                             index, len(symbols), symbol, result.fetched, result.inserted)

        regime = process_symbol_timeframe(
            session, client, REGIME_SYMBOL, REGIME_TIMEFRAME,
            start_ms=to_epoch_ms(start - timedelta(days=REGIME_PADDING_DAYS)), end_ms=to_epoch_ms(end),
        )
        if regime.error:
            logging.warning("%-12s %s FAILED: %s", REGIME_SYMBOL, REGIME_TIMEFRAME, regime.error)
            failed.append(REGIME_SYMBOL + REGIME_TIMEFRAME)
        else:
            logging.info("\n%-12s %6d daily candles (regime source)", REGIME_SYMBOL, regime.fetched)
    finally:
        session.close()

    logging.info("Done: %d ok, %d failed", len(symbols) - len(failed), len(failed))
    # Non-zero when nothing landed: a chained analysis must not mistake an
    # empty or stale database for fresh data.
    return 1 if len(failed) == len(symbols) else 0


if __name__ == "__main__":
    sys.exit(main())
