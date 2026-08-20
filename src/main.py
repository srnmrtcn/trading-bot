from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler

from src.backfill import run_initial_backfill
from src.binance_client import BinanceClient
from src.config import get_database_url
from src.db.models import Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.scheduler import build_scheduler
from src.storage import get_kline_time_bounds
from src.symbol_registry import refresh_symbols

logger = logging.getLogger("main")

TIMEFRAMES = ["1h", "1d"]
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")


def configure_logging(log_file: str = None) -> None:
    """Log to console and to a rotating file, as the design spec requires."""
    log_file = log_file or LOG_FILE
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [console_handler, file_handler]


def find_pending_backfills(session, symbols: list, timeframes: list) -> list:
    """Symbol/timeframe pairs with no stored klines at all.

    Checked per pair rather than globally, so a crash partway through the
    initial backfill does not leave the remaining symbols without their
    history on restart.
    """
    pending = []
    for symbol in symbols:
        for timeframe in timeframes:
            _, latest = get_kline_time_bounds(session, symbol, timeframe)
            if latest is None:
                pending.append((symbol, timeframe))
    return pending


def startup():
    database_url = get_database_url()
    engine = make_engine(database_url)
    create_all_tables(engine)
    session_factory = make_session_factory(engine)
    binance_client = BinanceClient()

    session = session_factory()
    try:
        try:
            refresh_symbols(session, binance_client)
        except Exception:
            # A transient boot failure (DNS, Binance maintenance) must not kill
            # an unattended service — the daily symbol_refresh job retries it.
            logger.exception("Symbol refresh failed at startup, continuing with known symbols")
            session.rollback()

        symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
        pending = find_pending_backfills(session, symbols, TIMEFRAMES)
        if pending:
            logger.info("Running initial backfill for %d symbol/timeframe pairs", len(pending))
            results = []
            for symbol, timeframe in pending:
                results.extend(run_initial_backfill(session, binance_client, [symbol], [timeframe]))
            failed = [result for result in results if result.error]
            logger.info(
                "Initial backfill finished: %d succeeded, %d failed",
                len(results) - len(failed), len(failed),
            )
            for result in failed:
                logger.error("Initial backfill failed for %s %s: %s", result.symbol, result.timeframe, result.error)
        else:
            logger.info("All active symbols already have kline data, skipping initial backfill")
    finally:
        session.close()

    return session_factory, binance_client


def run_forever(session_factory, binance_client) -> None:
    scheduler = build_scheduler(session_factory, binance_client)
    scheduler.start()
    logger.info("Scheduler started, service running")
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


def main() -> None:
    configure_logging()
    session_factory, binance_client = startup()
    run_forever(session_factory, binance_client)


if __name__ == "__main__":
    main()
