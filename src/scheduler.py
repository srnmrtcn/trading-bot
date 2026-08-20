from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.backfill import run_gap_backfill
from src.db.models import Symbol
from src.fetch_log import record_run
from src.integrity import floor_to_timeframe
from src.kline_fetcher import process_symbol_timeframe
from src.storage import get_kline_time_bounds
from src.symbol_registry import refresh_symbols
from src.timeutil import to_epoch_ms, utc_now

logger = logging.getLogger("scheduler")

DEFAULT_BACKFILL_DAYS = 730
GAP_LOOKBACK_DAYS = 30


def get_resume_point(session, symbol: str, timeframe: str, now: datetime = None) -> datetime:
    """Where the next fetch for this symbol/timeframe should start.

    Derived from the stored data (``MAX(klines.open_time)``), never from
    ``fetch_log``: a wall-clock ``finished_at`` is captured *after* the fetch,
    so using it would skip every candle between the fetched window's end and
    the moment the run finished, and would never refresh the partially-formed
    candle that was stored mid-formation.

    The last stored candle is deliberately re-fetched so its final values
    overwrite that stub; ``upsert_klines`` makes this idempotent.
    """
    now = now if now is not None else utc_now()
    _, latest = get_kline_time_bounds(session, symbol, timeframe)
    if latest is not None:
        return latest
    return now - timedelta(days=DEFAULT_BACKFILL_DAYS)


def repair_recent_gaps(session, binance_client, symbol: str, timeframe: str, now: datetime = None) -> int:
    """Re-fetch missing candles in the recent window. Returns the gaps filled."""
    now = now if now is not None else utc_now()
    earliest, _ = get_kline_time_bounds(session, symbol, timeframe)
    if earliest is None:
        # Nothing stored yet: there is no gap to repair, only history to fetch.
        return 0

    window_end = floor_to_timeframe(now, timeframe)
    # Never look further back than the symbol's first candle — the absence of
    # data before a coin was listed is not a gap.
    window_start = max(
        floor_to_timeframe(now - timedelta(days=GAP_LOOKBACK_DAYS), timeframe),
        earliest,
    )
    if window_start > window_end:
        return 0

    results = run_gap_backfill(session, binance_client, symbol, timeframe, window_start, window_end)
    return sum(1 for result in results if not result.error)


def run_timeframe_job(session_factory, binance_client, timeframe: str, now: datetime = None) -> None:
    session = session_factory()
    try:
        end = now if now is not None else utc_now()
        symbols = [row.symbol for row in session.query(Symbol).filter(Symbol.is_active == True).all()]  # noqa: E712
        succeeded = 0
        failed = 0
        gaps_filled = 0
        for symbol in symbols:
            try:
                start = get_resume_point(session, symbol, timeframe, now=end)
                started_at = utc_now()
                result = process_symbol_timeframe(
                    session, binance_client, symbol, timeframe,
                    start_ms=to_epoch_ms(start),
                    end_ms=to_epoch_ms(end),
                )
                if result.error:
                    failed += 1
                    logger.error("Fetch failed for %s %s: %s", symbol, timeframe, result.error)
                else:
                    succeeded += 1
                record_run(
                    session, symbol, timeframe,
                    status="error" if result.error else "success",
                    started_at=started_at, finished_at=utc_now(),
                    error_message=result.error,
                )
                if not result.error:
                    # Skip gap repair when the plain fetch just failed: the
                    # next run retries both, and there is no point hammering an
                    # unreachable API twice per symbol.
                    gaps_filled += repair_recent_gaps(session, binance_client, symbol, timeframe, now=end)
            except Exception:
                # A single symbol's failure (including a failure in
                # record_run or gap repair itself) must never abort processing
                # of the remaining symbols. Log it here because such a failure
                # may happen before any fetch_log row could be written, which
                # would otherwise make it completely invisible.
                failed += 1
                logger.exception("Unhandled error processing %s %s", symbol, timeframe)
                session.rollback()
        logger.info(
            "%s job finished: %d symbols succeeded, %d failed, %d gaps filled",
            timeframe, succeeded, failed, gaps_filled,
        )
    finally:
        session.close()


def run_symbol_refresh_job(session_factory, binance_client) -> None:
    session = session_factory()
    try:
        result = refresh_symbols(session, binance_client)
        logger.info(
            "Symbol refresh finished: %d active, %d deactivated",
            result.active_count, result.deactivated_count,
        )
    except Exception:
        # A failed refresh must not kill the scheduler thread; the job runs
        # again tomorrow.
        logger.exception("Symbol refresh failed")
        session.rollback()
    finally:
        session.close()


def build_scheduler(session_factory, binance_client) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: run_timeframe_job(session_factory, binance_client, "1h"),
        CronTrigger(minute=5),
        id="hourly_klines",
    )
    scheduler.add_job(
        lambda: run_timeframe_job(session_factory, binance_client, "1d"),
        CronTrigger(hour=0, minute=10),
        id="daily_klines",
    )
    scheduler.add_job(
        lambda: run_symbol_refresh_job(session_factory, binance_client),
        CronTrigger(hour=0, minute=0),
        id="symbol_refresh",
    )
    return scheduler
