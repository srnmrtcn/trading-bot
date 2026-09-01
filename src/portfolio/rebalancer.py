from __future__ import annotations

from datetime import datetime, timedelta

from src.db.models import Kline, PortfolioSnapshot
from src.integrity import floor_to_timeframe
from src.portfolio.config import REBALANCE_DAYS, STRATEGY_VERSION


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
        session.query(Kline)
        .filter(
            Kline.timeframe == "1d",
            Kline.open_time < boundary,
            Kline.open_time >= boundary - timedelta(days=days),
        )
        .order_by(Kline.symbol.asc(), Kline.open_time.asc())
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
