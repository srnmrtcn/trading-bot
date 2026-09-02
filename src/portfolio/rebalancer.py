from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass
from decimal import Decimal

from src.db.models import FuturesDailyKline, PortfolioSnapshot
from src.integrity import floor_to_timeframe
from src.portfolio.config import REBALANCE_DAYS, STRATEGY_VERSION
from src.funding_collector import funding_events_between
from src.portfolio.accounting import position_sizes
from src.portfolio.book import close_position, open_positions, portfolio_equity, record_open
from src.portfolio.config import (
    LEG_EXPOSURE,
    LIQUIDITY_WINDOW_DAYS,
    LOOKBACK_DAYS,
    MIN_DOLLAR_VOLUME,
    MIN_UNIVERSE,
    SIGNAL_SKIP_DAYS,
    TOP_FRACTION,
)
from src.portfolio.selection import book_for, closes_by_day
from src.timeutil import utc_now


def is_rebalance_due(session, now: datetime) -> bool:
    """
    Returns True if a rebalance is due based on the last snapshot and current time.
    """
    last = (
        session.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_version == STRATEGY_VERSION)
        .order_by(PortfolioSnapshot.as_of.desc(), PortfolioSnapshot.id.desc())
        .first()
    )
    if last is None:
        return True
    return now - last.as_of >= timedelta(days=REBALANCE_DAYS)


def load_daily_bars(session, now: datetime, days: int) -> dict:
    """
    Loads daily bars for symbols within the specified date range.
    """
    boundary = floor_to_timeframe(now, "1d")
    rows = (
        session.query(FuturesDailyKline)
        .filter(
            FuturesDailyKline.open_time < boundary,
            FuturesDailyKline.open_time >= boundary - timedelta(days=days),
        )
        .order_by(FuturesDailyKline.symbol.asc(), FuturesDailyKline.open_time.asc())
        .all()
    )
    bars = {}
    for row in rows:
        bars.setdefault(row.symbol, []).append(
            {"open_time": row.open_time, "close": row.close, "volume": row.volume}
        )
    return bars


def record_snapshot(session, as_of: datetime, equity, closed: int, opened: int):
    """
    Records a portfolio snapshot to the database.
    """
    snapshot = PortfolioSnapshot(
        strategy_version=STRATEGY_VERSION, as_of=as_of, equity=equity,
        positions_closed=closed, positions_opened=opened,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


@dataclass
class RebalanceResult:
    acted: bool
    closed: int
    opened: int
    equity: Decimal
    universe: int
    # Why the run ended the way it did. acted=False has two causes that look
    # identical from outside and could not be more different: "not_due" is the
    # normal answer on six days out of seven, while "no_book" means the run WAS
    # due and could not act. The second is a silent outage -- the book simply
    # never opens -- so the caller has to be able to tell them apart in order
    # to keep quiet about one and complain about the other.
    reason: str = "ok"
    # How many symbols had daily bars at all. Separates "the bars never
    # arrived" (0) from "the bars are here but too few names clear the
    # liquidity floor" (many, with universe 0).
    symbols: int = 0


def run_rebalance(session, now: datetime = None) -> RebalanceResult:
    """
    Executes a rebalance operation, closing existing positions and opening new ones.
    """
    now = now if now is not None else utc_now()
    equity = portfolio_equity(session)
    if not is_rebalance_due(session, now):
        return RebalanceResult(False, 0, 0, equity, 0, "not_due")

    history = LIQUIDITY_WINDOW_DAYS + max(LOOKBACK_DAYS) + SIGNAL_SKIP_DAYS + 2
    bars = load_daily_bars(session, now, history)
    closes = {symbol: closes_by_day(rows) for symbol, rows in bars.items()}

    closed = 0
    for position in open_positions(session):
        day_closes = closes.get(position.symbol) or {}
        if not day_closes:
            continue
        events = funding_events_between(session, position.symbol, position.opened_at, now)
        equity += close_position(session, position, day_closes[max(day_closes)], events, now)
        closed += 1

    as_of = (floor_to_timeframe(now, "1d") - timedelta(days=1)).date()
    opened = 0
    universe = 0
    if equity > 0:
        longs, shorts, prices = book_for(
            bars, as_of, LOOKBACK_DAYS, SIGNAL_SKIP_DAYS, TOP_FRACTION,
            LIQUIDITY_WINDOW_DAYS, MIN_DOLLAR_VOLUME, MIN_UNIVERSE,
        )
        universe = len(longs) + len(shorts)
        for direction, names in (("long", longs), ("short", shorts)):
            for symbol, size in position_sizes(equity, names, prices, LEG_EXPOSURE).items():
                record_open(session, symbol, direction, prices[symbol], size, now)
                opened += 1

    if closed == 0 and opened == 0:
        # Nothing happened, so the week is NOT spent. Recording a snapshot here
        # would set the clock and block the next attempt for a full
        # REBALANCE_DAYS -- which is exactly the wrong response to the reason
        # this branch is usually reached: the daily bars did not arrive, so
        # there was no universe to rank. That is a data outage lasting minutes,
        # and it would have cost a week of trading. A book that legitimately
        # has too few eligible names simply retries tomorrow, which is cheap.
        return RebalanceResult(False, 0, 0, equity, universe, "no_book",
                              len(bars))

    record_snapshot(session, now, equity, closed, opened)
    return RebalanceResult(True, closed, opened, equity, universe, "ok",
                           len(bars))