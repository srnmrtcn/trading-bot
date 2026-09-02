from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from src.db.models import Kline
from src.integrity import TIMEFRAME_DELTAS, detect_gaps
from src.kline_fetcher import process_symbol_timeframe
from src.timeutil import DEFAULT_BACKFILL_DAYS, to_epoch_ms, utc_now

logger = logging.getLogger("backfill")

# Most gaps close on the first attempt. The ones that do not are permanent:
# Binance genuinely has no candles for an illiquid pair's quiet hour or for an
# exchange halt, so the fetch succeeds, returns nothing, and the same gap is
# detected again on the next run -- forever. Uncapped, each of those costs one
# API call per symbol per run for the life of the service, and the hourly job
# already walks close to five hundred symbols.
#
# The cap bounds that. Real gaps still close, one run at a time; a symbol with
# more than this many is reported rather than silently absorbed.
MAX_GAPS_PER_RUN = 5


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


def _window_is_complete(session, symbol: str, timeframe: str, range_start: datetime, range_end: datetime) -> bool:
    """True when the window holds every candle it should, decided by a COUNT.

    This runs 489 times an hour and almost always answers "complete": the
    measured 1h job spends 348 seconds -- a quarter of its wall clock -- on gap
    detection that fills nothing, because filling nothing is the correct
    outcome on a healthy database. Loading 720 timestamps per symbol into
    Python to conclude that is the expensive way to ask a cheap question.

    Sound, not just fast. Stored open_times are the ones Binance returns, which
    sit on the timeframe grid, and range_start is floored to that same grid, so
    every row inside the window is one of the expected points. A unique
    constraint on (symbol, timeframe, open_time) means none of them repeats.
    A count equal to the number of grid points therefore leaves no room for a
    missing one.

    The comparison is >= rather than == on purpose: a count somehow ABOVE the
    expected number would mean off-grid rows, which is a data-integrity problem
    and not something a gap repair can fix -- and treating it as "incomplete"
    would put this symbol into a full scan every hour forever.
    """
    step = TIMEFRAME_DELTAS[timeframe]
    expected = int((range_end - range_start) / step) + 1
    stored = (
        session.query(func.count(Kline.open_time))
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe,
                Kline.open_time >= range_start, Kline.open_time <= range_end)
        .scalar()
    ) or 0
    return stored >= expected


def run_gap_backfill(session, binance_client, symbol: str, timeframe: str, range_start: datetime, range_end: datetime, max_gaps: int = MAX_GAPS_PER_RUN) -> list:
    if _window_is_complete(session, symbol, timeframe, range_start, range_end):
        return []

    existing_times = [
        row.open_time for row in
        session.query(Kline.open_time)
        .filter(Kline.symbol == symbol, Kline.timeframe == timeframe,
                Kline.open_time >= range_start, Kline.open_time <= range_end)
        .all()
    ]
    gaps = detect_gaps(existing_times, timeframe, range_start, range_end)

    if len(gaps) > max_gaps:
        # Newest first, not oldest. Either order can starve the other end when a
        # gap is unfillable, and recent candles are the ones every signal reads;
        # an ancient hole must never be allowed to block this week's data.
        # The warning is the part that matters: a symbol that keeps reporting
        # skipped gaps is telling us its permanent gaps need recording as
        # known-empty, which this cap deliberately does not attempt.
        skipped = len(gaps) - max_gaps
        gaps = sorted(gaps, key=lambda gap: gap.start, reverse=True)[:max_gaps]
        logger.warning(
            "%s %s has more gaps than one run repairs: filling the %d newest, "
            "%d left for later runs",
            symbol, timeframe, max_gaps, skipped,
        )

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
