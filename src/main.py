from __future__ import annotations

import logging
import time

from src.backfill import run_initial_backfill
from src.binance_client import BinanceClient
from src.config import get_database_url
from src.db.models import Kline, Symbol
from src.db.session import create_all_tables, make_engine, make_session_factory
from src.scheduler import build_scheduler
from src.symbol_registry import refresh_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

TIMEFRAMES = ["1h", "1d"]


def startup():
    database_url = get_database_url()
    engine = make_engine(database_url)
    create_all_tables(engine)
    session_factory = make_session_factory(engine)
    binance_client = BinanceClient()

    session = session_factory()
    try:
        refresh_symbols(session, binance_client)
        has_data = session.query(Kline).first() is not None
        if not has_data:
            logger.info("No existing kline data found, running initial backfill")
            symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
            run_initial_backfill(session, binance_client, symbols, TIMEFRAMES)
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
    session_factory, binance_client = startup()
    run_forever(session_factory, binance_client)


if __name__ == "__main__":
    main()
